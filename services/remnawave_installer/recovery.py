import copy
import ipaddress
import json
import re
import shlex

from .haproxy import HAPROXY_CONTAINER, build_haproxy_config
from .panel import InstallerPanel, _host_identity_matches, _uuid
from .profiles import build_profile, reality_public_key
from .remote import ROOT, RemoteServer
from .types import TCP_REALITY_TRANSPORTS, InstallError, InstallSpec


def _refuse(message: str) -> None:
    """Останавливает восстановление при неоднозначных данных."""
    raise InstallError("recovery", message, retryable=False)


async def _remote_state(remote: RemoteServer, spec: InstallSpec, previous: dict | None) -> dict:
    """Читает согласованные метки и управляемые файлы сервера."""
    haproxy = spec.role == "haproxy_entry"
    name = HAPROXY_CONTAINER if haproxy else "remnanode"
    required = ["haproxy/compose.json", "haproxy/haproxy.cfg"] if haproxy else ["node/compose.json"]
    script = (
        "import hashlib,json,pathlib,re,subprocess,sys\n"
        "root=pathlib.Path(sys.argv[1]); name=sys.argv[2]; names=json.loads(sys.argv[3])\n"
        "def read(path):\n"
        " if any(p.is_symlink() for p in [path,*path.parents]) or not path.is_file(): raise SystemExit(31)\n"
        " if path.stat().st_size>2000000: raise SystemExit(32)\n"
        " return path.read_text()\n"
        "owner=read(root/'.owner').strip()\n"
        "if not re.fullmatch('[a-f0-9]{64}',owner): raise SystemExit(33)\n"
        "managed=json.loads(read(root/'.managed-files.json')); files={}\n"
        "for namepart in names:\n"
        " path=root/namepart; value=read(path)\n"
        " if managed.get(str(path))!=hashlib.sha256(value.encode()).hexdigest(): raise SystemExit(34)\n"
        " files[namepart]=value\n"
        "data=json.loads(subprocess.run(['docker','inspect',name],check=True,capture_output=True).stdout)[0]\n"
        "config=data.get('Config') or {}; labels=config.get('Labels') or {}\n"
        "if labels.get('io.solobot.remnawave.owner')!=owner or not (data.get('State') or {}).get('Running'): raise SystemExit(35)\n"
        "compose=json.loads(files[names[0]]); service=(compose.get('services') or {}).get(name) or {}\n"
        "if (service.get('labels') or {}).get('io.solobot.remnawave.owner')!=owner or service.get('image')!=config.get('Image'): raise SystemExit(36)\n"
        "secret=''; environment={}\n"
        "if name=='remnanode':\n"
        " environment=dict(value.split('=',1) for value in config.get('Env') or [] if '=' in value)\n"
        " expected=service.get('environment') or {}\n"
        " secret=environment.get('SECRET_KEY','')\n"
        " if not secret or expected.get('SECRET_KEY')!=secret or str(expected.get('NODE_PORT'))!=environment.get('NODE_PORT'): raise SystemExit(37)\n"
        "else:\n"
        " runtime=subprocess.run(['docker','exec',name,'cat','/usr/local/etc/haproxy/haproxy.cfg'],check=True,capture_output=True).stdout.decode()\n"
        " if runtime!=files[names[1]]: raise SystemExit(38)\n"
        "print(json.dumps({'owner':owner,'image':config.get('Image'),'files':files,'node_secret':secret,'node_port':environment.get('NODE_PORT')}))\n"
    )
    raw = await remote.run(
        f"python3 -c {shlex.quote(script)} {ROOT} {shlex.quote(name)} {shlex.quote(json.dumps(required))}",
        step="recovery",
        timeout=60,
        secret=True,
    )
    try:
        state = json.loads(raw)
        if not isinstance(state, dict) or not re.fullmatch(r"[a-f0-9]{64}", state.get("owner", "")):
            raise ValueError
        if not isinstance(state.get("files"), dict) or set(state["files"]) != set(required):
            raise ValueError
        if any(not isinstance(value, str) for value in state["files"].values()):
            raise ValueError
        if not isinstance(state.get("image"), str) or not state["image"].startswith(
            "haproxy:" if haproxy else "remnawave/node:"
        ):
            raise ValueError
        if not haproxy and (not state.get("node_secret") or str(spec.node_port) != str(state.get("node_port"))):
            raise ValueError
        if previous and previous.get("remote_owner_id") and previous["remote_owner_id"] != state["owner"]:
            raise ValueError
    except (ValueError, TypeError):
        _refuse("Не удалось подтвердить принадлежность работающего сервера этой установке")
    return state


def _base(job_id: str, spec: InstallSpec) -> dict:
    """Подготавливает снимок подтверждённой установленной ноды."""
    return {
        "install_id": job_id,
        "installed": True,
        "remote_deployed": True,
        "installed_addons": list(spec.addons),
        "done": [
            "keys",
            "panel_profile",
            "panel_node",
            "docker",
            "addons",
            "verify",
            "cdn",
            "panel_squad",
            "panel_host",
        ],
    }


async def _recover_node(panel: InstallerPanel, job_id: str, spec: InstallSpec) -> dict:
    """Восстанавливает привязки ноды по точному профилю панели."""
    if spec.transport not in TCP_REALITY_TRANSPORTS or spec.role not in {"normal", "exit", "haproxy_exit"}:
        _refuse("Для этой установки требуется отдельная проверка сохранённых ресурсов; сервер не изменён")
    nodes = await panel._call("GET", "/nodes")
    if not isinstance(nodes, list) or any(not isinstance(item, dict) for item in nodes):
        _refuse("Панель не вернула список установленных нод")
    matches = [node for node in nodes if node.get("address") == spec.address and node.get("port") == spec.node_port]
    if len(matches) != 1:
        _refuse("Не удалось однозначно найти существующую ноду в панели; сервер не изменён")
    node = matches[0]
    node_uuid = _uuid(node.get("uuid"), "нода")
    binding = node.get("configProfile")
    if not isinstance(binding, dict):
        _refuse("Панель не подтвердила профиль существующей ноды")
    profile_uuid = _uuid(binding.get("activeConfigProfileUuid"), "профиль ноды")
    profile = await panel._call("GET", f"/config-profiles/{profile_uuid}")
    config = profile.get("config") if isinstance(profile, dict) else None
    inbounds = config.get("inbounds") if isinstance(config, dict) else None
    if (
        not isinstance(profile, dict)
        or profile.get("uuid") != profile_uuid
        or not isinstance(inbounds, list)
        or len(inbounds) != 1
        or not isinstance(inbounds[0], dict)
    ):
        _refuse("Профиль панели не соответствует установленной ноде")
    inbound = inbounds[0]
    tag = inbound.get("tag")
    if not isinstance(tag, str) or not tag.endswith("_" + job_id[:10]):
        _refuse("Профиль не подтвердил принадлежность этой установке")
    stream = inbound.get("streamSettings") or {}
    if not isinstance(stream, dict):
        _refuse("Панель не подтвердила транспорт существующего инбаунда")
    reality = stream.get("realitySettings") or {}
    if not isinstance(reality, dict):
        _refuse("Панель не подтвердила параметры Reality")
    short_ids = reality.get("shortIds")
    if (
        not isinstance(short_ids, list)
        or len(short_ids) != 1
        or not isinstance(short_ids[0], str)
        or not re.fullmatch(r"(?:[a-f0-9]{2}){1,8}", short_ids[0])
    ):
        _refuse("Не удалось однозначно восстановить параметры Reality")
    artifacts = _base(job_id, spec)
    try:
        public_key = reality_public_key(reality.get("privateKey"))
        artifacts.update(
            profile_uuid=profile_uuid,
            node_uuid=node_uuid,
            inbound_tag=tag,
            private_key=reality["privateKey"],
            public_key=public_key,
            short_id=short_ids[0],
            keys_generated=True,
            profile_config=copy.deepcopy(config),
        )
        if build_profile(spec, copy.deepcopy(artifacts)) != config:
            raise ValueError
        panel._bind_inbounds(spec, artifacts, profile, config)
        panel._validate_node(spec, artifacts, node, check_name=False)
    except (ValueError, KeyError, TypeError):
        _refuse("Живая конфигурация Reality отличается от параметров установки")
    if (
        node.get("isConnected") is not True
        or node.get("isDisabled") is not False
        or node.get("isConnecting") is not False
    ):
        _refuse("Выходная нода не подтвердила подключение к панели")
    if spec.squad_uuid and spec.role != "exit":
        squad = await panel._call("GET", f"/internal-squads/{_uuid(spec.squad_uuid, 'сквад')}")
        members = squad.get("inbounds") if isinstance(squad, dict) else None
        if (
            not isinstance(squad, dict)
            or squad.get("uuid") != spec.squad_uuid
            or not isinstance(members, list)
            or not any(isinstance(item, dict) and item.get("uuid") == artifacts["inbound_uuid"] for item in members)
        ):
            _refuse("Клиентский сквад не подтвердил инбаунд установленной ноды")
        artifacts["squad_uuid"] = spec.squad_uuid
    return artifacts


async def _recover_hosts(panel: InstallerPanel, spec: InstallSpec, artifacts: dict) -> None:
    """Отдельно восстанавливает доступные привязки хостов подписки."""
    if not spec.publish_host or spec.role in {"exit", "haproxy_exit"}:
        return
    try:
        hosts = await panel._call("GET", "/hosts")
    except InstallError:
        hosts = None
    available = isinstance(hosts, list) and all(isinstance(item, dict) for item in hosts)
    for index, route in enumerate(spec.cascade_specs()):
        state = artifacts if index == 0 else artifacts["cascade_routes"][route.exit_install_id]
        expected = panel._host_body(route, state)
        resolution = {"status": "unavailable", "expected": expected}
        state["host_recovery"] = resolution
        if not available:
            continue
        collisions = [
            host
            for host in hosts
            if host.get("address") == expected["address"] and host.get("port") == expected["port"]
        ]
        owned = [host for host in collisions if _host_identity_matches(host, expected)]
        if len(owned) > 1:
            resolution["status"] = "ambiguous"
            continue
        if not owned:
            resolution["status"] = "changed" if collisions else "missing"
            continue
        try:
            identifier = _uuid(owned[0].get("uuid"), "хост")
        except InstallError:
            resolution["status"] = "changed"
            continue
        state["host_uuid"] = identifier
        state["host_snapshot"] = copy.deepcopy(owned[0])
        resolution["status"] = "bound"


async def recover_artifacts(
    job_id: str, spec: InstallSpec, previous_artifacts: dict | None, remote: RemoteServer, lookup_snapshot
) -> dict:
    """Возвращает проверенный снимок без изменения сервера и панели."""
    if not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise ValueError("Некорректный номер установки")
    if previous_artifacts is not None:
        restored = copy.deepcopy(previous_artifacts)
        if restored.get("installed") or restored.get("remote_deployed"):
            await _remote_state(remote, spec, restored)
        return restored
    if spec.transport not in TCP_REALITY_TRANSPORTS or spec.role not in {
        "normal",
        "exit",
        "haproxy_exit",
        "haproxy_entry",
    }:
        _refuse("Автоматическое восстановление этого транспорта пока недоступно; сервер не изменён")
    proof = await _remote_state(remote, spec, None)
    async with InstallerPanel(spec) as panel:
        await panel.ensure_version()
        if spec.role == "haproxy_entry":
            artifacts = _base(job_id, spec)
            artifacts["done"] = ["docker", "addons", "verify", "panel_squad", "panel_host"]
            artifacts["cascade_routes"] = {}
            for index, route in enumerate(spec.cascade_specs()):
                snapshot = await lookup_snapshot(route.exit_install_id)
                if (
                    not isinstance(snapshot, dict)
                    or snapshot.get("id") != route.exit_install_id
                    or snapshot.get("status") not in {"succeeded", "warning"}
                ):
                    _refuse("Не найден установленный выход каскада")
                target = InstallSpec.from_dict(snapshot.get("params") or {})
                if (
                    target.role != "haproxy_exit"
                    or target.panel_ref != spec.panel_ref
                    or target.squad_uuid != spec.squad_uuid
                    or target.address == spec.address
                    or ipaddress.ip_address(target.transit_address()).version
                    != ipaddress.ip_address(spec.transit_address()).version
                    or not any(
                        ipaddress.ip_address(spec.transit_address()) in ipaddress.ip_network(source)
                        for source in target.haproxy_sources
                    )
                ):
                    _refuse("Параметры выхода не соответствуют действующему каскаду")
                saved = await _recover_node(panel, snapshot["id"], target)
                state = artifacts if index == 0 else artifacts["cascade_routes"].setdefault(route.exit_install_id, {})
                state["haproxy"] = {
                    "exit_install_id": snapshot["id"],
                    "panel_ref": target.panel_ref,
                    "name": target.name,
                    "address": target.transit_address(),
                    "node_address": target.address,
                    "port": target.client_port,
                    "profile_uuid": saved["profile_uuid"],
                    "inbound_uuid": saved["inbound_uuid"],
                    "node_uuid": saved["node_uuid"],
                    "public_key": saved["public_key"],
                    "short_id": saved["short_id"],
                    "reality_domain": target.reality_server_name(),
                    "squad_uuid": target.squad_uuid,
                }
            if proof["files"]["haproxy/haproxy.cfg"] != build_haproxy_config(spec, artifacts):
                _refuse("Работающая конфигурация HAProxy отличается от сохранённых направлений")
            artifacts["haproxy_image"] = proof["image"]
        else:
            artifacts = await _recover_node(panel, job_id, spec)
            artifacts.update(node_secret=proof["node_secret"], deployed_node_image=proof["image"])
        await _recover_hosts(panel, spec, artifacts)
    artifacts["remote_owner_id"] = proof["owner"]
    return artifacts

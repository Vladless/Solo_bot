import hashlib
import ipaddress
import json
import shlex

from .remote import (
    APT,
    ROOT,
    RemoteServer,
    _check_container_owner,
    _listener_conflict,
    _node_listener_pids,
    claim_paths,
    ensure_docker,
    owner_id,
)
from .types import InstallError, InstallSpec


HAPROXY_IMAGE = "haproxy:3.2.26-alpine"
HAPROXY_CONTAINER = "solobot-haproxy"
_DIRECTORY = ROOT + "/haproxy"
_CONFIG = _DIRECTORY + "/haproxy.cfg"
_COMPOSE = _DIRECTORY + "/compose.json"


def _target(artifacts: dict) -> tuple[str, int]:
    """Проверяет сохранённый адрес выхода каскада."""
    value = artifacts.get("haproxy") if isinstance(artifacts, dict) else None
    try:
        address = str(ipaddress.ip_address(value["address"]))
        port = value["port"]
    except (ValueError, KeyError, TypeError):
        raise InstallError("haproxy", "Не сохранён проверенный выход каскада HAProxy", retryable=False) from None
    if type(port) is not int or not 1 <= port <= 65535:
        raise InstallError("haproxy", "Некорректный порт выхода каскада HAProxy", retryable=False)
    return address, port


def _route_targets(spec: InstallSpec, artifacts: dict) -> list[tuple[InstallSpec, str, int]]:
    """Проверяет адреса и отдельные порты всех направлений."""
    if spec.role != "haproxy_entry" or spec.transport != "vless_tcp_reality":
        raise InstallError("haproxy", "HAProxy поддерживает только вход TCP Reality", retryable=False)
    result = []
    ports = set()
    children = artifacts.get("cascade_routes") or {}
    if not isinstance(children, dict):
        raise InstallError("haproxy", "Некорректные сохранённые направления каскада", retryable=False)
    for index, route in enumerate(spec.cascade_specs()):
        if type(route.client_port) is not int or not 1 <= route.client_port <= 65535 or route.client_port in ports:
            raise InstallError("haproxy", "Некорректный или повторяющийся клиентский порт HAProxy", retryable=False)
        child = artifacts if index == 0 else children.get(route.exit_install_id, {})
        address, port = _target(child)
        ports.add(route.client_port)
        result.append((route, address, port))
    return result


def build_haproxy_config(spec: InstallSpec, artifacts: dict) -> str:
    """Собирает прозрачный TCP-прокси с PROXY v2."""
    routes = _route_targets(spec, artifacts)
    config = (
        "global\n"
        "    log stdout format raw local0\n"
        "    maxconn 65535\n"
        "defaults\n"
        "    log global\n"
        "    mode tcp\n"
        "    option tcplog\n"
        "    option clitcpka\n"
        "    option srvtcpka\n"
        "    timeout connect 10s\n"
        "    timeout client 24h\n"
        "    timeout server 24h\n"
    )
    for index, (route, address, port) in enumerate(routes):
        name = "reality" if index == 0 else f"reality_{route.client_port}"
        endpoint = f"[{address}]:{port}" if ipaddress.ip_address(address).version == 6 else f"{address}:{port}"
        config += (
            f"frontend {name}\n"
            f"    bind 0.0.0.0:{route.client_port}\n"
            f"    bind [::]:{route.client_port} v6only\n"
            f"    default_backend {name}_exit\n"
            f"backend {name}_exit\n"
            f"    server exit {endpoint} send-proxy-v2 check inter 5s fall 3 rise 2\n"
        )
    return config


def _compose(spec: InstallSpec) -> dict:
    """Описывает отдельный контейнер TCP-прокси."""
    return {
        "services": {
            HAPROXY_CONTAINER: {
                "container_name": HAPROXY_CONTAINER,
                "image": HAPROXY_IMAGE,
                "network_mode": "host",
                "restart": "always",
                "user": "0:0",
                "read_only": True,
                "cap_drop": ["ALL"],
                "cap_add": ["NET_BIND_SERVICE"],
                "security_opt": ["no-new-privileges:true"],
                "labels": {"io.solobot.remnawave.owner": owner_id(spec)},
                "volumes": [_CONFIG + ":/usr/local/etc/haproxy/haproxy.cfg:ro"],
            },
        },
    }


async def check_haproxy(remote: RemoteServer, spec: InstallSpec, artifacts: dict) -> None:
    """Проверяет контейнер, собственный listener и доступность выхода."""
    routes = _route_targets(spec, artifacts)
    expected = hashlib.sha256(build_haproxy_config(spec, artifacts).encode()).hexdigest()
    script = (
        "import hashlib,json,socket,subprocess,sys\n"
        "owner,expected,targets=sys.argv[1:]\n"
        f"data=json.loads(subprocess.run(['docker','inspect',{HAPROXY_CONTAINER!r}],capture_output=True,check=True).stdout)[0]\n"
        "if (data.get('Config',{}).get('Labels') or {}).get('io.solobot.remnawave.owner')!=owner: raise SystemExit(33)\n"
        "if not data.get('State',{}).get('Running'): raise SystemExit(34)\n"
        "health=(data.get('State',{}).get('Health') or {}).get('Status')\n"
        "if health in ('unhealthy','starting'): raise SystemExit(35)\n"
        f"if not any(m.get('Source')=={_CONFIG!r} and m.get('Destination')=='/usr/local/etc/haproxy/haproxy.cfg' "
        "and m.get('RW') is False for m in data.get('Mounts',[])): raise SystemExit(36)\n"
        f"runtime=subprocess.run(['docker','exec',{HAPROXY_CONTAINER!r},'cat','/usr/local/etc/haproxy/haproxy.cfg'],check=True,capture_output=True)\n"
        "if hashlib.sha256(runtime.stdout).hexdigest()!=expected: raise SystemExit(37)\n"
        f"subprocess.run(['docker','exec',{HAPROXY_CONTAINER!r},'haproxy','-c','-f','/usr/local/etc/haproxy/haproxy.cfg'],check=True,capture_output=True)\n"
        "for address,port in json.loads(targets):\n"
        " with socket.create_connection((address,int(port)),timeout=10): pass\n"
    )
    targets = json.dumps([[address, port] for _route, address, port in routes])
    await remote.run(
        f"python3 -c {shlex.quote(script)} {owner_id(spec)} {expected} {shlex.quote(targets)}",
        step="haproxy_verify",
        timeout=30,
    )
    for _attempt in range(6):
        pids = await _node_listener_pids(remote, spec, name=HAPROXY_CONTAINER)
        listeners = await remote.run("ss -tlnpH", step="haproxy_verify")
        present = {
            int(line.split()[3].rsplit(":", 1)[-1])
            for line in listeners.splitlines()
            if len(line.split()) >= 4 and line.split()[3].rsplit(":", 1)[-1].isdecimal()
        }
        if all(
            route.client_port in present and not _listener_conflict(listeners, route.client_port, pids)
            for route, _, _ in routes
        ):
            return
        await remote.run("sleep 2", step="haproxy_verify", timeout=5)
    raise InstallError("haproxy_verify", "Собственный контейнер HAProxy не подтвердил все клиентские TCP-порты")


async def deploy_haproxy(remote: RemoteServer, spec: InstallSpec, artifacts: dict, log, checkpoint=None) -> None:
    """Проверяет новую конфигурацию и возвращает прежнюю при сбое."""
    config = build_haproxy_config(spec, artifacts)
    await claim_paths(remote, spec)
    await ensure_docker(remote)
    await _check_container_owner(remote, spec, HAPROXY_CONTAINER)
    snapshot_script = (
        "import hashlib,json,pathlib,sys\n"
        "root=pathlib.Path(sys.argv[1]); result={}\n"
        "manifest=root/'.managed-files.json'\n"
        "managed=json.loads(manifest.read_text()) if manifest.is_file() else {}\n"
        "for name in ('haproxy.cfg','compose.json'):\n"
        " path=root/'haproxy'/name\n"
        " if path.is_symlink(): raise SystemExit(31)\n"
        " if path.exists():\n"
        "  if managed.get(str(path))!=hashlib.sha256(path.read_bytes()).hexdigest(): raise SystemExit(32)\n"
        "  result[name]=path.read_text()\n"
        "print(json.dumps(result))\n"
    )
    try:
        previous = json.loads(await remote.run(f"python3 -c {shlex.quote(snapshot_script)} {ROOT}", step="haproxy"))
    except (TypeError, ValueError):
        raise InstallError("haproxy", "Не удалось проверить предыдущую конфигурацию HAProxy", retryable=False) from None
    if not isinstance(previous, dict) or set(previous) - {"haproxy.cfg", "compose.json"}:
        raise InstallError("haproxy", "Некорректный снимок предыдущей конфигурации HAProxy", retryable=False)
    candidate = _DIRECTORY + "/candidate.cfg"
    await remote.write(candidate, config, mode=0o644)
    await log("Проверка конфигурации и запуск HAProxy с PROXY v2")
    await remote.run(f"docker pull {HAPROXY_IMAGE}", step="haproxy", timeout=600)
    await remote.run(
        f"docker run --rm --network none --read-only -v {candidate}:/usr/local/etc/haproxy/haproxy.cfg:ro "
        f"{HAPROXY_IMAGE} haproxy -c -f /usr/local/etc/haproxy/haproxy.cfg",
        step="haproxy",
        timeout=60,
    )
    try:
        await remote.write(_CONFIG, config, mode=0o644)
        await remote.write(_COMPOSE, json.dumps(_compose(spec)))
        await remote.run(
            f"cd {_DIRECTORY} && docker compose -f compose.json up -d --force-recreate --pull never",
            step="haproxy",
            timeout=180,
        )
        await check_haproxy(remote, spec, artifacts)
    except InstallError:
        await _check_container_owner(remote, spec, HAPROXY_CONTAINER)
        if all(name in previous for name in ("haproxy.cfg", "compose.json")):
            await remote.write(_CONFIG, previous["haproxy.cfg"], mode=0o644)
            await remote.write(_COMPOSE, previous["compose.json"])
            await remote.run(
                f"cd {_DIRECTORY} && docker compose -f compose.json up -d --force-recreate --pull never",
                step="haproxy_rollback",
                timeout=180,
            )
            await log("Восстановлена предыдущая конфигурация HAProxy")
        else:
            remove_script = (
                "import json,subprocess,sys\n"
                f"result=subprocess.run(['docker','inspect',{HAPROXY_CONTAINER!r}],capture_output=True)\n"
                "if result.returncode:\n"
                " subprocess.run(['docker','info'],check=True,capture_output=True); raise SystemExit(0)\n"
                "data=json.loads(result.stdout)[0]\n"
                "if (data.get('Config',{}).get('Labels') or {}).get('io.solobot.remnawave.owner')!=sys.argv[1]: raise SystemExit(33)\n"
                f"subprocess.run(['docker','rm','-f',{HAPROXY_CONTAINER!r}],check=True,capture_output=True)\n"
            )
            await remote.run(f"python3 -c {shlex.quote(remove_script)} {owner_id(spec)}", step="haproxy_rollback")
        raise
    from .remote import _open_firewall

    await _open_firewall(remote, spec)
    artifacts.update(remote_deployed=True, haproxy_image=HAPROXY_IMAGE)
    if checkpoint:
        await checkpoint(artifacts)


async def protect_haproxy_exit(remote: RemoteServer, spec: InstallSpec, *, additional_ports=()) -> None:
    """Разрешает PROXY v2 только с доверенных адресов входа."""
    if spec.role != "haproxy_exit":
        return
    sources = getattr(spec, "haproxy_sources", [])
    if not sources:
        raise InstallError("firewall", "Укажите доверенные IP входов HAProxy", retryable=False)
    try:
        networks = [ipaddress.ip_network(value, strict=False) for value in sources]
    except ValueError:
        raise InstallError("firewall", "Некорректный IP входа HAProxy", retryable=False) from None
    if any(network.prefixlen != network.max_prefixlen for network in networks):
        raise InstallError("firewall", "Разрешены только отдельные IP входов HAProxy", retryable=False)
    ports = {spec.client_port, *additional_ports}
    if any(type(port) is not int or not 1 <= port <= 65535 for port in ports):
        raise InstallError("firewall", "Некорректный порт выхода HAProxy", retryable=False)
    await remote.run(
        f"command -v nft >/dev/null || ( {APT} update && {APT} install -y nftables )",
        step="firewall",
        timeout=900,
    )
    script = (
        "import hashlib,json,pathlib,subprocess,sys\n"
        "root=pathlib.Path(sys.argv[1]); manifest=root/'.managed-files.json'\n"
        "managed=json.loads(manifest.read_text()) if manifest.is_file() else {}\n"
        "paths=(root/'addons'/'haproxy_exit.nft',root/'addons'/'apply-haproxy_exit.py',"
        "pathlib.Path('/etc/systemd/system/solobot-haproxy_exit-nft.service'))\n"
        "owned=[]\n"
        "for path in paths:\n"
        " if path.is_symlink(): raise SystemExit(31)\n"
        " if path.exists():\n"
        "  if managed.get(str(path))!=hashlib.sha256(path.read_bytes()).hexdigest(): raise SystemExit(32)\n"
        "  owned.append(path)\n"
        "listed=subprocess.run(['nft','-j','list','tables'],capture_output=True,check=True)\n"
        "tables=json.loads(listed.stdout).get('nftables',[])\n"
        "present=any(item.get('table',{}).get('family')=='inet' and item.get('table',{}).get('name')=='solobot_haproxy_exit' for item in tables)\n"
        "if present and not owned: raise SystemExit(33)\n"
    )
    await remote.run(f"python3 -c {shlex.quote(script)} {ROOT}", step="firewall")
    lines = [
        "table inet solobot_haproxy_exit {",
        "chain inbound { type filter hook input priority -30; policy accept;",
    ]
    for port in sorted(ports):
        for version, family in ((4, "ip"), (6, "ip6")):
            allowed = [str(network) for network in networks if network.version == version]
            if allowed:
                lines.append(f"tcp dport {port} {family} saddr {{ " + ", ".join(allowed) + " } accept")
        lines.append(f"tcp dport {port} drop")
    lines.extend(["}", "}", ""])
    from .addons import _nft_service

    await _nft_service(remote, "haproxy_exit", "\n".join(lines))

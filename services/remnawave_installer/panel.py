import asyncio
import copy
import ipaddress
import uuid

from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any
from urllib.parse import quote

from .defaults import get_install_defaults as get_install_defaults
from .types import NODE_VERSION, PANEL_VERSION, InstallError, InstallSpec


Checkpoint = Callable[[dict], Awaitable[None]]
_SQUAD_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}


def _uuid(value: Any, label: str) -> str:
    """Проверяет идентификатор объекта панели."""
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as error:
        raise InstallError("panel", f"Панель вернула некорректный UUID: {label}") from error


def _approved_specs(artifacts: dict, previous_spec: InstallSpec, spec: InstallSpec) -> list[InstallSpec]:
    """Читает подтверждённые варианты текущего изменения."""
    edit = artifacts.get("edit") or {}
    approved = [previous_spec, spec]
    values = [edit.get("before"), edit.get("previous_target"), *(edit.get("applied_specs") or {}).values()]
    for value in values:
        if not isinstance(value, dict):
            continue
        try:
            candidate = InstallSpec.from_dict(value)
        except (ValueError, TypeError):
            continue
        if (
            candidate.panel_ref == previous_spec.panel_ref
            and candidate.address == previous_spec.address
            and candidate.role == previous_spec.role
        ):
            approved.append(candidate)
    return approved


async def list_panel_options() -> list[dict]:
    """Возвращает настроенные панели без ключей доступа."""
    from database import async_session_maker, get_servers

    async with async_session_maker() as session:
        grouped = await get_servers(session, include_enabled=True)
    panels: dict[str, set[str]] = {}
    for cluster, servers in grouped.items():
        for server in servers:
            if str(server.get("panel_type") or "").lower() != "remnawave":
                continue
            api_url = str(server.get("api_url") or "").strip().rstrip("/")
            if api_url:
                panels.setdefault(api_url, set()).add(str(cluster))
    return [{"panel_ref": url, "name": ", ".join(sorted(names))} for url, names in sorted(panels.items())]


async def _resolve_panel_url(panel_ref: str) -> str:
    """Находит единственную настроенную панель."""
    from database import async_session_maker, get_servers

    async with async_session_maker() as session:
        grouped = await get_servers(session, include_enabled=True)
    matched = set()
    for cluster, servers in grouped.items():
        for server in servers:
            if str(server.get("panel_type") or "").lower() != "remnawave":
                continue
            api_url = str(server.get("api_url") or "").strip().rstrip("/")
            if api_url and panel_ref in {str(cluster), str(server.get("server_name")), api_url}:
                matched.add(api_url)
    if len(matched) != 1:
        raise InstallError("panel", "Настроенная панель не найдена или выбор неоднозначен", retryable=False)
    return matched.pop()


async def list_squad_options(panel_ref: str) -> list[dict]:
    """Возвращает сквады выбранной настроенной панели."""
    async with InstallerPanel(InstallSpec(panel_ref=panel_ref)) as panel:
        await panel.ensure_version()
        return [{"uuid": item["uuid"], "name": item["name"]} for item in await panel.list_squads()]


@asynccontextmanager
async def _open_panel(spec: InstallSpec):
    """Открывает клиент с существующей авторизацией панели."""
    from panels.remnawave_runtime import remnawave_api
    from settings.config import REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD

    api_url = await _resolve_panel_url(spec.panel_ref)
    async with remnawave_api(api_url) as api:
        if not await api.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
            raise InstallError("panel", "Не удалось авторизоваться в настроенной панели")
        yield api


class InstallerPanel:
    def __init__(self, spec: InstallSpec, checkpoint: Checkpoint | None = None) -> None:
        self.spec = spec
        self.checkpoint = checkpoint
        self.api = None
        self._context = None
        self._version_checked = False

    async def __aenter__(self) -> "InstallerPanel":
        self._context = _open_panel(self.spec)
        self.api = await self._context.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        if self._context is not None:
            await self._context.__aexit__(exc_type, exc_value, traceback)
        self.api = None
        self._version_checked = False

    async def _save(self, artifacts: dict, checkpoint: Checkpoint | None = None) -> None:
        """Сохраняет снимок до следующего внешнего изменения."""
        callback = checkpoint or self.checkpoint
        if callback:
            await callback(artifacts)

    @staticmethod
    def _update_intents(artifacts: dict, kind: str, identifier: str) -> list[dict]:
        """Возвращает сохранённые разрешённые записи одного объекта."""
        update = (artifacts.get("panel_updates") or {}).get(kind) or {}
        return update.get("approved", []) if update.get("uuid") == identifier else []

    async def _record_update(self, artifacts: dict, kind: str, identifier: str, *bodies: dict) -> None:
        """Фиксирует ожидаемую запись до обращения к панели."""
        artifacts.setdefault("panel_updates", {})[kind] = {"uuid": identifier, "approved": copy.deepcopy(list(bodies))}
        await self._save(artifacts)

    async def _call(self, method: str, path: str, body: dict | None = None, *, missing: bool = False):
        """Запрашивает панель без вывода секретных ответов."""
        if self.api is None:
            raise InstallError("panel", "Клиент панели не открыт")
        try:
            response = await self.api._request(method, path, json=body)
        except (asyncio.CancelledError, InstallError):
            raise
        except Exception as error:
            raise InstallError("panel", f"Панель недоступна при {method} {path}") from error
        if response.status_code == 404 and missing:
            return None
        expected = 201 if method == "POST" else 200
        if response.status_code != expected:
            raise InstallError("panel", f"Панель отклонила {method} {path}: HTTP {response.status_code}")
        try:
            payload = response.json()
        except (ValueError, TypeError) as error:
            raise InstallError("panel", "Панель вернула некорректный JSON") from error
        if not isinstance(payload, dict) or "response" not in payload:
            raise InstallError("panel", "Ответ панели не соответствует контракту 3.2.1")
        return payload["response"]

    async def ensure_version(self) -> str:
        """Проверяет точную поддерживаемую версию панели."""
        payload = await self._call("GET", "/system/metadata")
        version = payload.get("version") if isinstance(payload, dict) else None
        if version != PANEL_VERSION:
            raise InstallError(
                "panel", f"Для установки поддерживается только Remnawave {PANEL_VERSION}", retryable=False
            )
        self._version_checked = True
        return version

    async def _ready(self) -> None:
        """Проверяет контракт перед изменением панели."""
        if not self._version_checked:
            await self.ensure_version()

    async def list_profiles(self) -> list[dict]:
        """Читает профили по текущему контракту."""
        data = await self._call("GET", "/config-profiles")
        profiles = data.get("configProfiles") if isinstance(data, dict) else None
        if not isinstance(profiles, list) or any(not isinstance(item, dict) for item in profiles):
            raise InstallError("panel_profile", "Панель вернула некорректный список профилей")
        return profiles

    async def list_squads(self) -> list[dict]:
        """Читает доступные внутренние сквады."""
        data = await self._call("GET", "/internal-squads")
        squads = data.get("internalSquads") if isinstance(data, dict) else None
        if not isinstance(squads, list) or any(not isinstance(item, dict) for item in squads):
            raise InstallError("panel_host", "Панель вернула некорректный список сквадов")
        return squads

    async def fetch_node_secret(self, artifacts: dict, checkpoint: Checkpoint | None = None) -> str:
        """Получает сертификат ноды один раз на задачу."""
        await self._ready()
        if artifacts.get("node_secret"):
            return artifacts["node_secret"]
        await self._save(artifacts, checkpoint)
        result = await self._call("GET", "/keygen")
        secret = result.get("secretKey") if isinstance(result, dict) else None
        if not isinstance(secret, str) or not secret.strip():
            raise InstallError("panel_node", "Панель не вернула сертификат ноды")
        artifacts["node_secret"] = secret
        await self._save(artifacts, checkpoint)
        return secret

    async def ensure_profile(self, spec: InstallSpec, artifacts: dict, config: dict) -> dict:
        """Создаёт собственный профиль и сохраняет UUID инбаунда."""
        await self._ready()
        inbounds = config.get("inbounds")
        if not isinstance(inbounds, list) or len(inbounds) != 1 or not isinstance(inbounds[0], dict):
            raise InstallError("panel_profile", "Профиль установки должен содержать один инбаунд", retryable=False)
        tag = inbounds[0].get("tag")
        if not isinstance(tag, str) or not tag:
            raise InstallError("panel_profile", "У инбаунда отсутствует тег", retryable=False)
        profile_uuid = artifacts.get("profile_uuid")
        if profile_uuid:
            profile = await self._call("GET", f"/config-profiles/{_uuid(profile_uuid, 'профиль')}")
        else:
            profiles = await self.list_profiles()
            matching = [item for item in profiles if item.get("name") == spec.profile_name]
            if matching:
                install_id = artifacts.get("install_id")
                if len(matching) != 1 or not isinstance(install_id, str) or not tag.endswith("_" + install_id[:10]):
                    raise InstallError(
                        "panel_profile", "Имя профиля уже занято; чужой профиль не изменён", retryable=False
                    )
                profile = await self._call("GET", f"/config-profiles/{_uuid(matching[0].get('uuid'), 'профиль')}")
                if not isinstance(profile, dict) or profile.get("config") != config:
                    raise InstallError(
                        "panel_profile", "Конфигурация занятого профиля не соответствует задаче", retryable=False
                    )
            else:
                await self._save(artifacts)
                profile = await self._call("POST", "/config-profiles", {"name": spec.profile_name, "config": config})
            if not isinstance(profile, dict):
                raise InstallError("panel_profile", "Панель не вернула созданный профиль")
            artifacts["profile_uuid"] = _uuid(profile.get("uuid"), "профиль")
            await self._save(artifacts)
        if not isinstance(profile, dict) or profile.get("name") != spec.profile_name:
            raise InstallError("panel_profile", "Сохранённый профиль не соответствует задаче", retryable=False)
        if profile.get("config") != config:
            raise InstallError("panel_profile", "Конфигурация своего профиля изменена вне установщика", retryable=False)
        profile_uuid = _uuid(profile.get("uuid"), "профиль")
        raw_inbounds = profile.get("inbounds")
        if not isinstance(raw_inbounds, list):
            raise InstallError("panel_profile", "Панель не вернула инбаунды профиля")
        matches = [item for item in raw_inbounds if isinstance(item, dict) and item.get("tag") == tag]
        if len(matches) != 1:
            raise InstallError("panel_profile", "Инбаунд созданного профиля не определён однозначно")
        inbound = matches[0]
        inbound_uuid = _uuid(inbound.get("uuid"), "инбаунд")
        if inbound.get("profileUuid") != profile_uuid:
            raise InstallError("panel_profile", "Инбаунд принадлежит другому профилю", retryable=False)
        if artifacts.get("inbound_uuid") and artifacts["inbound_uuid"] != inbound_uuid:
            raise InstallError("panel_profile", "UUID сохранённого инбаунда изменился", retryable=False)
        artifacts.update(profile_uuid=profile_uuid, inbound_uuid=inbound_uuid, inbound_tag=tag)
        artifacts["profile_config"] = copy.deepcopy(config)
        artifacts["xhttp_extra"] = inbounds[0].get("streamSettings", {}).get("xhttpSettings", {}).get("extra", {})
        await self._save(artifacts)
        return {"profile_uuid": profile_uuid, "inbound_uuid": inbound_uuid}

    async def update_profile(
        self, spec: InstallSpec, artifacts: dict, config: dict, *, previous_spec: InstallSpec | None = None
    ) -> dict:
        """Изменяет собственный профиль с сохранением инбаунда."""
        await self._ready()
        profile_uuid = _uuid(artifacts.get("profile_uuid"), "профиль")
        inbound_uuid = _uuid(artifacts.get("inbound_uuid"), "инбаунд")
        saved = await self._call("GET", f"/config-profiles/{profile_uuid}")
        configured = config.get("inbounds")
        previous_spec = previous_spec or spec
        approved_names = {item.profile_name for item in _approved_specs(artifacts, previous_spec, spec)}
        intents = self._update_intents(artifacts, "profile", profile_uuid)
        approved_names.update(item.get("name") for item in intents)
        if (
            not isinstance(saved, dict)
            or saved.get("uuid") != profile_uuid
            or saved.get("name") not in approved_names
            or not isinstance(configured, list)
            or len(configured) != 1
            or not isinstance(configured[0], dict)
            or not isinstance(saved.get("inbounds"), list)
            or len(saved["inbounds"]) != 1
        ):
            raise InstallError("panel_profile", "Обновление чужого или изменённого профиля запрещено", retryable=False)
        matches = [
            item for item in saved.get("inbounds", []) if isinstance(item, dict) and item.get("uuid") == inbound_uuid
        ]
        if len(matches) != 1 or not matches[0].get("tag") or matches[0].get("profileUuid") != profile_uuid:
            raise InstallError("panel_profile", "Сохранённый инбаунд профиля изменён", retryable=False)
        tag = artifacts.get("inbound_tag") or matches[0]["tag"]
        if matches[0]["tag"] != tag:
            raise InstallError("panel_profile", "Сохранённый инбаунд профиля изменён", retryable=False)
        config = copy.deepcopy(config)
        config["inbounds"][0]["tag"] = tag
        approved_configs = [config, *(item.get("config") for item in intents)]
        if artifacts.get("profile_config") is not None:
            approved_configs.append(artifacts["profile_config"])
        if saved.get("config") not in approved_configs:
            raise InstallError(
                "panel_profile", "Профиль изменён вне установщика; обновление остановлено", retryable=False
            )
        body = {"uuid": profile_uuid, "name": spec.profile_name, "config": config}
        if saved.get("name") == spec.profile_name and saved.get("config") == config:
            artifacts.update(inbound_tag=tag, profile_config=config)
            await self._record_update(artifacts, "profile", profile_uuid, body)
            return {"profile_uuid": profile_uuid, "inbound_uuid": inbound_uuid}
        await self._record_update(
            artifacts,
            "profile",
            profile_uuid,
            {"uuid": profile_uuid, "name": saved["name"], "config": saved["config"]},
            body,
        )
        updated = await self._call("PATCH", "/config-profiles", body)
        matches = [
            item
            for item in (updated or {}).get("inbounds", [])
            if isinstance(item, dict) and item.get("uuid") == inbound_uuid
        ]
        if (
            len(matches) != 1
            or matches[0].get("tag") != tag
            or updated.get("uuid") != profile_uuid
            or updated.get("name") != spec.profile_name
            or updated.get("config") != config
        ):
            raise InstallError("panel_profile", "Обновление собственного профиля не подтверждено")
        artifacts.update(inbound_tag=tag, profile_config=config)
        await self._record_update(artifacts, "profile", profile_uuid, body)
        return {"profile_uuid": profile_uuid, "inbound_uuid": inbound_uuid}

    async def update_node(self, previous_spec: InstallSpec, spec: InstallSpec, artifacts: dict) -> dict:
        """Меняет параметры собственной ноды без создания новой."""
        await self._ready()
        node_uuid = _uuid(artifacts.get("node_uuid"), "нода")
        node = await self._call("GET", f"/nodes/{node_uuid}")
        approved = _approved_specs(artifacts, previous_spec, spec)
        candidates = [
            {"uuid": node_uuid, "name": item.name, "address": item.address, "port": item.node_port} for item in approved
        ]
        candidates.extend(self._update_intents(artifacts, "node", node_uuid))
        if not isinstance(node, dict) or not any(
            all(node.get(key) == value for key, value in item.items()) for item in candidates
        ):
            raise InstallError("panel_node", "Параметры ноды не относятся к подтверждённому изменению", retryable=False)
        actual = copy.copy(spec)
        actual.name, actual.address, actual.node_port = node["name"], node["address"], node["port"]
        self._validate_node(actual, artifacts, node)
        body = {"uuid": node_uuid, "name": spec.name, "address": spec.address, "port": spec.node_port}
        if any(node.get(key) != value for key, value in body.items()):
            before = {key: node[key] for key in body}
            await self._record_update(artifacts, "node", node_uuid, before, body)
            node = await self._call("PATCH", "/nodes", body)
        self._validate_node(spec, artifacts, node)
        await self._record_update(artifacts, "node", node_uuid, body)
        return {"node_uuid": node_uuid}

    async def ensure_node(self, spec: InstallSpec, artifacts: dict) -> dict:
        """Создаёт только собственную ноду панели."""
        await self._ready()
        profile_uuid = _uuid(artifacts.get("profile_uuid"), "профиль")
        inbound_uuid = _uuid(artifacts.get("inbound_uuid"), "инбаунд")
        if artifacts.get("node_uuid"):
            node = await self._call("GET", f"/nodes/{_uuid(artifacts['node_uuid'], 'нода')}")
        else:
            nodes = await self._call("GET", "/nodes")
            if not isinstance(nodes, list) or any(not isinstance(item, dict) for item in nodes):
                raise InstallError("panel_node", "Панель вернула некорректный список нод")
            collisions = [
                node for node in nodes if node.get("name") == spec.name or node.get("address") == spec.address
            ]
            owned = []
            for candidate in collisions:
                try:
                    candidate_uuid = _uuid(candidate.get("uuid"), "нода")
                    self._validate_node(spec, {**artifacts, "node_uuid": candidate_uuid}, candidate)
                except InstallError:
                    continue
                owned.append(candidate)
            if len(owned) == 1:
                node = owned[0]
            elif collisions:
                raise InstallError(
                    "panel_node", "Имя или адрес ноды уже занят; существующая нода не изменена", retryable=False
                )
            else:
                await self._save(artifacts)
                node = await self._call(
                    "POST",
                    "/nodes",
                    {
                        "name": spec.name,
                        "address": spec.address,
                        "port": spec.node_port,
                        "configProfile": {"activeConfigProfileUuid": profile_uuid, "activeInbounds": [inbound_uuid]},
                    },
                )
            if not isinstance(node, dict):
                raise InstallError("panel_node", "Панель не вернула созданную ноду")
            artifacts["node_uuid"] = _uuid(node.get("uuid"), "нода")
            await self._save(artifacts)
        self._validate_node(spec, artifacts, node)
        return {"node_uuid": artifacts["node_uuid"]}

    @staticmethod
    def _validate_node(spec: InstallSpec, artifacts: dict, node: Any) -> None:
        """Проверяет принадлежность ноды текущей установке."""
        if not isinstance(node, dict):
            raise InstallError("panel_node", "Панель не вернула ноду")
        profile = node.get("configProfile") or {}
        inbounds = profile.get("activeInbounds") or []
        ids = [item.get("uuid") for item in inbounds if isinstance(item, dict)]
        if (
            node.get("uuid") != artifacts.get("node_uuid")
            or node.get("name") != spec.name
            or node.get("address") != spec.address
            or node.get("port") != spec.node_port
            or profile.get("activeConfigProfileUuid") != artifacts.get("profile_uuid")
            or ids != [artifacts.get("inbound_uuid")]
        ):
            raise InstallError("panel_node", "Сохранённая нода не соответствует задаче", retryable=False)

    async def ensure_host(self, spec: InstallSpec, artifacts: dict) -> dict:
        """Публикует хост после готовности транспорта."""
        await self._ready()
        if not spec.publish_host or spec.role == "exit":
            return {}
        cdn = spec.transport.startswith("cdn_")
        if cdn and not artifacts.get("cloud", {}).get("ready"):
            raise InstallError("panel_host", "CDN ещё не готов к публикации")
        body = self._host_body(spec, artifacts)
        address = body["address"]
        if artifacts.get("host_uuid"):
            host = await self._call("GET", f"/hosts/{_uuid(artifacts['host_uuid'], 'хост')}")
        else:
            hosts = await self._call("GET", "/hosts")
            if not isinstance(hosts, list) or any(not isinstance(item, dict) for item in hosts):
                raise InstallError("panel_host", "Панель вернула некорректный список хостов")
            collisions = [host for host in hosts if host.get("address") == address and host.get("port") == body["port"]]
            owned = [host for host in collisions if all(host.get(key) == value for key, value in body.items())]
            if len(owned) == 1:
                host = owned[0]
            elif collisions:
                raise InstallError("panel_host", "Адрес хоста уже опубликован; чужой хост не изменён", retryable=False)
            else:
                await self._save(artifacts)
                host = await self._call("POST", "/hosts", body)
            if not isinstance(host, dict):
                raise InstallError("panel_host", "Панель не вернула созданный хост")
            artifacts["host_uuid"] = _uuid(host.get("uuid"), "хост")
            await self._save(artifacts)
        if not isinstance(host, dict) or any(host.get(key) != value for key, value in body.items()):
            raise InstallError("panel_host", "Сохранённый хост не соответствует задаче", retryable=False)
        return {"host_uuid": artifacts["host_uuid"]}

    @staticmethod
    def _host_body(spec: InstallSpec, artifacts: dict) -> dict:
        """Собирает управляемые поля хоста панели."""
        cdn = spec.transport.startswith("cdn_")
        address = spec.cdn_domain if cdn else spec.domain if spec.transport == "vless_tcp_selfsteal" else spec.address
        body = {
            "remark": spec.host_remark,
            "address": address,
            "port": 443 if cdn else spec.client_port,
            "isDisabled": not spec.publish_host or spec.role == "exit",
            "inbound": {
                "configProfileUuid": _uuid(artifacts.get("profile_uuid"), "профиль"),
                "configProfileInboundUuid": _uuid(artifacts.get("inbound_uuid"), "инбаунд"),
            },
        }
        if cdn:
            body.update(
                path=artifacts.get("xhttp_path"),
                sni=address,
                host=address,
                alpn="h2" if spec.transport == "cdn_vk" else "h2,http/1.1",
                fingerprint="firefox",
                securityLayer="TLS",
                xhttpExtraParams=artifacts.get("xhttp_extra", {}),
            )
        return body

    async def update_host(self, previous_spec: InstallSpec, spec: InstallSpec, artifacts: dict) -> dict:
        """Изменяет или отключает собственный хост без дубликатов."""
        await self._ready()
        if not artifacts.get("host_uuid"):
            return await self.ensure_host(spec, artifacts)
        host_uuid = _uuid(artifacts["host_uuid"], "хост")
        host = await self._call("GET", f"/hosts/{host_uuid}")
        previous = self._host_body(previous_spec, artifacts)
        desired = self._host_body(spec, artifacts)
        previous.pop("xhttpExtraParams", None)
        approved = [self._host_body(item, artifacts) for item in _approved_specs(artifacts, previous_spec, spec)]
        for body in approved:
            body.pop("xhttpExtraParams", None)
        approved.extend(self._update_intents(artifacts, "host", host_uuid))
        if (
            not isinstance(host, dict)
            or host.get("uuid") != host_uuid
            or host.get("inbound") != desired["inbound"]
            or not any(
                all(host.get(key) == value for key, value in body.items()) for body in (previous, desired, *approved)
            )
        ):
            raise InstallError("panel_host", "Обновление чужого или изменённого хоста запрещено", retryable=False)
        if spec.publish_host and spec.transport.startswith("cdn_"):
            cloud = artifacts.get("cloud") or {}
            if not cloud.get("ready") or cloud.get("domain") != spec.cdn_domain:
                raise InstallError("panel_host", "Новый CDN ещё не готов к публикации")
        if previous_spec.transport.startswith("cdn_") and not spec.transport.startswith("cdn_"):
            desired.update(
                path=None,
                sni=None,
                host=None,
                alpn=None,
                fingerprint=None,
                securityLayer="DEFAULT",
                xhttpExtraParams=None,
            )
        if any(host.get(key) != value for key, value in desired.items()):
            await self._record_update(artifacts, "host", host_uuid, {key: host.get(key) for key in desired}, desired)
            host = await self._call("PATCH", "/hosts", {"uuid": host_uuid, **desired})
        if not isinstance(host, dict) or any(host.get(key) != value for key, value in desired.items()):
            raise InstallError("panel_host", "Изменение хоста не подтверждено панелью")
        await self._record_update(artifacts, "host", host_uuid, desired)
        return {"host_uuid": host_uuid}

    async def move_to_squad(self, previous_spec: InstallSpec, spec: InstallSpec, artifacts: dict) -> None:
        """Переносит свой инбаунд между сквадами, сохраняя остальные."""
        await self._ready()
        if previous_spec.panel_ref != spec.panel_ref:
            raise InstallError("panel_squad", "Перенос между панелями требует отдельной миграции", retryable=False)
        inbound_uuid = _uuid(artifacts.get("inbound_uuid"), "инбаунд")
        previous = _uuid(previous_spec.squad_uuid, "сквад") if previous_spec.squad_uuid else None
        desired = _uuid(spec.squad_uuid, "сквад") if spec.squad_uuid and spec.role != "exit" else None
        panel_key = str(getattr(self.api, "base_url", spec.panel_ref)).rstrip("/")
        squads = {}
        async with AsyncExitStack() as stack:
            for squad_uuid in sorted({item for item in (previous, desired) if item}):
                lock = _SQUAD_LOCKS.setdefault((panel_key, squad_uuid), asyncio.Lock())
                await stack.enter_async_context(lock)
                squad = await self._call("GET", f"/internal-squads/{squad_uuid}")
                if (
                    not isinstance(squad, dict)
                    or squad.get("uuid") != squad_uuid
                    or not isinstance(squad.get("inbounds"), list)
                ):
                    raise InstallError("panel_squad", "Панель не вернула выбранный сквад")
                ids = [_uuid(item.get("uuid"), "инбаунд") for item in squad["inbounds"] if isinstance(item, dict)]
                if len(ids) != len(squad["inbounds"]) or len(ids) != len(set(ids)):
                    raise InstallError("panel_squad", "Панель вернула некорректный состав сквада")
                squads[squad_uuid] = ids
            for squad_uuid, include in ((desired, True), (previous, False)):
                if squad_uuid is None or (not include and previous == desired):
                    continue
                ids = squads[squad_uuid]
                updated_ids = (
                    [*ids, inbound_uuid]
                    if include and inbound_uuid not in ids
                    else [item for item in ids if item != inbound_uuid]
                    if not include
                    else ids
                )
                if updated_ids != ids:
                    await self._save(artifacts)
                    result = await self._call(
                        "PATCH", "/internal-squads", {"uuid": squad_uuid, "inbounds": updated_ids}
                    )
                    actual = [item.get("uuid") for item in (result or {}).get("inbounds", []) if isinstance(item, dict)]
                    if result.get("uuid") != squad_uuid or actual != updated_ids:
                        raise InstallError("panel_squad", "Изменение состава сквада не подтверждено")
                    squads[squad_uuid] = updated_ids
                    await self._save(artifacts)
            artifacts["squad_uuid"] = desired
            await self._save(artifacts)

    async def add_to_squad(self, spec: InstallSpec, artifacts: dict) -> None:
        """Дополняет выбранный сквад, сохраняя его инбаунды."""
        await self._ready()
        if not spec.squad_uuid or spec.role == "exit":
            return
        squad_uuid = _uuid(spec.squad_uuid, "сквад")
        inbound_uuid = _uuid(artifacts.get("inbound_uuid"), "инбаунд")
        panel_key = str(getattr(self.api, "base_url", spec.panel_ref)).rstrip("/")
        lock = _SQUAD_LOCKS.setdefault((panel_key, squad_uuid), asyncio.Lock())
        async with lock:
            squad = await self._call("GET", f"/internal-squads/{squad_uuid}")
            if not isinstance(squad, dict) or not isinstance(squad.get("inbounds"), list):
                raise InstallError("panel_host", "Панель не вернула инбаунды выбранного сквада")
            current = [
                _uuid(item.get("uuid"), "инбаунд сквада") for item in squad["inbounds"] if isinstance(item, dict)
            ]
            if len(current) != len(squad["inbounds"]):
                raise InstallError("panel_host", "Панель вернула некорректные инбаунды сквада")
            if inbound_uuid not in current:
                await self._save(artifacts)
                updated = await self._call(
                    "PATCH", "/internal-squads", {"uuid": squad_uuid, "inbounds": [*current, inbound_uuid]}
                )
                confirmed = {item.get("uuid") for item in (updated or {}).get("inbounds", []) if isinstance(item, dict)}
                if not {*current, inbound_uuid}.issubset(confirmed):
                    raise InstallError("panel_host", "Добавление инбаунда в сквад не подтверждено")
            artifacts["squad_uuid"] = squad_uuid
            await self._save(artifacts)

    async def check_online(self, spec: InstallSpec, artifacts: dict) -> bool:
        """Подтверждает подключение ноды с нужным профилем."""
        await self._ready()
        node = await self._call("GET", f"/nodes/{_uuid(artifacts.get('node_uuid'), 'нода')}")
        self._validate_node(spec, artifacts, node)
        versions = node.get("versions") or {}
        return bool(
            node.get("isConnected") is True
            and node.get("isDisabled") is False
            and node.get("isConnecting") is False
            and versions.get("node") == NODE_VERSION
            and versions.get("xray")
        )

    async def ensure_cascade(
        self,
        exit_spec: InstallSpec,
        exit_artifacts: dict,
        artifacts: dict,
        checkpoint: Checkpoint | None = None,
    ) -> dict:
        """Создаёт отдельный доступ только к выходу каскада."""
        if exit_spec.role != "exit" or exit_spec.transport not in {"vless_tcp_reality", "vless_tcp_selfsteal"}:
            raise InstallError("cascade", "Для выхода каскада нужна выходная нода TCP Reality", retryable=False)
        if not all(exit_artifacts.get(name) for name in ("inbound_uuid", "public_key", "short_id", "node_uuid")):
            raise InstallError("cascade", "Параметры выходной ноды неполны", retryable=False)
        if not await self._same_panel(exit_spec):
            async with InstallerPanel(exit_spec) as panel:
                return await panel._create_cascade(exit_spec, exit_artifacts, artifacts, checkpoint or self.checkpoint)
        return await self._create_cascade(exit_spec, exit_artifacts, artifacts, checkpoint)

    async def _same_panel(self, spec: InstallSpec) -> bool:
        """Сравнивает настроенные адреса панелей."""
        if spec.panel_ref == self.spec.panel_ref:
            return True
        return await _resolve_panel_url(spec.panel_ref) == await _resolve_panel_url(self.spec.panel_ref)

    async def _create_cascade(
        self, exit_spec: InstallSpec, exit_artifacts: dict, artifacts: dict, checkpoint: Checkpoint | None
    ) -> dict:
        """Создаёт приватный сквад и технического пользователя."""
        await self._ready()
        cascade = artifacts.setdefault("cascade", {})
        if not cascade.get("nonce"):
            cascade["nonce"] = uuid.uuid4().hex[:22]
            cascade["uuid"] = str(uuid.uuid4())
        provenance = {
            "panel_ref": exit_spec.panel_ref,
            "exit_install_id": exit_artifacts.get("install_id"),
            "inbound_uuid": _uuid(exit_artifacts["inbound_uuid"], "выходной инбаунд"),
        }
        if any(cascade.get(key) not in (None, value) for key, value in provenance.items()):
            raise InstallError("cascade", "Выход сохранённого каскада изменился вне установщика", retryable=False)
        cascade.update(provenance)
        await self._save(artifacts, checkpoint)
        name = "cascade_" + cascade["nonce"]
        inbound_uuid = _uuid(exit_artifacts["inbound_uuid"], "выходной инбаунд")
        squad = None
        if cascade.get("squad_uuid"):
            squad = await self._call("GET", f"/internal-squads/{_uuid(cascade['squad_uuid'], 'сквад каскада')}")
        else:
            for item in await self.list_squads():
                if item.get("name") == name:
                    squad = item
                    break
            if squad is None:
                await self._save(artifacts, checkpoint)
                squad = await self._call("POST", "/internal-squads", {"name": name, "inbounds": [inbound_uuid]})
            cascade["squad_uuid"] = _uuid((squad or {}).get("uuid"), "сквад каскада")
            await self._save(artifacts, checkpoint)
        if (
            not isinstance(squad, dict)
            or squad.get("name") != name
            or [item.get("uuid") for item in squad.get("inbounds", [])] != [inbound_uuid]
        ):
            raise InstallError("cascade", "Приватный сквад каскада изменён; доступ не расширен", retryable=False)
        user = await self._call("GET", f"/users/by-username/{quote(name, safe='')}", missing=True)
        if user is None:
            await self._save(artifacts, checkpoint)
            user = await self._call(
                "POST",
                "/users",
                {
                    "username": name,
                    "vlessUuid": cascade["uuid"],
                    "expireAt": "2099-01-01T00:00:00.000Z",
                    "status": "ACTIVE",
                    "trafficLimitBytes": 0,
                    "trafficLimitStrategy": "NO_RESET",
                    "activeInternalSquads": [cascade["squad_uuid"]],
                    "description": "Remnawave node installer: private cascade",
                },
            )
            cascade["user_id"] = user.get("id") if isinstance(user, dict) else None
            await self._save(artifacts, checkpoint)
        squads = user.get("activeInternalSquads") if isinstance(user, dict) else None
        squad_ids = [item.get("uuid") if isinstance(item, dict) else item for item in squads or []]
        if (
            not isinstance(user, dict)
            or type(user.get("id")) is not int
            or user.get("vlessUuid") != cascade["uuid"]
            or user.get("username") != name
            or squad_ids != [cascade["squad_uuid"]]
            or user.get("externalSquadUuid")
            or user.get("status") != "ACTIVE"
        ):
            raise InstallError("cascade", "Технический пользователь каскада не подтверждён", retryable=False)
        cascade.update(
            user_id=user["id"],
            address=str(ipaddress.ip_address(exit_spec.address)),
            port=exit_spec.client_port,
            public_key=exit_artifacts["public_key"],
            short_id=exit_artifacts["short_id"],
            server_name=exit_spec.domain if exit_spec.transport == "vless_tcp_selfsteal" else exit_spec.reality_domain,
        )
        await self._save(artifacts, checkpoint)
        return cascade

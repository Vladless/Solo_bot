import ipaddress
import json
import time

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
import py3xui

from py3xui import AsyncApi, Client, Inbound

from database.access.resolution import UserId
from logger import logger
from settings.config import ADMIN_PASSWORD, ADMIN_USERNAME, USE_XUI_TOKEN, XUI_TOKEN


@dataclass
class ClientConfig:
    """Хранит параметры добавления и обновления клиента."""

    client_id: str
    email: str
    tg_id: int | str | None
    limit_ip: int
    total_gb: int
    expiry_time: int
    enable: bool
    inbound_id: int
    sub_id: str


_xui_instance_cache: dict[tuple[str, str, str | None], tuple[AsyncApi, float]] = {}
SESSION_TTL = 1800


_inbound_cache: dict[str, tuple[Inbound, float]] = {}
INBOUND_CACHE_TTL = SESSION_TTL


def _resolve_flow(inbound: Inbound) -> str:
    """Определяет режим VLESS по защите и транспорту входящего подключения."""
    ss = _stream_settings_dict(inbound)
    security = (ss.get("security") or "").lower()
    network = (ss.get("network") or "").lower()
    if security in ("reality", "tls") and network in ("tcp", "raw"):
        return "xtls-rprx-vision"
    return ""


def _stream_settings_dict(inbound: Inbound) -> dict[str, Any]:
    ss = getattr(inbound, "stream_settings", None)
    if ss is None:
        return {}
    if isinstance(ss, str):
        try:
            return json.loads(ss) if ss else {}
        except json.JSONDecodeError:
            return {}
    if isinstance(ss, dict):
        return ss
    if hasattr(ss, "model_dump"):
        return ss.model_dump(by_alias=True)
    return {}


def _client_identity(client: Client | None) -> str | None:
    if not client:
        return None
    for value in (getattr(client, "uuid", None), getattr(client, "id", None)):
        if isinstance(value, str) and value.strip() and not value.isdecimal():
            return str(value)
    return None


def _telegram_id(value: int | str | None) -> int:
    if isinstance(value, UserId) or value in (None, ""):
        return 0
    tg_id = int(value)
    return tg_id if 0 < tg_id <= 2**63 - 1 else 0


async def _panel_request(xui: AsyncApi, endpoint: str, *, method: str = "GET", data: dict | None = None) -> Any:
    """Выполняет запрос к API панели с настроенной авторизацией."""
    api = xui.client
    kwargs: dict[str, Any] = {"timeout": 10.0}
    if data is not None:
        kwargs["json"] = data
    response = await api._request_with_retry(
        method, api._url(endpoint), {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}, **kwargs
    )
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError(f"3x-ui: {payload.get('msg') if isinstance(payload, dict) else 'invalid response'}")
    return payload.get("obj")


async def _client_snapshot(xui: AsyncApi, inbound_id: int, email: str, client_id: str) -> tuple[dict, list[int]]:
    """Проверяет клиента и подготавливает данные для обновления."""
    obj = await _panel_request(xui, f"panel/api/clients/get/{quote(email, safe='')}")
    if not isinstance(obj, dict) or not isinstance(obj.get("client"), dict):
        raise ValueError(f"Клиент {email} не найден")
    record = obj["client"]
    inbound_ids = obj.get("inboundIds")
    if not isinstance(inbound_ids, list) or int(inbound_id) not in inbound_ids:
        raise ValueError(f"Клиент {email} не привязан к inbound {inbound_id}")
    if record.get("email") != email or record.get("uuid") != client_id:
        raise ValueError(f"UUID клиента {email} не совпадает с ожидаемым")

    client = dict(record)
    client["id"] = client.pop("uuid")
    client["tgId"] = _telegram_id(client.get("tgId"))
    for old, new in (("createdAt", "created_at"), ("updatedAt", "updated_at")):
        if old in client:
            client[new] = client.pop(old)
    reverse = client.get("reverse")
    if isinstance(reverse, str):
        client["reverse"] = json.loads(reverse) if reverse else None
    for field in (
        "privateKey",
        "publicKey",
        "allowedIPs",
        "allowedIPsByInbound",
        "preSharedKey",
        "keepAlive",
        "forwardedPorts",
    ):
        client.pop(field, None)
    return client, inbound_ids


async def _update_client(xui: AsyncApi, old_email: str, client: dict) -> None:
    endpoint = f"panel/api/clients/update/{quote(old_email, safe='')}"
    await _panel_request(xui, endpoint, method="POST", data=client)
    prefix = f"{id(xui)}|"
    for key in list(_inbound_cache):
        if key.startswith(prefix):
            _inbound_cache.pop(key, None)


async def reset_client_traffic(xui: AsyncApi, inbound_id: int, email: str, client_id: str) -> bool:
    """Сбрасывает трафик проверенного клиента в обеих версиях API."""
    legacy = False
    try:
        await _client_snapshot(xui, inbound_id, email, client_id)
    except httpx.HTTPStatusError as error:
        if error.response.status_code != 404:
            raise
        inbound = await xui.inbound.get_by_id(int(inbound_id))
        clients = getattr(getattr(inbound, "settings", None), "clients", None)
        matches = [client for client in clients or [] if getattr(client, "email", None) == email]
        if len(matches) != 1 or _client_identity(matches[0]) != client_id:
            raise ValueError(f"UUID клиента {email} не совпадает с ожидаемым") from error
        legacy = True
    if not legacy:
        try:
            await _panel_request(xui, f"panel/api/clients/resetTraffic/{quote(email, safe='')}", method="POST")
            return True
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 404:
                raise
    await _panel_request(
        xui,
        f"panel/api/inbounds/{int(inbound_id)}/resetClientTraffic/{quote(email, safe='')}",
        method="POST",
        data={},
    )
    return True


async def _get_inbound_cached(xui: AsyncApi, inbound_id: int) -> Inbound:
    """Возвращает inbound из кэша или запрашивает его в панели."""
    key = f"{id(xui)}|{inbound_id}"
    now = time.time()
    cached = _inbound_cache.get(key)
    if cached and (now - cached[1] < INBOUND_CACHE_TTL):
        return cached[0]
    inbound = await xui.inbound.get_by_id(int(inbound_id))
    _inbound_cache[key] = (inbound, now)
    return inbound


_NODES_CACHE: dict[str, tuple[list[dict], float]] = {}
NODES_CACHE_TTL = 60


def _is_routable_host(value: str) -> bool:
    """Проверяет пригодность адреса для клиентской ссылки."""
    host = (value or "").strip()
    if not host or host[0] in ("@", "/"):
        return False
    if host.lower() == "localhost":
        return False
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return True
    return not (address.is_unspecified or address.is_loopback)


async def _panel_get(xui: AsyncApi, api_url: str, endpoint: str) -> Any | None:
    """Получает данные панели или возвращает None при ошибке."""
    try:
        return await _panel_request(xui, endpoint)
    except Exception as error:
        logger.debug(f"[XUI] Ручка {endpoint} недоступна: {error}")
        return None


async def get_panel_nodes(xui: AsyncApi, api_url: str) -> list[dict]:
    """Возвращает список узлов мастер-панели 3x-ui."""
    now = time.time()
    cached = _NODES_CACHE.get(api_url)
    if cached and now - cached[1] < NODES_CACHE_TTL:
        return cached[0]
    obj = await _panel_get(xui, api_url, "panel/api/nodes/list")
    nodes = [node for node in obj if isinstance(node, dict)] if isinstance(obj, list) else []
    _NODES_CACHE[api_url] = (nodes, now)
    return nodes


async def get_inbound_raw(xui: AsyncApi, api_url: str, inbound_id: int) -> dict | None:
    """Возвращает исходные данные входящего подключения панели."""
    obj = await _panel_get(xui, api_url, f"panel/api/inbounds/get/{int(inbound_id)}")
    return obj if isinstance(obj, dict) else None


async def get_inbound_node(xui: AsyncApi, api_url: str, inbound_id: int) -> dict | None:
    """Возвращает узел входящего подключения."""
    raw = await get_inbound_raw(xui, api_url, inbound_id)
    if not raw:
        return None
    node_id = raw.get("nodeId")
    if node_id is None:
        return None
    for node in await get_panel_nodes(xui, api_url):
        if node.get("id") == node_id:
            return node
    return None


async def resolve_inbound_host(xui: AsyncApi, api_url: str, inbound_id: int, fallback_host: str) -> str:
    """Выбирает адрес ссылки по настройкам входящего подключения и узла."""
    raw = await get_inbound_raw(xui, api_url, inbound_id)
    if not raw:
        return fallback_host

    node_host = ""
    node_id = raw.get("nodeId")
    if node_id is not None:
        for node in await get_panel_nodes(xui, api_url):
            if node.get("id") == node_id:
                node_host = str(node.get("address") or "").strip()
                break

    listen = str(raw.get("listen") or "").strip()
    listen_host = listen if _is_routable_host(listen) else ""
    custom_host = str(raw.get("shareAddr") or "").strip()

    strategy = str(raw.get("shareAddrStrategy") or "node").strip().lower()
    if strategy == "listen":
        candidates = [listen_host, node_host]
    elif strategy == "custom":
        candidates = [custom_host, node_host, listen_host]
    else:
        candidates = [node_host, listen_host]

    for candidate in candidates:
        if candidate:
            return candidate
    return fallback_host


async def get_xui_instance(api_url: str) -> AsyncApi:
    token = XUI_TOKEN if USE_XUI_TOKEN else None
    if USE_XUI_TOKEN and not token:
        raise ValueError("USE_XUI_TOKEN включён, но XUI_TOKEN не задан")
    key = (api_url, ADMIN_USERNAME, token)
    current_time = time.time()

    xui_entry = _xui_instance_cache.get(key)
    if xui_entry:
        xui, last_login = xui_entry
        if token is not None or current_time - last_login < SESSION_TTL:
            return xui
        else:
            logger.info("[XUI Cache] Сессия устарела (>30 минут), переподключение...")
            await xui.login()
            _xui_instance_cache[key] = (xui, current_time)
            return xui

    xui = AsyncApi(
        api_url,
        ADMIN_USERNAME,
        ADMIN_PASSWORD,
        token=token,
        logger=logger,
    )
    if token is None:
        await xui.login()
    _xui_instance_cache[key] = (xui, current_time)
    return xui


async def check_xui_connection(api_url: str) -> None:
    """Проверяет доступность API панели с настроенной авторизацией."""
    xui = await get_xui_instance(api_url)
    await _panel_request(xui, "panel/api/inbounds/list")


async def add_client(xui: py3xui.AsyncApi, config: ClientConfig) -> dict[str, Any] | None:
    try:
        inbound = await _get_inbound_cached(xui, config.inbound_id)
        flow = _resolve_flow(inbound)
        client = {
            "id": config.client_id,
            "email": config.email.lower(),
            "limitIp": config.limit_ip if config.limit_ip is not None else 0,
            "totalGB": config.total_gb,
            "expiryTime": config.expiry_time,
            "enable": config.enable,
            "tgId": _telegram_id(config.tg_id),
            "subId": config.sub_id,
            "flow": flow,
        }
        await _panel_request(
            xui, "panel/api/clients/add", method="POST", data={"client": client, "inboundIds": [int(config.inbound_id)]}
        )
        _inbound_cache.pop(f"{id(xui)}|{config.inbound_id}", None)
        logger.info(f"Клиент {config.email} успешно добавлен с ID {config.client_id} (flow={flow!r})")
        return {"status": "success", "email": config.email, "client_id": config.client_id}

    except httpx.ConnectTimeout as e:
        logger.error(f"Ошибка при добавлении клиента {config.email}: {e}")
        return None

    except Exception as e:
        error_message = str(e)
        if "duplicate email" in error_message.lower() or "email already in use" in error_message.lower():
            logger.warning(f"Дублированный email: {config.email}. Пропуск. Сообщение: {error_message}")
            return {"status": "duplicate", "email": config.email}

        logger.error(f"Ошибка при добавлении клиента {config.email}: {error_message}")
        return None


async def extend_client_key(
    xui: py3xui.AsyncApi,
    inbound_id: int,
    email: str,
    new_expiry_time: int,
    client_id: str,
    total_gb: int,
    sub_id: str,
    tg_id: int,
    limit_ip: int = 0,
) -> bool | None:
    try:
        await reset_client_traffic(xui, inbound_id, email, client_id)
        client, _ = await _client_snapshot(xui, inbound_id, email, client_id)
        client.update(
            expiryTime=new_expiry_time,
            subId=sub_id,
            totalGB=total_gb,
            enable=True,
            limitIp=limit_ip,
            tgId=_telegram_id(tg_id),
        )
        await _update_client(xui, email, client)
        logger.info(f"Ключ клиента {email} успешно продлён до {new_expiry_time}")
        return True

    except httpx.ConnectTimeout as e:
        logger.error(f"Ошибка при обновлении клиента {email}: {e}")
        return False

    except Exception as e:
        logger.error(f"Ошибка при обновлении клиента с email {email}: {e}")
        return False


async def _client_is_detached(xui: AsyncApi, inbound_id: int, email: str, client_id: str) -> bool:
    """Подтверждает отсутствие клиента или его связи с входящим подключением."""
    rows = await _panel_request(xui, "panel/api/clients/list")
    if not isinstance(rows, list):
        return False
    match = None
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("email"), str) or not isinstance(row.get("uuid"), str):
            return False
        if "inboundIds" not in row:
            return False
        links = row.get("inboundIds")
        if links is not None and (
            not isinstance(links, list) or any(type(value) is not int or value <= 0 for value in links)
        ):
            return False
        if row["email"] == email or row["uuid"] == client_id:
            if row["email"] != email or row["uuid"] != client_id or match is not None:
                return False
            match = row
    return match is None or int(inbound_id) not in (match.get("inboundIds") or [])


async def delete_client(
    xui: py3xui.AsyncApi,
    inbound_id: int,
    email: str,
    client_id: str,
) -> bool:
    """Отсоединяет клиента и подтверждает отсутствие его связи."""
    try:
        try:
            obj = await _panel_request(xui, f"panel/api/clients/get/{quote(email, safe='')}")
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 404 or not await _client_is_detached(xui, inbound_id, email, client_id):
                return False
            _inbound_cache.pop(f"{id(xui)}|{inbound_id}", None)
            return True
        except json.JSONDecodeError:
            return False
        except ValueError:
            if not await _client_is_detached(xui, inbound_id, email, client_id):
                return False
            _inbound_cache.pop(f"{id(xui)}|{inbound_id}", None)
            return True
        if not isinstance(obj, dict) or not isinstance(obj.get("client"), dict):
            return False
        if "inboundIds" not in obj:
            return False
        record, inbound_ids = obj["client"], obj.get("inboundIds")
        if record.get("email") != email or record.get("uuid") != client_id:
            return False
        if inbound_ids is not None and (
            not isinstance(inbound_ids, list) or any(type(value) is not int or value <= 0 for value in inbound_ids)
        ):
            return False
        if int(inbound_id) not in (inbound_ids or []):
            _inbound_cache.pop(f"{id(xui)}|{inbound_id}", None)
            return True
        await _panel_request(
            xui,
            f"panel/api/clients/{quote(email, safe='')}/detach",
            method="POST",
            data={"inboundIds": [int(inbound_id)]},
        )
        _inbound_cache.pop(f"{id(xui)}|{inbound_id}", None)
        logger.info(f"Клиент с ID {client_id} удалён из inbound {inbound_id}")
        return True
    except Exception as error:
        logger.error(f"Ошибка при удалении клиента с ID {client_id}: {error}")
        return False


_LEGACY_TRAFFIC_ENDPOINT = "panel/api/inbounds/getClientTrafficsById/{key}"
_MODERN_TRAFFIC_ENDPOINT = "panel/api/clients/traffic/{key}"


def _traffic_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        return [payload]
    return [row for row in (payload or []) if isinstance(row, dict)]


async def _fetch_traffic(xui: py3xui.AsyncApi, endpoint: str, key: str) -> list[dict[str, Any]] | None:
    """Получает трафик; при HTTP 404 возвращает None."""
    try:
        obj = await _panel_request(xui, endpoint.format(key=quote(key, safe="")))
        return _traffic_rows(obj)
    except httpx.HTTPStatusError as error:
        if error.response.status_code == 404:
            return None
        raise


async def get_client_traffic(xui: py3xui.AsyncApi, client_id: str, email: str | None = None) -> dict[str, Any]:
    try:
        if email is None:
            try:
                obj = await _panel_request(xui, "panel/api/clients/list")
            except httpx.HTTPStatusError as error:
                if error.response.status_code != 404:
                    raise
                obj = None
            matching = [row for row in _traffic_rows(obj) if row.get("uuid") == client_id]
            if len(matching) == 1:
                email = matching[0].get("email")
        rows = await _fetch_traffic(xui, _MODERN_TRAFFIC_ENDPOINT, email) if email else None
        if rows is None:
            rows = await _fetch_traffic(xui, _LEGACY_TRAFFIC_ENDPOINT, client_id)
        if rows is None:
            raise ValueError("Маршруты получения трафика недоступны")
        if any(row.get("uuid") and row["uuid"] != client_id for row in rows):
            raise ValueError("UUID в статистике клиента не совпадает с ожидаемым")

        if not rows:
            logger.warning(f"Трафик для клиента {client_id} не найден.")
            return {"status": "not_found", "client_id": client_id}

        used_bytes = sum(int(row.get("up") or 0) + int(row.get("down") or 0) for row in rows)
        logger.info(f"Трафик для клиента {client_id} успешно получен: {used_bytes} байт.")
        return {"status": "success", "client_id": client_id, "used_bytes": used_bytes, "traffic": rows}

    except httpx.ConnectTimeout as e:
        logger.error(f"Ошибка при получении трафика клиента {client_id}: {e}")
        return {"status": "error", "error": "Timeout"}

    except Exception as e:
        logger.error(f"Ошибка при получении трафика клиента {client_id}: {e}")
        return {"status": "error", "error": str(e)}


async def toggle_client(
    xui: py3xui.AsyncApi,
    inbound_id: int,
    email: str,
    client_id: str,
    enable: bool = True,
) -> bool:
    try:
        client, _ = await _client_snapshot(xui, inbound_id, email, client_id)
        client["enable"] = enable
        await _update_client(xui, email, client)
        status = "включен" if enable else "отключен"
        logger.info(f"Клиент с email {email} и ID {client_id} успешно {status}.")
        return True

    except httpx.ConnectTimeout as e:
        status = "включении" if enable else "отключении"
        logger.error(f"Ошибка при {status} клиента с email {email} и ID {client_id}: {e}")
        return False

    except Exception as e:
        status = "включении" if enable else "отключении"
        logger.error(f"Ошибка при {status} клиента с email {email} и ID {client_id}: {e}")
        return False


async def change_client_email(
    xui: py3xui.AsyncApi,
    inbound_id: int,
    old_email: str,
    new_email: str,
    new_sub_id: str,
    client_id: str,
) -> bool:
    """Меняет имя и subId клиента без удаления записи."""
    try:
        try:
            client, _ = await _client_snapshot(xui, inbound_id, old_email, client_id)
        except ValueError:
            client, _ = await _client_snapshot(xui, inbound_id, new_email.lower(), client_id)
            if client.get("subId") != new_sub_id:
                raise ValueError("subId уже переименованного клиента не совпадает с ожидаемым") from None
            return True
        client.update(email=new_email.lower(), subId=new_sub_id)
        await _update_client(xui, old_email, client)
        logger.info(f"Ссылка сменена: {old_email} → {new_email} (ID {client_id}).")
        return True

    except Exception as e:
        logger.error(f"Ошибка смены email {old_email} → {new_email} (ID {client_id}): {e}")
        return False


def build_vless_link_from_inbound(
    inbound: py3xui.Inbound,
    user_uuid: str,
    email: str,
    external_host: str,
    port: int,
    remark: str | None = None,
    client_flow: str | None = None,
) -> str:
    name = remark or email
    stream = _stream_settings_dict(inbound)
    security = (stream.get("security") or "").lower()
    network = (stream.get("network") or "").lower()

    def _first(val):
        if isinstance(val, list) and val:
            return val[0]
        return val or ""

    rs = stream.get("realitySettings") or {}
    rs_settings = rs.get("settings") or {}

    pbk = rs_settings.get("publicKey") or rs.get("publicKey") or ""
    sni = _first(
        rs.get("serverNames") or rs_settings.get("serverNames") or rs.get("serverName") or rs_settings.get("serverName")
    )
    sid = _first(rs.get("shortIds") or rs_settings.get("shortIds") or rs.get("shortId") or rs_settings.get("shortId"))
    fp = rs.get("fingerprint") or rs_settings.get("fingerprint") or ""

    if security == "reality" and network in ("tcp", "raw"):
        parts = [
            f"vless://{user_uuid}@{external_host}:{port}",
            "?type=tcp&security=reality",
            f"&pbk={pbk}" if pbk else "",
            f"&fp={fp}" if fp else "",
            f"&sni={sni}" if sni else "",
            f"&sid={sid}" if sid else "",
            "&spx=%2F",
            f"&flow={client_flow}" if client_flow else "",
            f"#{name}",
        ]
        return "".join(parts)

    if network == "ws":
        ws = stream.get("wsSettings") or {}
        path = (ws.get("path") or "/").strip() or "/"
        host_hdr = external_host
        if security == "tls":
            parts = [
                f"vless://{user_uuid}@{external_host}:{port}",
                "?type=ws&security=tls",
                f"&host={host_hdr}",
                f"&sni={external_host}",
                f"&path={path}",
                f"#{name}",
            ]
            return "".join(parts)
        return f"vless://{user_uuid}@{external_host}:{port}?type=ws&path={path}#{name}"

    if security == "tls":
        return f"vless://{user_uuid}@{external_host}:{port}?type=tcp&security=tls&sni={external_host}#{name}"

    return f"vless://{user_uuid}@{external_host}:{port}?type=tcp#{name}"


async def get_vless_link_for_client(
    xui: py3xui.AsyncApi,
    inbound_id: int,
    email: str,
    external_host: str,
    port: int,
    remark: str | None = None,
) -> str | None:
    try:
        inbound = await xui.inbound.get_by_id(inbound_id)
        if not inbound:
            logger.warning(f"Не удалось собрать VLESS ссылку: inbound_id={inbound_id}, email={email}")
            return None

        true_uuid = None
        client_flow = None
        if getattr(inbound, "settings", None) and getattr(inbound.settings, "clients", None):
            for c in inbound.settings.clients:
                if getattr(c, "email", None) == email:
                    true_uuid = _client_identity(c)
                    client_flow = getattr(c, "flow", None)
                    break

        if not true_uuid:
            logger.warning(f"Не удалось получить UUID клиента: inbound_id={inbound_id}, email={email}")
            return None

        return build_vless_link_from_inbound(
            inbound,
            true_uuid,
            email,
            external_host,
            port,
            remark,
            client_flow,
        )
    except Exception as e:
        logger.error(f"Ошибка при сборке VLESS ссылки: {e}")
        return None

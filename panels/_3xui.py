import json
import time

from dataclasses import dataclass
from typing import Any

import httpx
import py3xui

from py3xui import AsyncApi, Client, Inbound

from logger import logger
from settings.config import ADMIN_PASSWORD, ADMIN_USERNAME, SUPERNODE, USE_XUI_TOKEN, XUI_TOKEN


@dataclass
class ClientConfig:
    """Конфигурация клиента для добавления/обновления."""

    client_id: str
    email: str
    tg_id: str
    limit_ip: int
    total_gb: int
    expiry_time: int
    enable: bool
    inbound_id: int
    sub_id: str


_xui_instance_cache: dict[str, tuple[AsyncApi, float]] = {}
SESSION_TTL = 1800


_inbound_cache: dict[str, tuple[Inbound, float]] = {}
INBOUND_CACHE_TTL = SESSION_TTL


def _resolve_flow(inbound: Inbound) -> str:
    """VLESS flow по stream_settings: vision только для (reality|tls)+tcp, иначе пусто."""
    ss = getattr(inbound, "stream_settings", None)
    if not ss or isinstance(ss, str):
        return ""
    security = (getattr(ss, "security", "") or "").lower()
    network = (getattr(ss, "network", "") or "").lower()
    if security in ("reality", "tls") and network == "tcp":
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
    for value in (client.uuid, client.id):
        if value is not None and str(value).strip():
            return str(value)
    return None


def _apply_client_identity(client: Client, client_id: str) -> None:
    client.id = client_id
    client.uuid = client_id


def _build_client(config: ClientConfig, flow: str) -> Client:
    return Client(
        id=config.client_id,
        uuid=config.client_id,
        email=config.email.lower(),
        limit_ip=config.limit_ip if config.limit_ip is not None else 0,
        total_gb=config.total_gb,
        expiry_time=config.expiry_time,
        enable=config.enable,
        tg_id=config.tg_id,
        sub_id=config.sub_id,
        flow=flow,
    )


async def _get_inbound_cached(xui: AsyncApi, inbound_id: int) -> Inbound:
    """Inbound с TTL-кэшем по ключу (xui, inbound_id)."""
    key = f"{id(xui)}|{inbound_id}"
    now = time.time()
    cached = _inbound_cache.get(key)
    if cached and (now - cached[1] < INBOUND_CACHE_TTL):
        return cached[0]
    inbound = await xui.inbound.get_by_id(int(inbound_id))
    _inbound_cache[key] = (inbound, now)
    return inbound


async def get_xui_instance(api_url: str) -> AsyncApi:
    key = f"{api_url}|{ADMIN_USERNAME}"
    current_time = time.time()

    xui_entry = _xui_instance_cache.get(key)
    if xui_entry:
        xui, last_login = xui_entry
        if current_time - last_login < SESSION_TTL:
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
        token=XUI_TOKEN if USE_XUI_TOKEN else None,
        logger=logger,
    )
    await xui.login()
    _xui_instance_cache[key] = (xui, current_time)
    return xui


async def add_client(xui: py3xui.AsyncApi, config: ClientConfig) -> dict[str, Any] | None:
    try:
        inbound = await _get_inbound_cached(xui, config.inbound_id)
        flow = _resolve_flow(inbound)
        client = _build_client(config, flow)

        await xui.client.add(config.inbound_id, [client])
        logger.info(f"Клиент {config.email} успешно добавлен с ID {config.client_id} (flow={flow!r})")
        return {"status": "success", "email": config.email, "client_id": config.client_id}

    except httpx.ConnectTimeout as e:
        logger.error(f"Ошибка при добавлении клиента {config.email}: {e}")
        return None

    except Exception as e:
        error_message = str(e)
        if "Duplicate email" in error_message:
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
        client = await xui.client.get_by_email(email)
        if not client or not _client_identity(client):
            logger.warning(f"Клиент с email {email} не найден или не имеет ID.")
            return None

        logger.info(f"Обновление ключа клиента {email} с ID {client.id} до {new_expiry_time}")

        inbound = await _get_inbound_cached(xui, inbound_id)
        flow = _resolve_flow(inbound)

        _apply_client_identity(client, client_id)
        client.expiry_time = new_expiry_time
        client.flow = flow
        client.sub_id = sub_id
        client.total_gb = total_gb
        client.enable = True
        client.limit_ip = limit_ip
        client.inbound_id = inbound_id
        client.tg_id = tg_id

        await xui.client.update(client_id, client)
        await xui.client.reset_stats(inbound_id, email)
        logger.info(f"Ключ клиента {email} успешно продлён до {new_expiry_time} (flow={flow!r})")
        return True

    except httpx.ConnectTimeout as e:
        logger.error(f"Ошибка при обновлении клиента {email}: {e}")
        return False

    except Exception as e:
        logger.error(f"Ошибка при обновлении клиента с email {email}: {e}")
        return False


async def delete_client(
    xui: py3xui.AsyncApi,
    inbound_id: int,
    email: str,
    client_id: str,
) -> bool:
    try:
        if SUPERNODE:
            await xui.client.delete(inbound_id, client_id)
            logger.info(f"Клиент с ID {client_id} был удален успешно (SUPERNODE)")
            return True

        client = await xui.client.get_by_email(email)
        if not client:
            logger.warning(f"Клиент с email {email} и ID {client_id} не найден")
            return False

        await xui.client.delete(inbound_id, client_id)
        logger.info(f"Клиент с ID {client_id} был удален успешно")
        return True

    except httpx.ConnectTimeout as e:
        logger.error(f"Ошибка при удалении клиента {email}: {e}")
        return False

    except Exception as e:
        logger.error(f"Ошибка при удалении клиента с ID {client_id}: {e}")
        return False


_LEGACY_TRAFFIC_ENDPOINT = "panel/api/inbounds/getClientTrafficsById/{key}"
_MODERN_TRAFFIC_ENDPOINT = "panel/api/clients/traffic/{key}"
_modern_panels: set[str] = set()


def _traffic_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        return [payload]
    return [row for row in (payload or []) if isinstance(row, dict)]


async def _fetch_traffic(xui: py3xui.AsyncApi, endpoint: str, key: str) -> list[dict[str, Any]] | None:
    """None — маршрут не отвечает данными: панель другого поколения."""
    api = xui.client
    try:
        response = await api._get(api._url(endpoint.format(key=key)), {"Accept": "application/json"})
        body = response.json()
    except Exception:
        return None
    if not isinstance(body, dict) or not body.get("success"):
        return None
    return _traffic_rows(body.get("obj"))


async def get_client_traffic(xui: py3xui.AsyncApi, client_id: str, email: str | None = None) -> dict[str, Any]:
    try:
        host = str(getattr(xui.client, "_host", "") or "")
        rows = None

        if email and host in _modern_panels:
            rows = await _fetch_traffic(xui, _MODERN_TRAFFIC_ENDPOINT, email)
        if rows is None:
            rows = await _fetch_traffic(xui, _LEGACY_TRAFFIC_ENDPOINT, client_id)
            if rows is not None:
                _modern_panels.discard(host)
        if rows is None and email:
            rows = await _fetch_traffic(xui, _MODERN_TRAFFIC_ENDPOINT, email)
            if rows is not None:
                _modern_panels.add(host)
                logger.info(f"[XUI] Панель {host} отвечает по маршрутам 3.6, перешёл на них.")

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
        client = await xui.client.get_by_email(email)
        if not client:
            logger.warning(f"Клиент с email {email} и ID {client_id} не найден.")
            return False

        inbound = await _get_inbound_cached(xui, inbound_id)
        flow = _resolve_flow(inbound)

        client.sub_id = email
        client.enable = enable
        _apply_client_identity(client, client_id)
        client.flow = flow
        client.limit_ip = 0
        client.inbound_id = inbound_id

        await xui.client.update(client_id, client)
        status = "включен" if enable else "отключен"
        logger.info(f"Клиент с email {email} и ID {client_id} успешно {status} (flow={flow!r}).")
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
    """Меняет email/ссылку клиента: удаляет старого и создаёт нового с тем же UUID.

    3x-ui не умеет переименовывать email через updateClient (матчит по email → record not found),
    поэтому delete+add. UUID (client_id) сохраняется → активные конфиги юзера продолжают работать,
    срок и квота переносятся.
    """
    try:
        client = await xui.client.get_by_email(old_email)
        if not client or not _client_identity(client):
            logger.warning(f"Клиент {old_email} не найден для смены ссылки (ID {client_id}).")
            return False

        expiry_time = int(getattr(client, "expiry_time", 0) or 0)
        total_gb = int(getattr(client, "total_gb", 0) or 0)
        limit_ip = int(getattr(client, "limit_ip", 0) or 0)
        enable = bool(getattr(client, "enable", True))
        tg_id_val = getattr(client, "tg_id", "") or ""

        if not await delete_client(xui, inbound_id, old_email, client_id):
            logger.error(f"Не удалось удалить клиента {old_email} при смене ссылки (ID {client_id}).")
            return False

        result = await add_client(
            xui,
            ClientConfig(
                client_id=client_id,
                email=new_email,
                tg_id=tg_id_val,
                limit_ip=limit_ip,
                total_gb=total_gb,
                expiry_time=expiry_time,
                enable=enable,
                inbound_id=inbound_id,
                sub_id=new_sub_id,
            ),
        )
        if not result or result.get("status") not in ("success", "duplicate"):
            logger.error(f"Не удалось создать клиента {new_email} при смене ссылки (ID {client_id}).")
            return False

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

    if security == "reality" and network == "tcp":
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

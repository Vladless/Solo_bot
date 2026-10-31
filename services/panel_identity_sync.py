from sqlalchemy.ext.asyncio import AsyncSession

from database.access.resolution import panel_identity_fields
from database.keys import get_keys
from database.servers import get_servers
from logger import (
    CLOGGER as logger,
    PANEL_REMNA,
    PANEL_XUI,
)
from panels._3xui import _client_snapshot, _update_client, get_xui_instance
from settings.config import REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD, SUPERNODE


async def push_identity_to_panel(session: AsyncSession, legacy_ref: int) -> None:
    """Обновляет данные владельца подписок во внешних панелях."""
    from panels.remnawave_runtime import remnawave_api

    tg, email = await panel_identity_fields(session, legacy_ref)
    if tg is None and not email:
        return
    keys = await get_keys(session, legacy_ref)
    if not keys:
        return
    servers_map = await get_servers(session)

    by_api_url: dict[str, set[tuple[str, str]]] = {}
    xui_targets: set[tuple[str, int, str, str]] = set()
    for key in keys:
        server_id = getattr(key, "server_id", None)
        client_id = getattr(key, "client_id", None)
        if not client_id:
            continue
        cluster = servers_map.get(server_id)
        if not cluster:
            cluster = [s for lst in servers_map.values() for s in lst if s.get("server_name") == server_id]
        for s in cluster or []:
            panel_type = str(s.get("panel_type", "3x-ui")).lower()
            api_url = str(s.get("api_url") or "").strip().rstrip("/")
            key_email = str(getattr(key, "email", "") or "")
            if not api_url or not key_email:
                continue
            if panel_type == "remnawave":
                by_api_url.setdefault(api_url, set()).add((str(client_id), key_email))
            elif panel_type == "3x-ui" and s.get("inbound_id"):
                if SUPERNODE:
                    key_email = f"{key_email}_{str(s.get('server_name') or '').lower()}"
                try:
                    inbound_id = int(s["inbound_id"])
                    if inbound_id <= 0:
                        raise ValueError("Некорректный inbound")
                except (TypeError, ValueError):
                    logger.warning(f"{PANEL_XUI} sync identity: некорректный inbound ({api_url})")
                    continue
                xui_targets.add((api_url, inbound_id, str(client_id), key_email.lower()))

    synced_xui_clients: set[tuple[str, str, str]] = set()
    for api_url, inbound_id, client_id, key_email in sorted(xui_targets):
        target = (api_url, client_id, key_email)
        if target in synced_xui_clients:
            continue
        try:
            xui = await get_xui_instance(api_url)
            client, _ = await _client_snapshot(xui, inbound_id, key_email, client_id)
            if client.get("tgId") != (tg or 0):
                client["tgId"] = tg or 0
                await _update_client(xui, key_email, client)
            synced_xui_clients.add(target)
        except Exception as e:
            logger.warning(f"{PANEL_XUI} sync identity для {client_id} ({api_url}) не удался: {e}")

    for api_url, client_ids in by_api_url.items():
        try:
            async with remnawave_api(api_url) as remna:
                if not await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
                    logger.warning(f"{PANEL_REMNA} sync identity: не удалось войти ({api_url})")
                    continue
                for client_id, key_email in client_ids:
                    try:
                        await remna.update_user(
                            uuid=client_id,
                            lookup_username=key_email or None,
                            telegram_id=tg,
                            email=email or None,
                            status=None,
                            traffic_limit_bytes=None,
                            traffic_limit_strategy=None,
                        )
                    except Exception as e:
                        logger.debug(f"{PANEL_REMNA} sync identity для {client_id} не удался: {e}")
        except Exception as e:
            logger.debug(f"{PANEL_REMNA} sync identity ({api_url}) не удался: {e}")

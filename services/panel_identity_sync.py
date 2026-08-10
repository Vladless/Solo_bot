from sqlalchemy.ext.asyncio import AsyncSession

from database.access.resolution import panel_identity_fields
from database.keys import get_keys
from database.servers import get_servers
from logger import (
    CLOGGER as logger,
    PANEL_REMNA,
)
from settings.config import REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD


async def push_identity_to_panel(session: AsyncSession, legacy_ref: int) -> None:
    """Проставляет актуальные поля владельца (telegramId, email) во внешней Remnawave-панели.

    Вызывается после связывания аккаунтов, чтобы ключи, созданные до привязки, получили оба поля
    единой идентичности, не дожидаясь продления.
    """
    from panels.remnawave_runtime import remnawave_api
    tg, email = await panel_identity_fields(session, legacy_ref)
    if tg is None and not email:
        return
    keys = await get_keys(session, legacy_ref)
    if not keys:
        return
    servers_map = await get_servers(session)

    by_api_url: dict[str, set[tuple[str, str]]] = {}
    for key in keys:
        server_id = getattr(key, "server_id", None)
        client_id = getattr(key, "client_id", None)
        if not client_id:
            continue
        cluster = servers_map.get(server_id)
        if not cluster:
            cluster = [s for lst in servers_map.values() for s in lst if s.get("server_name") == server_id]
        for s in cluster or []:
            if str(s.get("panel_type", "3x-ui")).lower() != "remnawave":
                continue
            api_url = s.get("api_url")
            if api_url:
                by_api_url.setdefault(api_url, set()).add(
                    (str(client_id), str(getattr(key, "email", "") or ""))
                )

    if not by_api_url:
        return


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

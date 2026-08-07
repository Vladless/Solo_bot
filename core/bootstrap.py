from database import async_session_maker
from database.db import warm_pool
from database.settings_cache import settings_cache
from database.tariffs import initialize_all_tariff_weights
from logger import logger

from .settings.buttons_config import BUTTONS_CONFIG, load_buttons_config, update_buttons_config
from .settings.management_config import MANAGEMENT_CONFIG, load_management_config, update_management_config
from .settings.menu_layout_config import MENU_LAYOUT, load_menu_layout, update_menu_layout
from .settings.modes_config import MODES_CONFIG, load_modes_config, update_modes_config
from .settings.money_config import MONEY_CONFIG, load_money_config, update_money_config
from .settings.notifications_config import NOTIFICATIONS_CONFIG, load_notifications_config, update_notifications_config
from .settings.payments_config import PAYMENTS_CONFIG, load_payments_config, update_payments_config
from .settings.providers_order_config import PROVIDERS_ORDER, load_providers_order, update_providers_order
from .settings.remnawave_config import REMNAWAVE_CONFIG, load_remnawave_config, update_remnawave_config
from .settings.runtime_sync import publish_runtime_snapshot
from .settings.tariffs_config import TARIFFS_CONFIG, load_tariffs_config, update_tariffs_config
from .settings.web_config import WEB_CONFIG, load_web_config, update_web_config


async def bootstrap() -> None:
    await warm_pool()
    async with async_session_maker() as session:
        await initialize_all_tariff_weights(session)
        await load_buttons_config(session)
        await load_menu_layout(session)
        await load_notifications_config(session)
        await load_modes_config(session)
        await load_payments_config(session)
        await load_providers_order(session)
        await load_money_config(session)
        await load_management_config(session)
        await load_tariffs_config(session)
        await load_web_config(session)
        await load_remnawave_config(session)
        await session.commit()
        await settings_cache.load(session)
        await publish_runtime_snapshot()

    try:
        from api.v2.routes._data_uri_migration import run_startup_data_uri_migration

        async with async_session_maker() as session:
            rows_updated, uris_replaced = await run_startup_data_uri_migration(session)
            if uris_replaced:
                await session.commit()
                logger.info(
                    "[bootstrap] data: URI migration: rows_updated={} uris_replaced={}",
                    rows_updated,
                    uris_replaced,
                )
    except Exception as exc:
        logger.warning("[bootstrap] data: URI migration failed: {}", exc)

    try:
        from database.identity_sessions import cleanup_expired_sessions
        from settings.config import API_TOKEN_TTL_DAYS

        async with async_session_maker() as session:
            if API_TOKEN_TTL_DAYS is None:
                from sqlalchemy import delete

                from database.models import IdentitySession

                result = await session.execute(delete(IdentitySession))
                removed = int(result.rowcount or 0)
                if removed:
                    await session.commit()
                    logger.info(
                        "[bootstrap] API_TOKEN_TTL_DAYS=None → identity sessions wiped on restart: {}",
                        removed,
                    )
            else:
                removed = await cleanup_expired_sessions(session)
                if removed:
                    await session.commit()
                    logger.info(
                        "[bootstrap] expired identity sessions removed (TTL={}d): {}",
                        API_TOKEN_TTL_DAYS,
                        removed,
                    )
    except Exception as exc:
        logger.warning("[bootstrap] identity-session cleanup failed: {}", exc)

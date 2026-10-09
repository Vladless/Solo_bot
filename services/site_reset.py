from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database.models import Identity
from database.models.web import (
    WebBlock,
    WebCustomElementBuild,
    WebErrorReport,
    WebFlow,
    WebFlowEvent,
    WebNotification,
    WebPage,
    WebPageVariant,
    WebPageVariantBlock,
    WebPushSubscription,
    WebTheme,
)
from database.site_state import reset_site_initialized
from logger import logger
from settings.config import DATABASE_URL, USE_PGBOUNCER


async def reset_site(_session: AsyncSession) -> None:
    """Сбрасывает данные сайта и учётные записи, сохраняя расчётные данные клиентов."""
    connect_args: dict = {}
    db_url = DATABASE_URL
    if "+asyncpg" in DATABASE_URL:
        connect_args["command_timeout"] = 300
        connect_args["timeout"] = 60
        if USE_PGBOUNCER:
            connect_args["prepared_statement_cache_size"] = 0
            sep = "&" if "?" in db_url else "?"
            db_url = f"{db_url}{sep}prepared_statement_cache_size=0"

    local_engine = create_async_engine(
        db_url,
        pool_pre_ping=True,
        connect_args=connect_args,
    )
    local_session_maker = async_sessionmaker(bind=local_engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with local_session_maker() as session:
            await _run_reset(session)
            await session.commit()
    finally:
        await local_engine.dispose()


async def _run_reset(session: AsyncSession) -> None:
    try:
        await session.execute(text("SET LOCAL statement_timeout = '5min'"))
        await session.execute(text("SET LOCAL lock_timeout = '30s'"))
    except Exception as exc:
        logger.warning("[Site:Reset] Не удалось задать тайм-ауты базы (возможно, SQLite): {}", exc)

    steps: list[tuple[str, object]] = [
        ("web_page_variant_blocks", delete(WebPageVariantBlock)),
        ("web_blocks", delete(WebBlock)),
        ("web_page_variants", delete(WebPageVariant)),
        ("web_themes", delete(WebTheme)),
        ("web_pages", delete(WebPage)),
        ("web_push_subscriptions", delete(WebPushSubscription)),
        ("web_notifications", delete(WebNotification)),
        ("web_error_reports", delete(WebErrorReport)),
        ("web_flow_events", delete(WebFlowEvent)),
        ("web_custom_element_builds", delete(WebCustomElementBuild)),
        ("web_flows", delete(WebFlow)),
        ("identities", delete(Identity)),
    ]
    for label, stmt in steps:
        logger.info("[Site:Reset] Шаг начат: {}", label)
        if label == "identities":
            try:
                activity = await session.execute(
                    text(
                        "SELECT pid, state, wait_event_type, wait_event, "
                        "query_start, LEFT(query, 200) AS query "
                        "FROM pg_stat_activity "
                        "WHERE datname = current_database() AND pid <> pg_backend_pid() "
                        "ORDER BY query_start"
                    )
                )
                for row in activity.mappings():
                    logger.info("[Site:Reset] Состояние базы перед очисткой клиентов: {}", dict(row))
            except Exception as exc:
                logger.warning("[Site:Reset] Диагностику базы снять не удалось: {}", exc)
        try:
            result = await session.execute(stmt)
            rowcount = getattr(result, "rowcount", "?")
            logger.info("[Site:Reset] Шаг выполнен: {} (строк: {})", label, rowcount)
        except Exception as exc:
            logger.error("[Site:Reset] Шаг не выполнен: {} — {}: {}", label, type(exc).__name__, exc)
            try:
                activity = await session.execute(
                    text(
                        "SELECT pid, state, wait_event_type, wait_event, "
                        "query_start, LEFT(query, 200) AS query "
                        "FROM pg_stat_activity "
                        "WHERE datname = current_database() AND pid <> pg_backend_pid()"
                    )
                )
                for row in activity.mappings():
                    logger.error("[Site:Reset] Состояние базы после сбоя: {}", dict(row))
            except Exception:
                pass
            raise
    await reset_site_initialized(session)
    logger.info("[Site:Reset] Сайт сброшен к исходному состоянию")

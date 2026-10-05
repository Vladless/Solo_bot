import asyncio

from logger import logger


async def notifications_loop(bot, sessionmaker) -> None:
    from handlers.notifications.engine import periodic_notifications

    await periodic_notifications(bot, sessionmaker=sessionmaker)


async def scheduled_broadcasts_loop_task(bot, _sessionmaker) -> None:
    from handlers.admin.sender.scheduled_service import scheduled_broadcasts_loop

    await scheduled_broadcasts_loop(bot)


async def backup_loop(bot, _sessionmaker) -> None:
    from settings.config import BACKUP_TIME
    from utils.backup import backup_database

    if BACKUP_TIME <= 0:
        await asyncio.Event().wait()
        return
    while True:
        try:
            error = await backup_database(bot_instance=bot)
            if error:
                logger.error("[Backup] Ошибка: {}", error)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("[Backup] Копия не создана: {}", exc)
        await asyncio.sleep(BACKUP_TIME)


def backup_thread_loop(stop_event, _bot, _sessionmaker) -> None:
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode

    from core.settings.modes_config import resolve_protect_content
    from settings.config import API_TOKEN, BACKUP_TIME
    from utils.backup import backup_database

    if BACKUP_TIME <= 0:
        stop_event.wait()
        return
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    backup_bot = Bot(
        token=API_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, protect_content=resolve_protect_content()),
    )
    try:
        while not stop_event.is_set():
            error = loop.run_until_complete(backup_database(bot_instance=backup_bot))
            if error:
                logger.error("[Backup] Ошибка: {}", error)
            if stop_event.wait(BACKUP_TIME):
                break
    finally:
        try:
            loop.set_exception_handler(lambda _loop, _ctx: None)
            loop.run_until_complete(backup_bot.session.close())
        except Exception:
            pass
        try:
            loop.close()
        except Exception:
            pass


async def blocked_drain_loop(_bot, sessionmaker) -> None:
    from core.redis_cache import cache_lpop_batch
    from database.bans import remove_blocked_user_ids, save_blocked_user_ids
    from settings.cache_config import BLOCKED_DRAIN_BATCH, BLOCKED_DRAIN_INTERVAL_SEC, BLOCKED_EVENTS_REDIS_KEY

    while True:
        try:
            events = await cache_lpop_batch(BLOCKED_EVENTS_REDIS_KEY, BLOCKED_DRAIN_BATCH)
            if events:
                final: dict[int, str] = {}
                for ev in events:
                    tg_id = ev.get("tg_id")
                    action = ev.get("action")
                    if tg_id and action:
                        final[int(tg_id)] = action

                to_add = [tid for tid, act in final.items() if act == "block"]
                to_remove = [tid for tid, act in final.items() if act == "unblock"]

                if to_add or to_remove:
                    async with sessionmaker() as session:
                        if to_add:
                            await save_blocked_user_ids(session, to_add)
                        if to_remove:
                            await remove_blocked_user_ids(session, to_remove)
                        await session.commit()
                    logger.info("[BlockedDrain] add={}, remove={}", len(to_add), len(to_remove))
        except Exception as e:
            logger.error("[BlockedDrain] Ошибка: {}", e)
        await asyncio.sleep(BLOCKED_DRAIN_INTERVAL_SEC)


async def server_checks_loop(_bot, sessionmaker) -> None:
    from servers import check_servers
    from settings.config import PING_TIME

    if PING_TIME <= 0:
        await asyncio.Event().wait()
        return
    await check_servers(sessionmaker=sessionmaker)


async def remnawave_monitor_loop(bot, sessionmaker) -> None:
    from services.remnawave_monitor import remnawave_monitor_loop as run_loop

    await run_loop(bot, sessionmaker)

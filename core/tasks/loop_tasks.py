import asyncio
import json

from datetime import UTC, datetime
from math import isfinite

from logger import logger


async def notifications_loop(bot, sessionmaker) -> None:
    from handlers.notifications.engine import periodic_notifications

    await periodic_notifications(bot, sessionmaker=sessionmaker)


async def yookassa_autopay_loop(bot, sessionmaker) -> None:
    from services.payments.yookassa_autopay.service import autopay_loop

    await autopay_loop(bot, sessionmaker)


async def kassai_autopay_loop(bot, sessionmaker) -> None:
    """Сверяет платежи и выполняет автопродление через СБП KassaI."""
    from services.payments.kassai_autopay.service import autopay_loop

    await autopay_loop(bot, sessionmaker)


async def yookassa_refunds_loop(bot, sessionmaker) -> None:
    """Продолжает сверку отправленных возвратов YooKassa."""
    from services.payments.yookassa.refunds import refunds_loop

    await refunds_loop(bot, sessionmaker)


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
    from core.redis_cache import cache_lack_batch, cache_lmove_batch, cache_lrange
    from database.bans import apply_telegram_chat_observations
    from settings.cache_config import BLOCKED_DRAIN_BATCH, BLOCKED_DRAIN_INTERVAL_SEC, BLOCKED_EVENTS_REDIS_KEY

    processing_key = f"{BLOCKED_EVENTS_REDIS_KEY}:processing"
    while True:
        try:
            events = await cache_lrange(processing_key, 0, BLOCKED_DRAIN_BATCH - 1, raw=True)
            if not events:
                await cache_lmove_batch(BLOCKED_EVENTS_REDIS_KEY, processing_key, BLOCKED_DRAIN_BATCH)
                events = await cache_lrange(processing_key, 0, BLOCKED_DRAIN_BATCH - 1, raw=True)
            if events:
                observations = []
                ignored = 0
                for raw in events:
                    try:
                        ev = json.loads(raw)
                    except (TypeError, ValueError, RecursionError):
                        ignored += 1
                        continue
                    if not isinstance(ev, dict):
                        ignored += 1
                        continue
                    tg_id = ev.get("tg_id")
                    user_id = ev.get("user_id")
                    action = ev.get("action")
                    observed_at = ev.get("observed_at")
                    if (
                        not isinstance(tg_id, int)
                        or isinstance(tg_id, bool)
                        or tg_id <= 0
                        or (
                            user_id is not None
                            and (not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0)
                        )
                        or action not in ("block", "unblock")
                        or not isinstance(observed_at, int | float)
                        or isinstance(observed_at, bool)
                    ):
                        ignored += 1
                        continue
                    try:
                        if not isfinite(observed_at):
                            raise ValueError("Некорректное время события")
                        observation_time = datetime.fromtimestamp(observed_at, UTC)
                    except (ValueError, OverflowError, OSError):
                        ignored += 1
                        continue
                    observations.append((user_id, tg_id, action == "block", observation_time))
                if ignored:
                    logger.warning("[BlockedDrain] Пропущено событий без подтверждённого времени: {}", ignored)
                if observations:
                    async with sessionmaker() as session:
                        added, removed = await apply_telegram_chat_observations(session, observations)
                        await session.commit()
                    logger.info("[BlockedDrain] add={}, remove={}", added, removed)
                if not await cache_lack_batch(processing_key, events, raw=True):
                    logger.warning("[BlockedDrain] Пакет сохранён для повторной проверки: {}", len(events))
        except Exception as e:
            logger.exception("[BlockedDrain] Ошибка {}", type(e).__name__)
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


async def remnawave_installer_loop(bot, sessionmaker) -> None:
    """Продолжает установку нод и ожидание DNS после перезапуска."""
    from services.remnawave_installer.jobs import installer_loop

    await installer_loop(bot, sessionmaker)


async def tickets_delivery_loop(_bot, sessionmaker) -> None:
    """Доставляет сохранённые события поддержки после фиксации транзакции."""
    from services.tickets.outbox import process_due

    while True:
        try:
            processed = await process_due(sessionmaker=sessionmaker)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("[Tickets] Ошибка очереди доставки: {}", type(exc).__name__)
            processed = 0
        await asyncio.sleep(0.5 if processed else 2)


async def overpay_autopay_loop(bot, sessionmaker) -> None:
    """Сверяет платежи и выполняет автопродление картой OverPay."""
    from services.payments.overpay_autopay.service import autopay_loop

    await autopay_loop(bot, sessionmaker)


async def payhot_autopay_loop(bot, sessionmaker) -> None:
    """Сверяет списания PayHot и продлевает подписки."""
    from services.payments.payhot.autopay import autopay_loop

    await autopay_loop(bot, sessionmaker)

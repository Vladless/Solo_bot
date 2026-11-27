import asyncio

from collections.abc import Awaitable, Callable

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InputPollOption
from sqlalchemy import select

from database import async_session_maker
from database.models import Poll
from database.polls import POLL_STATUS_OPEN, record_poll_message
from handlers.admin.sender.sender_service import DEFAULT_MESSAGES_PER_SECOND, RateLimiter
from handlers.admin.sender.sender_utils import is_telegram_chat_id
from logger import logger
from services.broadcast_control import BroadcastRun, wait_broadcast_delay


class _PollRecordingError(RuntimeError):
    pass


async def broadcast_poll(
    bot: Bot,
    *,
    poll_id: str,
    question: str,
    options: list[str],
    allows_multiple: bool,
    is_anonymous: bool,
    tg_ids: list[int],
    on_progress: Callable[[int, int, int, int], Awaitable[None]] | None = None,
    run: BroadcastRun | None = None,
) -> dict:
    limiter = RateLimiter(max_rate=DEFAULT_MESSAGES_PER_SECOND)
    poll_options = [InputPollOption(text=opt) for opt in options]
    sent = 0
    failed = 0
    total = len(tg_ids)
    stopped = False
    done = 0

    async def can_send() -> bool:
        if run is not None and run.stop_event.is_set():
            return False
        async with async_session_maker() as session:
            status = await session.scalar(select(Poll.status).where(Poll.id == poll_id))
        return status == POLL_STATUS_OPEN

    async def persist_message(telegram_poll_id: str, tg_id: int) -> bool:
        for attempt in range(3):
            try:
                async with async_session_maker() as session:
                    registered = await record_poll_message(
                        session, telegram_poll_id=telegram_poll_id, poll_id=poll_id, tg_id=tg_id
                    )
                    await session.commit()
                return registered
            except Exception as exc:
                if attempt == 2:
                    raise _PollRecordingError("Не удалось сохранить доставленный опрос") from exc
                await asyncio.sleep(0.1 * (attempt + 1))
        return False

    async def _send(tg_id: int) -> bool:
        msg = await bot.send_poll(
            chat_id=tg_id,
            question=question,
            options=poll_options,
            is_anonymous=is_anonymous,
            allows_multiple_answers=allows_multiple,
        )
        if msg.poll is None:
            raise ValueError("Telegram не вернул идентификатор опроса")
        saving = asyncio.create_task(persist_message(msg.poll.id, tg_id))
        try:
            return await asyncio.shield(saving)
        except asyncio.CancelledError:
            try:
                await saving
            except Exception as exc:
                logger.error("[Polls] Не удалось сохранить доставку при остановке: {}", exc)
            raise

    for idx, tg_id in enumerate(tg_ids, 1):
        if run is not None and run.stop_event.is_set():
            stopped = True
            break
        if not is_telegram_chat_id(tg_id):
            failed += 1
            done = idx
            continue
        acquired = await limiter.acquire(run) if run is not None else await limiter.acquire()
        if acquired is False or not await can_send():
            stopped = True
            break
        try:
            registered = await _send(tg_id)
            sent += 1
            if not registered:
                stopped = True
        except TelegramRetryAfter as exc:
            if await wait_broadcast_delay(run, float(exc.retry_after)) or not await can_send():
                stopped = True
                break
            try:
                registered = await _send(tg_id)
                sent += 1
                if not registered:
                    stopped = True
            except _PollRecordingError:
                raise
            except Exception:
                failed += 1
        except _PollRecordingError:
            raise
        except (TelegramForbiddenError, TelegramBadRequest):
            failed += 1
        except Exception as exc:
            failed += 1
            logger.debug("[Polls] send_poll {} не удался: {}", tg_id, exc)

        done = idx
        if on_progress is not None and idx % 50 == 0:
            await on_progress(idx, total, sent, failed)
        if stopped:
            break

    if on_progress is not None:
        await on_progress(done, total, sent, failed)
    return {"sent": sent, "failed": failed, "total": total, "stopped": stopped}

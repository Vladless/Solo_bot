import asyncio

from collections.abc import Awaitable, Callable

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InputPollOption

from database import async_session_maker
from database.polls import record_poll_message, set_sent_count
from handlers.admin.sender.sender_service import DEFAULT_MESSAGES_PER_SECOND, RateLimiter
from handlers.admin.sender.sender_utils import is_telegram_chat_id
from logger import logger


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
) -> dict:
    limiter = RateLimiter(max_rate=DEFAULT_MESSAGES_PER_SECOND)
    poll_options = [InputPollOption(text=opt) for opt in options]
    sent = 0
    failed = 0
    buffer: list[tuple[str, int]] = []
    total = len(tg_ids)

    async def flush() -> None:
        if not buffer:
            return
        async with async_session_maker() as session:
            for telegram_poll_id, tid in buffer:
                await record_poll_message(session, telegram_poll_id=telegram_poll_id, poll_id=poll_id, tg_id=tid)
            await session.commit()
        buffer.clear()

    async def _send(tg_id: int) -> bool:
        msg = await bot.send_poll(
            chat_id=tg_id,
            question=question,
            options=poll_options,
            is_anonymous=is_anonymous,
            allows_multiple_answers=allows_multiple,
        )
        if msg.poll is not None:
            buffer.append((msg.poll.id, tg_id))
        return True

    for idx, tg_id in enumerate(tg_ids, 1):
        if not is_telegram_chat_id(tg_id):
            continue
        await limiter.acquire()
        try:
            await _send(tg_id)
            sent += 1
        except TelegramRetryAfter as exc:
            await asyncio.sleep(min(float(exc.retry_after), 120.0))
            try:
                await _send(tg_id)
                sent += 1
            except Exception:
                failed += 1
        except (TelegramForbiddenError, TelegramBadRequest):
            failed += 1
        except Exception as exc:
            failed += 1
            logger.debug("[Polls] send_poll {} не удался: {}", tg_id, exc)

        if len(buffer) >= 50:
            await flush()
        if on_progress is not None and idx % 50 == 0:
            await on_progress(idx, total, sent, failed)

    await flush()
    async with async_session_maker() as session:
        await set_sent_count(session, poll_id, sent)
        await session.commit()

    if on_progress is not None:
        await on_progress(total, total, sent, failed)
    return {"sent": sent, "failed": failed, "total": total}

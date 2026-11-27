import asyncio

from weakref import WeakValueDictionary

from aiogram import Router
from aiogram.types import PollAnswer, Update
from sqlalchemy.ext.asyncio import AsyncSession

from database.polls import record_vote
from logger import logger


router = Router(name="polls")
_VOTE_LOCKS: WeakValueDictionary[tuple[asyncio.AbstractEventLoop, str, int], asyncio.Lock] = WeakValueDictionary()


@router.poll_answer()
async def on_poll_answer(poll_answer: PollAnswer, session: AsyncSession, event_update: Update | None = None) -> None:
    user = poll_answer.user
    if user is None:
        return
    key = (asyncio.get_running_loop(), poll_answer.poll_id, user.id)
    lock = _VOTE_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _VOTE_LOCKS[key] = lock
    async with lock:
        try:
            ordering = {"update_id": event_update.update_id} if event_update is not None else {}
            poll_id = await record_vote(
                session,
                telegram_poll_id=poll_answer.poll_id,
                tg_id=user.id,
                option_ids=list(poll_answer.option_ids or []),
                **ordering,
            )
            await session.commit()
            if poll_id is None:
                logger.debug("[Polls] Ответ ожидает регистрации telegram_poll_id={}", poll_answer.poll_id)
        except (Exception, asyncio.CancelledError) as exc:
            await session.rollback()
            if isinstance(exc, asyncio.CancelledError):
                raise
            logger.error("[Polls] Ошибка записи голоса: {}", exc)

from aiogram import Router
from aiogram.types import PollAnswer
from sqlalchemy.ext.asyncio import AsyncSession

from database.polls import record_vote
from logger import logger


router = Router(name="polls")


@router.poll_answer()
async def on_poll_answer(poll_answer: PollAnswer, session: AsyncSession) -> None:
    user = poll_answer.user
    if user is None:
        return
    try:
        poll_id = await record_vote(
            session,
            telegram_poll_id=poll_answer.poll_id,
            tg_id=user.id,
            option_ids=list(poll_answer.option_ids or []),
        )
        if poll_id is None:
            logger.debug("[Polls] poll_answer для неизвестного telegram_poll_id={}", poll_answer.poll_id)
    except Exception as exc:
        logger.error("[Polls] Ошибка записи голоса: {}", exc)

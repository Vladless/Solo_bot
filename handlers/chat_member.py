import asyncio

from datetime import datetime

from aiogram import Router
from aiogram.types import ChatMemberUpdated
from sqlalchemy import text

from core.redis_cache import cache_rpush
from database import (
    async_session_maker,
    bans as chat_availability,
)
from logger import logger
from settings.cache_config import BLOCKED_EVENTS_REDIS_KEY


router = Router(name="chat_member_router")


@router.my_chat_member()
async def on_my_chat_member(event: ChatMemberUpdated):
    if event.chat.type != "private" or event.chat.id <= 0:
        return
    tg_id = event.chat.id
    new_status = event.new_chat_member.status
    action = None
    if new_status == "kicked":
        action = "block"
    elif new_status == "member":
        old_status = event.old_chat_member.status if event.old_chat_member else None
        if old_status in ("kicked", "left"):
            action = "unblock"
    if action is None:
        return
    if not isinstance(event.date, datetime):
        logger.warning("[ChatMember] Не получено время события для chat={}", tg_id)
        return
    payload = {"tg_id": tg_id, "action": action, "observed_at": event.date.timestamp()}
    try:
        async with asyncio.timeout(1):
            queued = await cache_rpush(BLOCKED_EVENTS_REDIS_KEY, payload)
    except Exception as error:
        logger.warning("[ChatMember] Очередь недоступна: chat={}, type={}", tg_id, type(error).__name__)
        queued = 0
    if queued:
        logger.info("[ChatMember] chat={} action={} добавлено в очередь", tg_id, action)
        return
    try:
        async with asyncio.timeout(2):
            async with async_session_maker() as session:
                async with session.begin():
                    if session.bind is not None and session.bind.dialect.name == "postgresql":
                        await session.execute(text("SET LOCAL lock_timeout = '1s'"))
                    added, removed = await chat_availability.apply_telegram_chat_observations(
                        session, [(None, tg_id, action == "block", event.date)]
                    )
        logger.info("[ChatMember] chat={} action={} сохранено: added={}, removed={}", tg_id, action, added, removed)
    except Exception as error:
        logger.exception("[ChatMember] Ошибка сохранения статуса: chat={}, type={}", tg_id, type(error).__name__)

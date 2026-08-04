from aiogram import Router
from aiogram.types import ChatMemberUpdated

from core.redis_cache import cache_rpush
from logger import logger
from settings.cache_config import BLOCKED_EVENTS_REDIS_KEY


router = Router(name="chat_member_router")


@router.my_chat_member()
async def on_my_chat_member(event: ChatMemberUpdated):
    tg_id = event.from_user.id
    new_status = event.new_chat_member.status

    if new_status == "kicked":
        await cache_rpush(BLOCKED_EVENTS_REDIS_KEY, {"tg_id": tg_id, "action": "block"})
        logger.info(f"[ChatMember] Пользователь {tg_id} заблокировал бота → событие в Redis")

    elif new_status == "member":
        old_status = event.old_chat_member.status if event.old_chat_member else None
        if old_status in ("kicked", "left"):
            await cache_rpush(BLOCKED_EVENTS_REDIS_KEY, {"tg_id": tg_id, "action": "unblock"})
            logger.info(f"[ChatMember] Пользователь {tg_id} разблокировал бота → событие в Redis")

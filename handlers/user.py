from typing import Any

from aiogram import Router
from aiogram.filters.chat_member_updated import KICKED, MEMBER, ChatMemberUpdatedFilter
from aiogram.types import ChatMemberUpdated

from logger import logger

router = Router()


@router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=KICKED))
async def user_blocked_bot(event: ChatMemberUpdated, session: Any):
    logger.info(f"User {event.from_user.id} blocked the bot.")
    await session.execute("INSERT INTO blocked_users (tg_id) VALUES ($1) ON CONFLICT (tg_id) DO NOTHING", event.from_user.id)


@router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=MEMBER))
async def user_unblocked_bot(event: ChatMemberUpdated, session: Any):
    logger.info(f"User {event.from_user.id} unblocked the bot.")
    await session.execute("DELETE FROM blocked_users WHERE tg_id = $1", event.from_user.id)

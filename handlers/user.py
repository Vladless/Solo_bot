from typing import Any

from aiogram import Router
from aiogram.filters.chat_member_updated import KICKED, MEMBER, ChatMemberUpdatedFilter
from aiogram.types import ChatMemberUpdated

from database import add_blocked_user, remove_blocked_user
from logger import logger

router = Router()


@router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=KICKED))
async def user_blocked_bot(event: ChatMemberUpdated, session: Any):
    logger.info(f"User {event.from_user.id} blocked the bot.")
    await add_blocked_user(event.from_user.id, session)


@router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=MEMBER))
async def user_unblocked_bot(event: ChatMemberUpdated, session: Any):
    logger.info(f"User {event.from_user.id} unblocked the bot.")
    await remove_blocked_user(event.from_user.id, session)

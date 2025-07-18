from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from database.db import async_session_maker
from database.models import Admin


class IsAdminFilter(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        try:
            async with async_session_maker() as session:
                result = await session.execute(select(Admin).where(Admin.tg_id == event.from_user.id))
                admin = result.scalar_one_or_none()
                return admin is not None
        except Exception:
            return False

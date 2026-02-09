from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select, exists

from database.db import async_session_maker
from database.models import Admin


class IsAdminFilter(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        if not event.from_user:
            return False

        try:
            async with async_session_maker() as session:
                result = await session.execute(select(exists().where(Admin.tg_id == event.from_user.id)))
                return result.scalar()
        except (Exception,):
            return False


class IsSuperAdminFilter(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        if not event.from_user:
            return False

        try:
            async with async_session_maker() as session:
                admin = (
                    await session.execute(
                        select(Admin).where(Admin.tg_id == event.from_user.id)
                    )
                ).scalar_one_or_none()
                if not admin:
                    return False
                return admin.role != "moderator"
        except (Exception,):
            return False

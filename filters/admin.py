from aiogram.filters import BaseFilter
from aiogram.types import Message
from sqlalchemy import select
from database.models import Admin

from database.db import async_session_maker

class IsAdminFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        try:
            async with async_session_maker() as session:
                result = await session.execute(
                    select(Admin).where(Admin.tg_id == message.from_user.id)
                )
                admin = result.scalar_one_or_none()
                return admin is not None
        except Exception:
            return False

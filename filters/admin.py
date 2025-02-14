from aiogram.filters import BaseFilter
from aiogram.types import Message
from config import ADMIN_ID


class IsAdminFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        try:
            admin_ids: int | list[int] = ADMIN_ID
            if isinstance(admin_ids, list):
                return message.from_user.id in admin_ids
        except Exception:
            return False

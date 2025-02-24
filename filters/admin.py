from aiogram.filters import BaseFilter
from aiogram.types import Message

from config import ADMIN_ID


class IsAdminFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        try:
            return message.from_user.id in ADMIN_ID
        except Exception:
            return False

from aiogram.filters import BaseFilter
from aiogram.types import Message

from config import ADMIN_ID


class IsAdminFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        if isinstance(ADMIN_ID, list):
            return message.from_user.id in ADMIN_ID
        elif isinstance(ADMIN_ID, int):
            return message.from_user.id == ADMIN_ID
        else:
            return False

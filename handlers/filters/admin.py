from aiogram.filters import BaseFilter
from aiogram.types import Message

from config import ADMIN_ID


class IsAdminFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return message.from_user.id == ADMIN_ID

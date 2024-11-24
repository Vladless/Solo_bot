from typing import Union

from aiogram.filters import BaseFilter
from aiogram.types import Message

from config import ADMIN_ID


class IsAdminFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        try:
            admin_ids: Union[int, list[int]] = ADMIN_ID
            if isinstance(admin_ids, list):
                return message.chat.id in admin_ids
            return message.chat.id == admin_ids
        except Exception:
            return False

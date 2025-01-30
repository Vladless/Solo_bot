from aiogram.enums import ChatType
from aiogram.filters import BaseFilter
from aiogram.types import Chat, TelegramObject


class IsPrivateFilter(BaseFilter):
    async def __call__(self, event: TelegramObject, event_chat: Chat) -> bool:
        return event_chat.type == ChatType.PRIVATE

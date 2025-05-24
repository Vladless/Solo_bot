from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import InlineKeyboardButton, Message, Update
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import bot
from config import CHANNEL_EXISTS, CHANNEL_ID, CHANNEL_REQUIRED, CHANNEL_URL
from handlers.buttons import SUB_CHANELL, SUB_CHANELL_DONE
from handlers.texts import SUBSCRIPTION_REQUIRED_MSG
from handlers.utils import edit_or_send_message
from logger import logger


class SubscriptionMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        pass

    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        if not CHANNEL_EXISTS or not CHANNEL_REQUIRED:
            return await handler(event, data)

        tg_id = None
        message = None

        if event.message:
            tg_id = event.message.from_user.id
            message = event.message
        elif event.callback_query:
            tg_id = event.callback_query.from_user.id
            message = event.callback_query.message
        else:
            return await handler(event, data)

        try:
            member = await bot.get_chat_member(CHANNEL_ID, tg_id)
            if member.status not in ("member", "administrator", "creator"):
                logger.info(f"[SubMiddleware] Пользователь {tg_id} не подписан")
                return await self._ask_to_subscribe(message)
        except Exception as e:
            logger.warning(
                f"[SubMiddleware] Ошибка при проверке подписки для {tg_id}: {e}"
            )
            return await self._ask_to_subscribe(message)

        return await handler(event, data)

    async def _ask_to_subscribe(self, message: Message):
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=SUB_CHANELL, url=CHANNEL_URL))
        builder.row(
            InlineKeyboardButton(
                text=SUB_CHANELL_DONE, callback_data="check_subscription"
            )
        )

        await edit_or_send_message(
            target_message=message,
            text=SUBSCRIPTION_REQUIRED_MSG,
            reply_markup=builder.as_markup(),
        )

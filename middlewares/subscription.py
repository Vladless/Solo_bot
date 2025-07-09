from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import InlineKeyboardButton, Message, Update
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext

from bot import bot
from config import CHANNEL_EXISTS, CHANNEL_ID, CHANNEL_REQUIRED, CHANNEL_URL
from handlers.localization import get_user_texts, get_user_buttons
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

                state: FSMContext = data.get("state")
                if state:
                    original_text = message.text or message.caption
                    user_data = {
                        "tg_id": message.from_user.id,
                        "username": message.from_user.username,
                        "first_name": message.from_user.first_name,
                        "last_name": message.from_user.last_name,
                        "language_code": message.from_user.language_code,
                        "is_bot": message.from_user.is_bot,
                    }
                    await state.update_data(
                        original_text=original_text,
                        user_data=user_data,
                    )

                session = data.get("session")
                return await self._ask_to_subscribe(message, session, tg_id)
        except Exception as e:
            logger.warning(
                f"[SubMiddleware] Ошибка при проверке подписки для {tg_id}: {e}"
            )
            session = data.get("session")
            return await self._ask_to_subscribe(message, session, tg_id)

        return await handler(event, data)

    async def _ask_to_subscribe(self, message: Message, session, tg_id: int):
        texts = await get_user_texts(session, tg_id)
        buttons = await get_user_buttons(session, tg_id)
        
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=buttons.SUB_CHANELL, url=CHANNEL_URL))
        builder.row(
            InlineKeyboardButton(
                text=buttons.SUB_CHANELL_DONE, callback_data="check_subscription"
            )
        )

        await edit_or_send_message(
            target_message=message,
            text=texts.SUBSCRIPTION_REQUIRED_MSG,
            reply_markup=builder.as_markup(),
        )

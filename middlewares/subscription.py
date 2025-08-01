from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
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

                state: FSMContext = data.get("state")
                if state:
                    original_text = message.text or message.caption

                    if event.message:
                        from_user = event.message.from_user
                    elif event.callback_query:
                        from_user = event.callback_query.from_user
                    else:
                        logger.warning(f"[SubMiddleware] Не удалось определить пользователя из события: {event}")
                        return await handler(event, data)

                    if from_user.is_bot:
                        logger.warning(f"[SubMiddleware] Пропуск сохранения is_bot=True для {from_user.id}")
                    else:
                        state_data = await state.get_data()
                        if "original_text" not in state_data or "user_data" not in state_data:
                            user_data = {
                                "tg_id": from_user.id,
                                "username": from_user.username,
                                "first_name": from_user.first_name,
                                "last_name": from_user.last_name,
                                "language_code": from_user.language_code,
                                "is_bot": from_user.is_bot,
                            }
                            await state.update_data(
                                original_text=original_text,
                                user_data=user_data,
                            )

                return await self._ask_to_subscribe(message)
        except Exception as e:
            logger.warning(f"[SubMiddleware] Ошибка при проверке подписки для {tg_id}: {e}")
            return await self._ask_to_subscribe(message)

        return await handler(event, data)

    async def _ask_to_subscribe(self, message: Message):
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=SUB_CHANELL, url=CHANNEL_URL))
        builder.row(InlineKeyboardButton(text=SUB_CHANELL_DONE, callback_data="check_subscription"))

        await edit_or_send_message(
            target_message=message,
            text=SUBSCRIPTION_REQUIRED_MSG,
            reply_markup=builder.as_markup(),
        )

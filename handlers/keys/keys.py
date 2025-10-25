from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from database import delete_key, get_key_details
from handlers.buttons import APPLY, BACK, CANCEL
from handlers.keys.key_view import process_callback_view_key
from handlers.keys.operations import delete_key_from_cluster, update_subscription
from handlers.texts import DELETE_KEY_CONFIRM_MSG, KEY_DELETED_MSG_SIMPLE
from handlers.utils import edit_or_send_message, handle_error
from logger import logger


router = Router()


@router.callback_query(F.data.startswith("update_subscription|"))
async def process_callback_update_subscription(callback_query: CallbackQuery, session: AsyncSession):
    tg_id = callback_query.message.chat.id
    email = callback_query.data.split("|")[1]

    try:
        try:
            await callback_query.message.delete()
        except TelegramBadRequest as e:
            if "message can't be deleted" not in str(e):
                raise

        await update_subscription(tg_id, email, session)
        await process_callback_view_key(callback_query, session)
    except Exception as e:
        logger.error(f"Ошибка при обновлении ключа {email} пользователем: {e}")
        await handle_error(tg_id, callback_query, f"Ошибка при обновлении подписки: {e}")


@router.callback_query(F.data.startswith("delete_key|"))
async def process_callback_delete_key(callback_query: CallbackQuery):
    client_id = callback_query.data.split("|")[1]
    try:
        confirmation_keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text=APPLY, callback_data=f"confirm_delete|{client_id}")],
                [types.InlineKeyboardButton(text=CANCEL, callback_data="view_keys")],
            ]
        )

        if callback_query.message.caption:
            await callback_query.message.edit_caption(
                caption=DELETE_KEY_CONFIRM_MSG, reply_markup=confirmation_keyboard
            )
        else:
            await callback_query.message.edit_text(text=DELETE_KEY_CONFIRM_MSG, reply_markup=confirmation_keyboard)

    except Exception as e:
        logger.error(f"Ошибка при подготовке удаления ключа {client_id}: {e}")


@router.callback_query(F.data.startswith("confirm_delete|"))
async def process_callback_confirm_delete(callback_query: CallbackQuery, session: AsyncSession):
    email = callback_query.data.split("|")[1]
    try:
        record = await get_key_details(session, email)
        if record:
            client_id = record["client_id"]
            server_id = record["server_id"]
            response_message = KEY_DELETED_MSG_SIMPLE
            back_button = types.InlineKeyboardButton(text=BACK, callback_data="view_keys")
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[back_button]])

            await delete_key(session, client_id)

            await edit_or_send_message(
                target_message=callback_query.message,
                text=response_message,
                reply_markup=keyboard,
            )

            try:
                await delete_key_from_cluster(server_id, email, client_id, session)
            except Exception as e:
                logger.error(f"Ошибка при удалении ключа {client_id} с сервера {server_id}: {e}")

        else:
            response_message = "Ключ не найден или уже удален."
            back_button = types.InlineKeyboardButton(text=BACK, callback_data="view_keys")
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[back_button]])
            await edit_or_send_message(
                target_message=callback_query.message,
                text=response_message,
                reply_markup=keyboard,
            )
    except Exception as e:
        logger.error(f"Ошибка при подтверждении удаления ключа: {e}")
        await handle_error(callback_query.message.chat.id, callback_query, str(e))

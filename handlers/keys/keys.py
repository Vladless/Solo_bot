import asyncio

from typing import Any

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest

from aiogram.types import CallbackQuery

from database import (
    delete_key,
    get_key_details,
    get_servers,
)
from handlers.buttons import (
    APPLY,
    BACK,
    CANCEL,
)
from handlers.keys.key_view import process_callback_view_key
from handlers.keys.key_utils import (
    delete_key_from_cluster,
    update_subscription,
)
from handlers.texts import (
    DELETE_KEY_CONFIRM_MSG,
    KEY_DELETED_MSG_SIMPLE,
)
from handlers.utils import edit_or_send_message, handle_error
from logger import logger


router = Router()


@router.callback_query(F.data.startswith("update_subscription|"))
async def process_callback_update_subscription(callback_query: CallbackQuery, session: Any):
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
                [
                    types.InlineKeyboardButton(
                        text=APPLY,
                        callback_data=f"confirm_delete|{client_id}",
                    )
                ],
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
        logger.error(f"Ошибка при обработке запроса на удаление ключа {client_id}: {e}")


@router.callback_query(F.data.startswith("confirm_delete|"))
async def process_callback_confirm_delete(callback_query: CallbackQuery, session: Any):
    email = callback_query.data.split("|")[1]
    try:
        record = await get_key_details(email, session)
        if record:
            client_id = record["client_id"]
            response_message = KEY_DELETED_MSG_SIMPLE
            back_button = types.InlineKeyboardButton(text=BACK, callback_data="view_keys")
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[back_button]])

            await delete_key(client_id, session)

            await edit_or_send_message(
                target_message=callback_query.message, text=response_message, reply_markup=keyboard, media_path=None
            )

            servers = await get_servers(session)

            async def delete_key_from_servers():
                try:
                    tasks = []
                    for cluster_id, _cluster in servers.items():
                        tasks.append(delete_key_from_cluster(cluster_id, email, client_id))
                    await asyncio.gather(*tasks, return_exceptions=True)
                except Exception as e:
                    logger.error(f"Ошибка при удалении ключа {client_id}: {e}")

            asyncio.create_task(delete_key_from_servers())

            await delete_key(client_id, session)
        else:
            response_message = "Ключ не найден или уже удален."
            back_button = types.InlineKeyboardButton(text=BACK, callback_data="view_keys")
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[back_button]])
            await edit_or_send_message(
                target_message=callback_query.message, text=response_message, reply_markup=keyboard, media_path=None
            )
    except Exception as e:
        logger.error(e)

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from database import delete_key, get_key_details
from database.access.resolution import UserId
from database.keys import lock_owned_key_for_operation
from handlers.keys.utils import build_key_callback, key_owned_by_user, resolve_key
from handlers.keys.view.router import process_callback_view_key
from handlers.utils import edit_or_send_message, handle_error
from logger import logger
from middlewares.session import operation_session, release_session_early
from services.operations import delete_key_from_cluster, update_subscription
from settings.buttons import APPLY, BACK, CANCEL
from settings.texts import DELETE_KEY_CONFIRM_MSG, KEY_DELETED_MSG_SIMPLE


router = Router()


@router.callback_query(F.data.startswith("update_subscription|"), flags={"popup": True})
async def process_callback_update_subscription(callback_query: CallbackQuery, session: AsyncSession):
    tg_id = callback_query.message.chat.id
    key_ref = callback_query.data.split("|", 1)[1]
    key_obj = await resolve_key(session, callback_query.from_user.id, key_ref)
    email = key_obj.email if key_obj else key_ref

    try:
        record = await get_key_details(session, email)
        if (
            key_obj is None
            or not key_owned_by_user(record, callback_query.from_user.id)
            or record.get("user_id") != key_obj.user_id
            or record.get("client_id") != key_obj.client_id
        ):
            await callback_query.answer("Доступ запрещён.", show_alert=True)
            return
        try:
            await callback_query.message.delete()
        except TelegramBadRequest as e:
            if "message can't be deleted" not in str(e):
                raise

        await update_subscription(record["user_id"], email, session)
        await process_callback_view_key(callback_query, session)
    except Exception as e:
        logger.error(f"Ошибка при обновлении ключа {email} пользователем: {e}")
        await handle_error(tg_id, callback_query, f"Ошибка при обновлении подписки: {e}")


@router.callback_query(F.data.startswith("delete_key|"), flags={"popup": True})
async def process_callback_delete_key(callback_query: CallbackQuery, session: AsyncSession):
    key_ref = callback_query.data.split("|", 1)[1]
    try:
        key_obj = await resolve_key(session, callback_query.from_user.id, key_ref)
        key_identifier = key_obj.email if key_obj else key_ref
        record = await get_key_details(session, key_identifier)
        if (
            key_obj is None
            or not key_owned_by_user(record, callback_query.from_user.id)
            or record.get("user_id") != key_obj.user_id
            or record.get("client_id") != key_obj.client_id
        ):
            await callback_query.answer("Доступ запрещён.", show_alert=True)
            return
        confirmation_keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text=APPLY,
                        callback_data=build_key_callback("confirm_delete", record.get("client_id"), key_identifier),
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
        logger.error(f"Ошибка при подготовке удаления ключа {key_identifier}: {e}")


@router.callback_query(F.data.startswith("confirm_delete|"), flags={"popup": True})
async def process_callback_confirm_delete(callback_query: CallbackQuery, session: AsyncSession):
    key_ref = callback_query.data.split("|", 1)[1]
    try:
        key_obj = await resolve_key(session, callback_query.from_user.id, key_ref)
        email = key_obj.email if key_obj else key_ref
        record = await get_key_details(session, email)
        if (
            key_obj is None
            or not key_owned_by_user(record, callback_query.from_user.id)
            or record.get("user_id") != key_obj.user_id
            or record.get("client_id") != key_obj.client_id
        ):
            await callback_query.answer("Доступ запрещён.", show_alert=True)
            return
        if record:
            client_id = record["client_id"]
            response_message = KEY_DELETED_MSG_SIMPLE
            back_button = types.InlineKeyboardButton(text=BACK, callback_data="view_keys")
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[back_button]])

            await release_session_early(session)
            async with operation_session(session) as operation:
                current = await lock_owned_key_for_operation(operation, UserId(key_obj.user_id), client_id, email)
                if current is None:
                    await callback_query.answer("Данные устарели. Откройте подписку заново.", show_alert=True)
                    return
                await delete_key_from_cluster(current.server_id, current.email, current.client_id, operation)
                await delete_key(operation, current.client_id, user_id=current.user_id)

            await edit_or_send_message(
                target_message=callback_query.message,
                text=response_message,
                reply_markup=keyboard,
            )

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

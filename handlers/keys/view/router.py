import os
import re

import pytz

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_key_details, get_keys
from database.access.resolution import resolve_user_optional
from database.models import Key
from handlers.keys.view.payload import (
    DEVICES_PER_PAGE,
    _render_my_devices,
    build_keys_response,
    render_key_info,
)
from handlers.keys.utils import build_key_ref, key_owned_by_user, resolve_key
from handlers.utils import (
    edit_or_send_message,
    safe_answer_callback,
)
from logger import logger
from panels.remnawave_runtime import (
    invalidate_remnawave_profile,
    with_remnawave_api,
)
from settings.buttons import (
    BACK,
)
from settings.texts import (
    RENAME_KEY_PROMPT,
)


router = Router()
moscow_tz = pytz.timezone("Europe/Moscow")


class RenameKeyState(StatesGroup):
    waiting_for_new_alias = State()


@router.callback_query(F.data == "view_keys")
@router.message(F.text == "/subs")
async def process_callback_or_message_view_keys(
    callback_query_or_message: Message | CallbackQuery,
    session: AsyncSession,
    page: int = 0,
):
    if isinstance(callback_query_or_message, CallbackQuery):
        target_message = callback_query_or_message.message
    else:
        target_message = callback_query_or_message

    tg_id = callback_query_or_message.from_user.id

    records = await get_keys(session, tg_id)

    if records and len(records) == 1:
        key_ref = build_key_ref(records[0].client_id, records[0].email)
        image_path = os.path.join("img", "pic_view.jpg")
        await render_key_info(target_message, session, key_ref, image_path)
        return

    inline_keyboard, response_message = await build_keys_response(records, session, page=page)
    image_path = os.path.join("img", "pic_keys.jpg")

    await edit_or_send_message(
        target_message=target_message,
        text=response_message,
        reply_markup=inline_keyboard,
        media_path=image_path,
    )


@router.callback_query(F.data.startswith("view_keys|"))
async def process_callback_view_keys_paged(
    callback_query: CallbackQuery,
    session: AsyncSession,
):
    parts = callback_query.data.split("|")
    page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    await process_callback_or_message_view_keys(callback_query, session, page=page)


@router.callback_query(F.data.startswith("rename_key|"), flags={"popup": True})
async def handle_rename_key(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    client_id = callback.data.split("|")[1]
    key_row = (await session.execute(select(Key).where(Key.client_id == client_id))).scalar_one_or_none()
    if not key_row or key_row.tg_id != callback.from_user.id:
        await safe_answer_callback(callback, "Доступ запрещён.", show_alert=True)
        return
    await state.set_state(RenameKeyState.waiting_for_new_alias)
    await state.update_data(client_id=client_id)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=BACK, callback_data="cancel_and_back_to_view_keys"))

    await edit_or_send_message(
        target_message=callback.message,
        text=RENAME_KEY_PROMPT,
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data == "cancel_and_back_to_view_keys")
async def cancel_and_back(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.clear()
    await process_callback_or_message_view_keys(callback, session)


@router.message(F.text, RenameKeyState.waiting_for_new_alias)
async def handle_new_alias_input(message: Message, state: FSMContext, session: AsyncSession):
    alias = message.text.strip()

    if len(alias) > 10:
        await message.answer("❌ Имя слишком длинное. Введите до 10 символов.\nПовторите ввод.")
        return

    if not alias or not re.match(r"^[a-zA-Zа-яА-ЯёЁ0-9@._-]+$", alias):
        await message.answer(
            "❌ Введены недопустимые символы или имя пустое. Используйте только буквы, цифры и @._-\nПовторите ввод."
        )
        return

    data = await state.get_data()
    client_id = data.get("client_id")

    try:
        u = await resolve_user_optional(session, message.chat.id)
        if u is None:
            await message.answer("❌ Не удалось переименовать подписку.")
            await state.clear()
            return
        await session.execute(update(Key).where(Key.user_id == u.id, Key.client_id == client_id).values(alias=alias))
    except Exception as error:
        await message.answer("❌ Не удалось переименовать подписку.")
        logger.error(f"Ошибка при обновлении alias: {error}")
    finally:
        await state.clear()

    await process_callback_or_message_view_keys(message, session)


@router.callback_query(F.data.startswith("view_key|"), flags={"popup": True})
async def process_callback_view_key(callback_query: CallbackQuery, session: AsyncSession):
    key_ref = callback_query.data.split("|", 1)[1]
    key_obj = await resolve_key(session, callback_query.from_user.id, key_ref)
    record = await get_key_details(session, key_obj.email) if key_obj else None
    if not key_owned_by_user(record, callback_query.from_user.id):
        await safe_answer_callback(callback_query, "Доступ запрещён.", show_alert=True)
        return
    image_path = os.path.join("img", "pic_view.jpg")
    await render_key_info(callback_query.message, session, key_ref, image_path)


@router.callback_query(F.data.startswith("my_devices|"), flags={"popup": True})
async def handle_my_devices(callback_query: CallbackQuery, session: AsyncSession):
    parts = callback_query.data.split("|")
    if len(parts) < 3:
        await safe_answer_callback(callback_query, "❌ Некорректный запрос.", show_alert=True)
        return
    key_ref = parts[1]
    try:
        page = int(parts[2])
    except ValueError:
        page = 0
    await _render_my_devices(callback_query, session, key_ref, page)


@router.callback_query(F.data.startswith("unbind_dev|"), flags={"popup": True})
async def handle_unbind_device(callback_query: CallbackQuery, session: AsyncSession):
    parts = callback_query.data.split("|")
    if len(parts) < 4:
        await safe_answer_callback(callback_query, "❌ Некорректный запрос.", show_alert=True)
        return
    key_ref = parts[1]
    try:
        page = int(parts[2])
        idx = int(parts[3])
    except ValueError:
        await safe_answer_callback(callback_query, "❌ Некорректный запрос.", show_alert=True)
        return

    key_obj = await resolve_key(session, callback_query.from_user.id, key_ref)
    key_name = key_obj.email if key_obj else key_ref
    record = await get_key_details(session, key_name)
    if not record or not key_owned_by_user(record, callback_query.from_user.id):
        await safe_answer_callback(callback_query, "❌ Ключ не найден.", show_alert=True)
        return

    client_id = record.get("client_id")
    if not client_id:
        await safe_answer_callback(callback_query, "❌ У ключа отсутствует client_id.", show_alert=True)
        return

    from services.hwid_cooldown import check_delete_allowed, format_wait_time, register_deletion

    allowed, wait_days = await check_delete_allowed(client_id)
    if not allowed:
        await safe_answer_callback(
            callback_query,
            f"⏳ Слишком частое удаление устройств.\nПопробуйте через {format_wait_time(wait_days)}.",
            show_alert=True,
        )
        return

    server_id = str(record.get("server_id") or "")
    key_email = str(record.get("email") or "") or None

    async def _delete(api):
        devices = await api.get_user_hwid_devices(client_id, username=key_email) or []
        target_idx = page * DEVICES_PER_PAGE + idx
        if target_idx >= len(devices):
            return None
        target_hwid = devices[target_idx].get("hwid")
        if not target_hwid:
            return False
        return await api.delete_user_hwid_device(client_id, target_hwid, username=key_email)

    result = await with_remnawave_api(session, server_id, _delete, fallback_any=True, timeout_sec=10.0)
    if result is None:
        await safe_answer_callback(callback_query, "❌ Устройство не найдено.", show_alert=True)
    elif result is False:
        await safe_answer_callback(callback_query, "❌ Не удалось отвязать устройство.", show_alert=True)
    else:
        await invalidate_remnawave_profile(session, server_id, str(client_id), fallback_any=True)
        await register_deletion(client_id)
        await safe_answer_callback(callback_query, "✅ Устройство отвязано.")

    await _render_my_devices(callback_query, session, key_ref, page)

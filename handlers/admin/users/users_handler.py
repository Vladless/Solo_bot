import asyncio
import uuid

from datetime import datetime, timedelta, timezone
from typing import Any

import pytz

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.formatting import BlockQuote, Bold, Text
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from config import REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD, USE_COUNTRY_SELECTION
from database import (
    delete_key,
    delete_user_data,
    get_balance,
    get_client_id_by_email,
    get_key_details,
    get_servers,
    get_tariff_by_id,
    get_tariffs_for_cluster,
    set_user_balance,
    update_balance,
    update_key_expiry,
    update_trial,
)
from database.models import Key, ManualBan, Payment, Referral, Server, Tariff, User
from filters.admin import IsAdminFilter
from handlers.keys.operations import (
    create_key_on_cluster,
    delete_key_from_cluster,
    get_user_traffic,
    renew_key_in_cluster,
    reset_traffic_in_cluster,
    update_subscription,
)
from handlers.utils import generate_random_email, sanitize_key_name
from logger import logger
from panels.remnawave import RemnawaveAPI
from utils.csv_export import export_referrals_csv

from ..panel.keyboard import (
    AdminPanelCallback,
    build_admin_back_btn,
    build_admin_back_kb,
)
from .keyboard import (
    AdminUserEditorCallback,
    AdminUserKeyEditorCallback,
    build_cluster_selection_kb,
    build_editor_btn,
    build_editor_kb,
    build_hwid_menu_kb,
    build_key_delete_kb,
    build_key_edit_kb,
    build_user_ban_type_kb,
    build_user_delete_kb,
    build_user_edit_kb,
    build_users_balance_change_kb,
    build_users_balance_kb,
    build_users_key_expiry_kb,
    build_users_key_show_kb,
)


MOSCOW_TZ = pytz.timezone("Europe/Moscow")

router = Router()


class UserEditorState(StatesGroup):
    waiting_for_user_data = State()
    waiting_for_key_name = State()
    waiting_for_balance = State()
    waiting_for_expiry_time = State()
    waiting_for_message_text = State()
    preview_message = State()
    selecting_cluster = State()
    selecting_duration = State()
    selecting_country = State()


class RenewTariffState(StatesGroup):
    selecting_group = State()
    selecting_tariff = State()


class BanUserStates(StatesGroup):
    waiting_for_reason = State()
    waiting_for_ban_duration = State()
    waiting_for_forever_reason = State()


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_hwid_menu"), IsAdminFilter())
async def handle_hwid_menu(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    email = callback_data.data
    tg_id = callback_data.tg_id

    client_id = await get_client_id_by_email(session, email)
    if not client_id:
        await callback_query.message.edit_text("🚫 Не удалось найти client_id по email.")
        return

    servers = await get_servers(session=session)
    remna_server = None
    for cluster_servers in servers.values():
        for server in cluster_servers:
            if server.get("panel_type", "") == "remnawave":
                remna_server = server
                break
        if remna_server:
            break

    if not remna_server:
        await callback_query.message.edit_text(
            "🚫 Нет доступного сервера Remnawave.", reply_markup=build_editor_kb(tg_id)
        )
        return

    api = RemnawaveAPI(remna_server["api_url"])
    if not await api.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
        await callback_query.message.edit_text("❌ Ошибка авторизации в Remnawave.")
        return

    devices = await api.get_user_hwid_devices(client_id)

    if not devices:
        text = "💻 <b>HWID устройства</b>\n\n🔌 Нет привязанных устройств."
    else:
        text = f"💻 <b>HWID устройства</b>\n\nПривязано: <b>{len(devices)}</b>\n\n"
        for idx, device in enumerate(devices, 1):
            created = device.get("createdAt", "")[:19].replace("T", " ")
            updated = device.get("updatedAt", "")[:19].replace("T", " ")
            text += (
                f"<b>{idx}.</b> <code>{device.get('hwid')}</code>\n"
                f"└ 📱 <b>Модель:</b> {device.get('deviceModel') or '—'}\n"
                f"└ 🧠 <b>Платформа:</b> {device.get('platform') or '—'} / {device.get('osVersion') or '—'}\n"
                f"└ 🌐 <b>User-Agent:</b> {device.get('userAgent') or '—'}\n"
                f"└ 🕓 <b>Создано:</b> {created}\n"
                f"└ 🔄 <b>Обновлено:</b> {updated}\n\n"
            )

    await callback_query.message.edit_text(text, reply_markup=build_hwid_menu_kb(email, tg_id))


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_hwid_reset"), IsAdminFilter())
async def handle_hwid_reset(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    email = callback_data.data
    tg_id = callback_data.tg_id

    client_id = await get_client_id_by_email(session, email)
    if not client_id:
        await callback_query.message.edit_text("🚫 Не удалось найти client_id по email.")
        return

    servers = await get_servers(session=session)
    remna_server = None
    for cluster_servers in servers.values():
        for server in cluster_servers:
            if server.get("panel_type", "") == "remnawave":
                remna_server = server
                break
        if remna_server:
            break

    if not remna_server:
        await callback_query.message.edit_text(
            "🚫 Нет доступного сервера Remnawave.", reply_markup=build_editor_kb(tg_id)
        )
        return

    api = RemnawaveAPI(remna_server["api_url"])
    if not await api.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
        await callback_query.message.edit_text("❌ Ошибка авторизации в Remnawave.")
        return

    devices = await api.get_user_hwid_devices(client_id)
    if not devices:
        await callback_query.message.edit_text(
            "ℹ️ У пользователя нет привязанных устройств.",
            reply_markup=build_editor_kb(tg_id, True),
        )
        return

    deleted = 0
    for device in devices:
        if await api.delete_user_hwid_device(client_id, device["hwid"]):
            deleted += 1

    await callback_query.message.edit_text(
        f"✅ Удалено HWID-устройств: <b>{deleted}</b> из <b>{len(devices)}</b>.",
        reply_markup=build_editor_kb(tg_id, True),
    )


@router.callback_query(
    AdminPanelCallback.filter(F.action == "search_user"),
    IsAdminFilter(),
)
async def handle_search_user(callback_query: CallbackQuery, state: FSMContext):
    text = (
        "<b>🔍 Поиск пользователя</b>"
        "\n\n📌 Введите ID, Username или перешлите сообщение пользователя."
        "\n\n🆔 ID - числовой айди"
        "\n📝 Username - юзернейм пользователя"
        "\n\n<i>✉️ Для поиска, вы можете просто переслать сообщение от пользователя.</i>"
    )

    await state.set_state(UserEditorState.waiting_for_user_data)
    await callback_query.message.edit_text(text=text, reply_markup=build_admin_back_kb())


@router.callback_query(
    AdminPanelCallback.filter(F.action == "search_key"),
    IsAdminFilter(),
)
async def handle_search_key(callback_query: CallbackQuery, state: FSMContext):
    await state.set_state(UserEditorState.waiting_for_key_name)
    await callback_query.message.edit_text(text="🔑 Введите имя ключа для поиска:", reply_markup=build_admin_back_kb())


@router.message(UserEditorState.waiting_for_key_name, IsAdminFilter())
async def handle_key_name_input(message: Message, state: FSMContext, session: Any):
    kb = build_admin_back_kb()

    if not message.text:
        await message.answer(text="🚫 Пожалуйста, отправьте текстовое сообщение.", reply_markup=kb)
        return

    key_name = sanitize_key_name(message.text)
    key_details = await get_key_details(session, key_name)

    if not key_details:
        await message.answer(text="🚫 Пользователь с указанным именем ключа не найден.", reply_markup=kb)
        return

    await process_user_search(message, state, session, key_details["tg_id"])


@router.message(UserEditorState.waiting_for_user_data, IsAdminFilter())
async def handle_user_data_input(message: Message, state: FSMContext, session: AsyncSession):
    kb = build_admin_back_kb()

    if message.forward_from:
        tg_id = message.forward_from.id
        await process_user_search(message, state, session, tg_id)
        return

    if not message.text:
        await message.answer(text="🚫 Пожалуйста, отправьте текстовое сообщение.", reply_markup=kb)
        return

    if message.text.isdigit():
        tg_id = int(message.text)
    else:
        username = message.text.strip().lstrip("@")
        username = username.replace("https://t.me/", "")

        stmt = select(User.tg_id).where(User.username == username)
        result = await session.execute(stmt)
        tg_id = result.scalar_one_or_none()

        if tg_id is None:
            await message.answer(
                text="🚫 Пользователь с указанным Username не найден!",
                reply_markup=kb,
            )
            return

    await process_user_search(message, state, session, tg_id)


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_send_message"),
    IsAdminFilter(),
)
async def handle_send_message(
    callback_query: types.CallbackQuery,
    callback_data: AdminUserEditorCallback,
    state: FSMContext,
):
    tg_id = callback_data.tg_id

    await callback_query.message.edit_text(
        text=(
            "✉️ Введите текст сообщения, которое вы хотите отправить пользователю:\n\n"
            "Поддерживается только Telegram-форматирование — <b>жирный</b>, <i>курсив</i> и другие стили через редактор Telegram.\n\n"
            "Вы можете отправить:\n"
            "• Только <b>текст</b>\n"
            "• Только <b>картинку</b>\n"
            "• <b>Текст + картинку</b>"
        ),
        reply_markup=build_editor_kb(tg_id),
    )

    await state.update_data(tg_id=tg_id)
    await state.set_state(UserEditorState.waiting_for_message_text)


@router.message(UserEditorState.waiting_for_message_text, IsAdminFilter())
async def handle_message_text_input(message: Message, state: FSMContext):
    data = await state.get_data()
    tg_id = data.get("tg_id")
    text_message = message.html_text or message.text or message.caption or ""
    photo = message.photo[-1].file_id if message.photo else None

    max_len = 1024 if photo else 4096
    if len(text_message) > max_len:
        await message.answer(
            f"⚠️ Сообщение слишком длинное.\nМаксимум: <b>{max_len}</b> символов, сейчас: <b>{len(text_message)}</b>.",
            reply_markup=build_editor_kb(tg_id),
        )
        await state.clear()
        return

    await state.update_data(text=text_message, photo=photo)
    await state.set_state(UserEditorState.preview_message)

    if photo:
        await message.answer_photo(photo=photo, caption=text_message, parse_mode="HTML")
    else:
        await message.answer(text=text_message, parse_mode="HTML")

    await message.answer(
        "👀 Это предпросмотр сообщения. Отправить?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="📤 Отправить", callback_data="send_user_message"),
                    InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_user_message"),
                ]
            ]
        ),
    )


@router.callback_query(F.data == "send_user_message", IsAdminFilter(), UserEditorState.preview_message)
async def handle_send_user_message(callback_query: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tg_id = data.get("tg_id")
    text_message = data.get("text")
    photo = data.get("photo")
    try:
        if photo:
            await callback_query.bot.send_photo(
                chat_id=tg_id,
                photo=photo,
                caption=text_message,
                parse_mode="HTML",
            )
        else:
            await callback_query.bot.send_message(
                chat_id=tg_id,
                text=text_message,
                parse_mode="HTML",
            )
        await callback_query.message.edit_text(
            text="✅ Сообщение успешно отправлено.", reply_markup=build_editor_kb(tg_id)
        )
    except Exception as e:
        await callback_query.message.edit_text(
            text=f"❌ Не удалось отправить сообщение: {e}",
            reply_markup=build_editor_kb(tg_id),
        )
    await state.clear()


@router.callback_query(F.data == "cancel_user_message", IsAdminFilter(), UserEditorState.preview_message)
async def handle_cancel_user_message(callback_query: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tg_id = data.get("tg_id")
    await callback_query.message.edit_text(text="🚫 Отправка сообщения отменена.", reply_markup=build_editor_kb(tg_id))
    await state.clear()


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_trial_restore"),
    IsAdminFilter(),
)
async def handle_trial_restore(
    callback_query: types.CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: Any,
):
    tg_id = callback_data.tg_id

    await update_trial(session, tg_id, 0)
    await callback_query.message.edit_text(text="✅ Триал успешно восстановлен!", reply_markup=build_editor_kb(tg_id))


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_balance_edit"), IsAdminFilter())
async def handle_balance_change(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    tg_id = callback_data.tg_id

    stmt = (
        select(Payment.amount, Payment.payment_system, Payment.status, Payment.created_at)
        .where(Payment.tg_id == tg_id)
        .order_by(Payment.created_at.desc())
        .limit(5)
    )
    result = await session.execute(stmt)
    records = result.all()

    balance = await get_balance(session, tg_id)
    balance = int(balance or 0)

    text = (
        f"<b>💵 Изменение баланса</b>"
        f"\n\n🆔 ID: <b>{tg_id}</b>"
        f"\n💰 Баланс: <b>{balance}Р</b>"
        f"\n📊 Последние операции (5):"
    )

    if records:
        for amount, payment_system, status, created_at in records:
            date = created_at.strftime("%Y-%m-%d %H:%M:%S")
            text += (
                f"\n<blockquote>💸 Сумма: {amount} | {payment_system}"
                f"\n📌 Статус: {status}"
                f"\n⏳ Дата: {date}</blockquote>"
            )
    else:
        text += "\n <i>🚫 Отсутствуют</i>"

    await callback_query.message.edit_text(text=text, reply_markup=await build_users_balance_kb(session, tg_id))


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_balance_add"), IsAdminFilter())
async def handle_balance_add(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    state: FSMContext,
    session: Any,
):
    tg_id = callback_data.tg_id
    amount = callback_data.data

    if amount is not None:
        amount = int(amount)
        old_balance = await get_balance(session, tg_id)

        if amount >= 0:
            await update_balance(session, tg_id, amount)
            new_balance = old_balance + amount
        else:
            new_balance = max(0, old_balance + amount)
            await set_user_balance(session, tg_id, new_balance)
        if old_balance != new_balance:
            await handle_balance_change(callback_query, callback_data, session)
        return

    await state.update_data(tg_id=tg_id, op_type="add")
    await state.set_state(UserEditorState.waiting_for_balance)

    await callback_query.message.edit_text(
        text="✍️ Введите сумму, которую хотите добавить на баланс пользователя:",
        reply_markup=build_users_balance_change_kb(tg_id),
    )


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_balance_take"), IsAdminFilter())
async def handle_balance_take(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    state: FSMContext,
):
    tg_id = callback_data.tg_id

    await state.update_data(tg_id=tg_id, op_type="take")
    await state.set_state(UserEditorState.waiting_for_balance)

    await callback_query.message.edit_text(
        text="✍️ Введите сумму, которую хотите вычесть из баланса пользователя:",
        reply_markup=build_users_balance_change_kb(tg_id),
    )


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_balance_set"), IsAdminFilter())
async def handle_balance_set(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    state: FSMContext,
):
    tg_id = callback_data.tg_id

    await state.update_data(tg_id=tg_id, op_type="set")
    await state.set_state(UserEditorState.waiting_for_balance)

    await callback_query.message.edit_text(
        text="✍️ Введите баланс, который хотите установить пользователю:",
        reply_markup=build_users_balance_change_kb(tg_id),
    )


@router.message(UserEditorState.waiting_for_balance, IsAdminFilter())
async def handle_balance_input(message: Message, state: FSMContext, session: Any):
    data = await state.get_data()
    tg_id = data.get("tg_id")
    op_type = data.get("op_type")

    if not message.text.isdigit() or int(message.text) < 0:
        await message.answer(
            text="🚫 Пожалуйста, введите корректную сумму!",
            reply_markup=build_users_balance_change_kb(tg_id),
        )
        return

    amount = int(message.text)

    if op_type == "add":
        text = f"✅ К балансу пользователя добавлено <b>{amount}Р</b>"
        await update_balance(session, tg_id, amount)
    elif op_type == "take":
        current_balance = await get_balance(session, tg_id)
        new_balance = max(0, current_balance - amount)
        deducted = current_balance if amount > current_balance else amount
        text = f"✅ Из баланса пользователя было вычтено <b>{deducted}Р</b>"
        await set_user_balance(session, tg_id, new_balance)
    else:
        text = f"✅ Баланс пользователя изменен на <b>{amount}Р</b>"
        await set_user_balance(session, tg_id, amount)

    await message.answer(text=text, reply_markup=build_users_balance_change_kb(tg_id))


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_key_edit"), IsAdminFilter())
async def handle_key_edit(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback | AdminUserKeyEditorCallback,
    session: Any,
    update: bool = False,
):
    email = callback_data.data
    result = await session.execute(select(Key).where(Key.email == email))
    key_obj: Key | None = result.scalar_one_or_none()

    if not key_obj:
        await callback_query.message.edit_text(
            text="🚫 Информация о ключе не найдена.",
            reply_markup=build_editor_kb(callback_data.tg_id),
        )
        return

    key_value = key_obj.key or key_obj.remnawave_link or "—"
    alias_part = f" (<i>{key_obj.alias}</i>)" if key_obj.alias else ""

    if key_obj.created_at:
        created_at_dt = datetime.fromtimestamp(int(key_obj.created_at) / 1000) + timedelta(hours=3)
        created_at = created_at_dt.strftime("%d %B %Y года %H:%M")
    else:
        created_at = "—"

    if key_obj.expiry_time:
        expiry_dt = datetime.fromtimestamp(int(key_obj.expiry_time) / 1000)
        expiry_date = expiry_dt.strftime("%d %B %Y года %H:%M")
    else:
        expiry_date = "—"

    tariff_name = "—"
    subgroup_title = "—"
    if key_obj.tariff_id:
        result = await session.execute(select(Tariff.name, Tariff.subgroup_title).where(Tariff.id == key_obj.tariff_id))
        row = result.first()
        if row:
            tariff_name = row[0]
            subgroup_title = row[1] or "—"

    text = (
        "<b>🔑 Информация о подписке</b>\n\n"
        "<blockquote>"
        f"🔗 <b>Ключ{alias_part}:</b> <code>{key_value}</code>\n"
        f"📆 <b>Создан:</b> {created_at} (МСК)\n"
        f"⏰ <b>Истекает:</b> {expiry_date} (МСК)\n"
        f"🌐 <b>Кластер:</b> {key_obj.server_id or '—'}\n"
        f"🆔 <b>ID клиента:</b> {key_obj.tg_id or '—'}\n"
        f"📁 <b>Группа:</b> {subgroup_title}\n"
        f"📦 <b>Тариф:</b> {tariff_name}\n"
        "</blockquote>"
    )

    if not update or not callback_data.edit:
        await callback_query.message.edit_text(text=text, reply_markup=build_key_edit_kb(key_obj.__dict__, email))
    else:
        await callback_query.message.edit_text(
            text=text,
            reply_markup=await build_users_key_expiry_kb(session, callback_data.tg_id, email),
        )


@router.callback_query(F.data == "back:renew", IsAdminFilter())
async def handle_back_to_key_menu(
    callback_query: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
):
    data = await state.get_data()
    email = data["email"]
    tg_id = data["tg_id"]
    await state.clear()

    callback_data = AdminUserEditorCallback(action="users_key_edit", data=email, tg_id=tg_id)
    await handle_key_edit(
        callback_query=callback_query,
        callback_data=callback_data,
        session=session,
        update=False,
    )


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_renew"), IsAdminFilter())
async def handle_user_choose_tariff_group(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
    state: FSMContext,
):
    email = callback_data.data
    tg_id = callback_data.tg_id

    await state.set_state(RenewTariffState.selecting_group)
    await state.update_data(email=email, tg_id=tg_id)

    result = await session.execute(select(Tariff.group_code).distinct())
    groups = [row[0] for row in result.fetchall()]

    builder = InlineKeyboardBuilder()
    for group_code in groups:
        builder.button(text=group_code, callback_data=f"group:{group_code}")
    builder.button(text="🔙 Назад", callback_data="back:renew")
    builder.adjust(1)

    await callback_query.message.edit_text(
        text="📁 <b>Выберите тарифную группу:</b>",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("group:"), IsAdminFilter())
async def handle_user_choose_tariff(
    callback_query: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
):
    group_code = callback_query.data.split(":", 1)[1]
    await state.update_data(group_code=group_code)
    await state.set_state(RenewTariffState.selecting_tariff)

    result = await session.execute(
        select(Tariff).where(Tariff.group_code == group_code, Tariff.is_active.is_(True)).order_by(Tariff.id)
    )
    tariffs = result.scalars().all()

    if not tariffs:
        await callback_query.message.edit_text("❌ Нет активных тарифов в группе.")
        return

    builder = InlineKeyboardBuilder()
    for tariff in tariffs:
        builder.button(text=f"{tariff.name} – {int(tariff.price_rub)}₽", callback_data=f"confirm:{tariff.id}")
    builder.button(text="🔙 Назад", callback_data="back:group")
    builder.adjust(1)

    await callback_query.message.edit_text(
        text=f"📦 <b>Выберите тариф для группы <code>{group_code}</code>:</b>",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("confirm:"), IsAdminFilter())
async def handle_user_renew_confirm(
    callback_query: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
):
    tariff_id = int(callback_query.data.split(":")[1])
    data = await state.get_data()
    email = data["email"]
    tg_id = data["tg_id"]

    stmt = update(Key).where(Key.tg_id == tg_id, Key.email == email).values(tariff_id=tariff_id)
    await session.execute(stmt)
    await session.commit()

    await update_subscription(
        tg_id=tg_id,
        email=email,
        session=session
    )
    
    await state.clear()

    callback_data = AdminUserEditorCallback(action="users_key_edit", data=email, tg_id=tg_id)

    await handle_key_edit(
        callback_query=callback_query,
        callback_data=callback_data,
        session=session,
        update=False,
    )


@router.callback_query(F.data == "back:group", IsAdminFilter())
async def handle_back_to_group(
    callback_query: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    await state.get_data()

    result = await session.execute(select(Tariff.group_code).distinct())
    groups = [row[0] for row in result.fetchall()]

    builder = InlineKeyboardBuilder()
    for group_code in groups:
        builder.button(text=group_code, callback_data=f"group:{group_code}")
    builder.button(text="🔙 Назад", callback_data="back:renew")
    builder.adjust(1)

    await callback_query.message.edit_text(
        text="📁 <b>Выберите тарифную группу:</b>",
        reply_markup=builder.as_markup(),
    )
    await state.set_state(RenewTariffState.selecting_group)


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_expiry_edit"), IsAdminFilter())
async def handle_change_expiry(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    tg_id = callback_data.tg_id
    email = callback_data.data

    await callback_query.message.edit_reply_markup(reply_markup=await build_users_key_expiry_kb(session, tg_id, email))


@router.callback_query(AdminUserKeyEditorCallback.filter(F.action == "add"), IsAdminFilter())
async def handle_expiry_add(
    callback_query: CallbackQuery,
    callback_data: AdminUserKeyEditorCallback,
    state: FSMContext,
    session: Any,
):
    tg_id = callback_data.tg_id
    email = callback_data.data
    days = callback_data.month

    key_details = await get_key_details(session, email)

    if not key_details:
        await callback_query.message.edit_text(
            text="🚫 Информация о ключе не найдена.",
            reply_markup=build_editor_kb(tg_id),
        )
        return

    if days:
        await change_expiry_time(key_details["expiry_time"] + days * 24 * 3600 * 1000, email, session)
        await handle_key_edit(callback_query, callback_data, session, True)
        return

    await state.update_data(tg_id=tg_id, email=email, op_type="add")
    await state.set_state(UserEditorState.waiting_for_expiry_time)

    await callback_query.message.edit_text(
        text="✍️ Введите количество дней, которое хотите добавить к времени действия ключа:",
        reply_markup=build_users_key_show_kb(tg_id, email),
    )


@router.callback_query(AdminUserKeyEditorCallback.filter(F.action == "take"), IsAdminFilter())
async def handle_expiry_take(
    callback_query: CallbackQuery,
    callback_data: AdminUserKeyEditorCallback,
    state: FSMContext,
):
    tg_id = callback_data.tg_id
    email = callback_data.data

    await state.update_data(tg_id=tg_id, email=email, op_type="take")
    await state.set_state(UserEditorState.waiting_for_expiry_time)

    await callback_query.message.edit_text(
        text="✍️ Введите количество дней, которое хотите вычесть из времени действия ключа:",
        reply_markup=build_users_key_show_kb(tg_id, email),
    )


@router.callback_query(AdminUserKeyEditorCallback.filter(F.action == "set"), IsAdminFilter())
async def handle_expiry_set(
    callback_query: CallbackQuery,
    callback_data: AdminUserKeyEditorCallback,
    state: FSMContext,
    session: Any,
):
    tg_id = callback_data.tg_id
    email = callback_data.data

    key_details = await get_key_details(session, email)

    if not key_details:
        await callback_query.message.edit_text(
            text="🚫 Информация о ключе не найдена.",
            reply_markup=build_editor_kb(tg_id),
        )
        return

    await state.update_data(tg_id=tg_id, email=email, op_type="set")
    await state.set_state(UserEditorState.waiting_for_expiry_time)

    text = (
        "✍️ Введите новое время действия ключа:"
        "\n\n📌 Формат: <b>год-месяц-день час:минута</b>"
        f"\n\n📄 Текущая дата: {datetime.fromtimestamp(key_details['expiry_time'] / 1000).strftime('%Y-%m-%d %H:%M')}"
    )

    await callback_query.message.edit_text(text=text, reply_markup=build_users_key_show_kb(tg_id, email))


@router.message(UserEditorState.waiting_for_expiry_time, IsAdminFilter())
async def handle_expiry_time_input(message: Message, state: FSMContext, session: Any):
    data = await state.get_data()
    tg_id = data.get("tg_id")
    email = data.get("email")
    op_type = data.get("op_type")

    if op_type != "set" and (not message.text.isdigit() or int(message.text) < 0):
        await message.answer(
            text="🚫 Пожалуйста, введите корректное количество дней!",
            reply_markup=build_users_key_show_kb(tg_id, email),
        )
        return

    key_details = await get_key_details(session, email)

    if not key_details:
        await message.answer(
            text="🚫 Информация о ключе не найдена.",
            reply_markup=build_editor_kb(tg_id),
        )
        return

    try:
        current_expiry_time = datetime.fromtimestamp(key_details["expiry_time"] / 1000, tz=MOSCOW_TZ)

        if op_type == "add":
            days = int(message.text)
            new_expiry_time = current_expiry_time + timedelta(days=days)
            text = f"✅ Ко времени действия ключа добавлено <b>{days} дн.</b>"

        elif op_type == "take":
            days = int(message.text)
            new_expiry_time = current_expiry_time - timedelta(days=days)
            text = f"✅ Из времени действия ключа вычтено <b>{days} дн.</b>"

        else:
            new_expiry_time = datetime.strptime(message.text, "%Y-%m-%d %H:%M")
            new_expiry_time = MOSCOW_TZ.localize(new_expiry_time)
            text = f"✅ Время действия ключа изменено на <b>{message.text} (МСК)</b>"

        new_expiry_timestamp = int(new_expiry_time.timestamp() * 1000)
        await change_expiry_time(new_expiry_timestamp, email, session)

    except ValueError:
        text = "🚫 Пожалуйста, используйте корректный формат даты (ГГГГ-ММ-ДД ЧЧ:ММ)!"
    except Exception as e:
        text = f"❗ Произошла ошибка во время изменения времени действия ключа: {e}"

    await message.answer(text=text, reply_markup=build_users_key_show_kb(tg_id, email))


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_update_key"), IsAdminFilter())
async def handle_update_key(callback_query: CallbackQuery, callback_data: AdminUserEditorCallback, session: Any):
    tg_id = callback_data.tg_id
    email = callback_data.data

    await callback_query.message.edit_text(
        text=f"📡 Выберите кластер, на котором пересоздать ключ <b>{email}</b>:",
        reply_markup=await build_cluster_selection_kb(session, tg_id, email, action="confirm_admin_key_reissue"),
    )


@router.callback_query(F.data.startswith("confirm_admin_key_reissue|"), IsAdminFilter())
async def confirm_admin_key_reissue(callback_query: CallbackQuery, session: Any, state: FSMContext):
    _, tg_id, email, cluster_id = callback_query.data.split("|")
    tg_id = int(tg_id)

    try:
        servers = await get_servers(session)
        cluster_servers = servers.get(cluster_id, [])

        tariffs = await get_tariffs_for_cluster(session, cluster_id)
        if not tariffs:
            builder = InlineKeyboardBuilder()
            builder.row(
                InlineKeyboardButton(
                    text="🔗 Привязать тариф", callback_data=AdminPanelCallback(action="clusters").pack()
                )
            )
            builder.row(
                InlineKeyboardButton(
                    text="🔙 Назад",
                    callback_data=AdminUserEditorCallback(action="users_key_edit", tg_id=tg_id, data=email).pack(),
                )
            )
            await callback_query.message.edit_text(
                f"🚫 <b>Невозможно пересоздать подписку</b>\n\n"
                f"📊 <b>Информация о кластере:</b>\n<blockquote>"
                f"🌐 <b>Кластер:</b> <code>{cluster_id}</code>\n"
                f"⚠️ <b>Статус:</b> Нет привязанного тарифа\n</blockquote>"
                f"💡 <b>Привяжите тариф к кластеру</b>",
                reply_markup=builder.as_markup(),
            )
            return

        if USE_COUNTRY_SELECTION:
            unique_countries = {srv["server_name"] for srv in cluster_servers}
            await state.update_data(tg_id=tg_id, email=email, cluster_id=cluster_id)
            builder = InlineKeyboardBuilder()
            for country in sorted(unique_countries):
                builder.button(
                    text=country,
                    callback_data=f"admin_reissue_country|{tg_id}|{email}|{country}",
                )
            builder.row(InlineKeyboardButton(text="Назад", callback_data=f"users_key_edit|{email}"))
            await callback_query.message.edit_text(
                "🌍 Выберите сервер (страну) для пересоздания подписки:",
                reply_markup=builder.as_markup(),
            )
            return

        result = await session.execute(select(Key.remnawave_link).where(Key.email == email))
        remnawave_link = result.scalar_one_or_none()

        await update_subscription(tg_id, email, session, cluster_override=cluster_id, remnawave_link=remnawave_link)

        await handle_key_edit(
            callback_query,
            AdminUserEditorCallback(tg_id=tg_id, data=email, action="view_key"),
            session,
            True,
        )

    except Exception as e:
        logger.error(f"Ошибка при перевыпуске ключа {email}: {e}")
        await callback_query.message.answer(f"❗ Ошибка: {e}")


@router.callback_query(F.data.startswith("admin_reissue_country|"), IsAdminFilter())
async def admin_reissue_country(callback_query: CallbackQuery, session: AsyncSession, state: FSMContext):
    _, tg_id, email, country = callback_query.data.split("|")
    tg_id = int(tg_id)

    try:
        data = await state.get_data()
        cluster_id = data.get("cluster_id")

        if cluster_id:
            tariffs = await get_tariffs_for_cluster(session, cluster_id)
            if not tariffs:
                builder = InlineKeyboardBuilder()
                builder.row(
                    InlineKeyboardButton(
                        text="🔗 Привязать тариф", callback_data=AdminPanelCallback(action="clusters").pack()
                    )
                )
                builder.row(
                    InlineKeyboardButton(
                        text="🔙 Назад",
                        callback_data=AdminUserEditorCallback(action="users_key_edit", tg_id=tg_id, data=email).pack(),
                    )
                )
                await callback_query.message.edit_text(
                    f"🚫 <b>Невозможно пересоздать подписку</b>\n\n"
                    f"📊 <b>Информация о кластере:</b>\n<blockquote>"
                    f"🌐 <b>Кластер:</b> <code>{cluster_id}</code>\n"
                    f"⚠️ <b>Статус:</b> Нет привязанного тарифа\n</blockquote>"
                    f"💡 <b>Привяжите тариф к кластеру</b>",
                    reply_markup=builder.as_markup(),
                )
                return

        result = await session.execute(select(Key.remnawave_link, Key.tariff_id).where(Key.email == email))
        remnawave_link, _tariff_id = result.one_or_none() or (None, None)

        await update_subscription(
            tg_id=tg_id,
            email=email,
            session=session,
            country_override=country,
            remnawave_link=remnawave_link,
        )

        await handle_key_edit(
            callback_query,
            AdminUserEditorCallback(tg_id=tg_id, data=email, action="view_key"),
            session,
            True,
        )
    except Exception as e:
        logger.error(f"Ошибка при перевыпуске ключа для страны {country}: {e}")
        await callback_query.message.answer(f"❗ Ошибка: {e}")


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_delete_key"), IsAdminFilter())
async def handle_delete_key(callback_query: CallbackQuery, callback_data: AdminUserEditorCallback, session: Any):
    email = callback_data.data

    result = await session.execute(select(Key.client_id).where(Key.email == email))
    client_id = result.scalar_one_or_none()

    if client_id is None:
        await callback_query.message.edit_text(
            text="🚫 Ключ не найден!", reply_markup=build_editor_kb(callback_data.tg_id)
        )
        return

    await callback_query.message.edit_text(
        text="❓ Вы уверены, что хотите удалить ключ?",
        reply_markup=build_key_delete_kb(callback_data.tg_id, email),
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_delete_key_confirm"),
    IsAdminFilter(),
)
async def handle_delete_key_confirm(
    callback_query: types.CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    email = callback_data.data

    result = await session.execute(select(Key.client_id).where(Key.email == email))
    client_id = result.scalar_one_or_none()

    kb = build_editor_kb(callback_data.tg_id)

    if client_id:
        clusters = await get_servers(session=session)

        async def delete_key_from_servers():
            tasks = []
            for cluster_name, cluster_servers in clusters.items():
                for _ in cluster_servers:
                    tasks.append(delete_key_from_cluster(cluster_name, email, client_id, session))
            await asyncio.gather(*tasks, return_exceptions=True)

        await delete_key_from_servers()
        await delete_key(session, client_id)

        await callback_query.message.edit_text(text="✅ Ключ успешно удален.", reply_markup=kb)
    else:
        await callback_query.message.edit_text(text="🚫 Ключ не найден или уже удален.", reply_markup=kb)


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_delete_user"), IsAdminFilter())
async def handle_delete_user(callback_query: CallbackQuery, callback_data: AdminUserEditorCallback):
    tg_id = callback_data.tg_id
    await callback_query.message.edit_text(
        text=f"❗️ Вы уверены, что хотите удалить пользователя с ID {tg_id}?",
        reply_markup=build_user_delete_kb(tg_id),
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_delete_user_confirm"),
    IsAdminFilter(),
)
async def handle_delete_user_confirm(
    callback_query: types.CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    tg_id = callback_data.tg_id

    result = await session.execute(select(Key.email, Key.client_id).where(Key.tg_id == tg_id))
    key_records = result.all()

    async def delete_keys_from_servers():
        try:
            tasks = []
            servers = await get_servers(session=session)
            for email, client_id in key_records:
                for cluster_id, _cluster in servers.items():
                    tasks.append(delete_key_from_cluster(cluster_id, email, client_id, session))
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            logger.error(f"Ошибка при удалении ключей с серверов для пользователя {tg_id}: {e}")

    await delete_keys_from_servers()

    try:
        await delete_user_data(session, tg_id)

        await callback_query.message.edit_text(
            text=f"🗑️ Пользователь с ID {tg_id} был удален.",
            reply_markup=build_admin_back_kb(),
        )
    except Exception as e:
        logger.error(f"Ошибка при удалении данных из базы данных для пользователя {tg_id}: {e}")
        await callback_query.message.edit_text(
            text=f"❌ Произошла ошибка при удалении пользователя с ID {tg_id}. Попробуйте снова.",
            reply_markup=build_admin_back_kb(),
        )


async def process_user_search(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    tg_id: int,
    edit: bool = False,
) -> None:
    await state.clear()

    stmt_user = select(User.username, User.balance, User.created_at, User.updated_at).where(User.tg_id == tg_id)
    result_user = await session.execute(stmt_user)
    user_data = result_user.first()

    if not user_data:
        await message.answer(
            text="🚫 Пользователь с указанным ID не найден!",
            reply_markup=build_admin_back_kb(),
        )
        return

    username, balance, created_at, updated_at = user_data
    balance = int(balance or 0)
    created_at_str = created_at.replace(tzinfo=pytz.UTC).astimezone(MOSCOW_TZ).strftime("%H:%M:%S %d.%m.%Y")
    updated_at_str = updated_at.replace(tzinfo=pytz.UTC).astimezone(MOSCOW_TZ).strftime("%H:%M:%S %d.%m.%Y")

    stmt_ref_count = select(func.count()).select_from(Referral).where(Referral.referrer_tg_id == tg_id)
    result_ref = await session.execute(stmt_ref_count)
    referral_count = result_ref.scalar_one()

    stmt_ref_by = select(Referral.referrer_tg_id).where(Referral.referred_tg_id == tg_id).limit(1)
    result_ref_by = await session.execute(stmt_ref_by)
    referrer_tg_id = result_ref_by.scalar_one_or_none()

    referrer_text = None
    if referrer_tg_id:
        stmt_referrer = select(User.username).where(User.tg_id == referrer_tg_id)
        result_referrer = await session.execute(stmt_referrer)
        ref_username = result_referrer.scalar_one_or_none()
        if ref_username:
            referrer_text = f"🤝 Пригласил: @{ref_username} ({referrer_tg_id})"
        else:
            referrer_text = f"🤝 Пригласил: {referrer_tg_id}"

    stmt = select(func.count(Payment.id), func.coalesce(func.sum(Payment.amount), 0)).where(
        Payment.status == "success", Payment.tg_id == tg_id
    )
    result = await session.execute(stmt)
    topups_amount, topups_sum = result.one_or_none() or (0, 0)

    stmt_keys = select(Key).where(Key.tg_id == tg_id)
    result_keys = await session.execute(stmt_keys)
    key_records = result_keys.scalars().all()

    stmt_ban = (
        select(1)
        .where((ManualBan.tg_id == tg_id) & (or_(ManualBan.until.is_(None), ManualBan.until > func.now())))
        .limit(1)
    )
    result_ban = await session.execute(stmt_ban)
    is_banned = result_ban.scalar_one_or_none() is not None
    user_obj = await session.get(User, tg_id)
    full_name = user_obj.first_name if user_obj else None

    body = Text(
        f"🆔 ID: {tg_id}\n",
        f"📄 Логин: @{username}" if username else "📄 Логин: —",
        f"{f' ({full_name})' if full_name else ''}\n",
        f"📅 Дата регистрации: {created_at_str}\n",
        f"🏃 Дата активности: {updated_at_str}\n",
        f"💰 Баланс: {balance} Р.\n",
        f"💳 Пополнения: {topups_sum} Р. ({topups_amount} шт.)\n",
        f"👥 Количество рефералов: {referral_count}\n",
    )

    if referrer_text:
        body += Text(referrer_text, "\n")

    text_builder = Text(Bold("📊 Информация о пользователе"), "\n\n", BlockQuote(body))

    text = text_builder.as_html()
    kb = await build_user_edit_kb(tg_id, key_records, is_banned=is_banned)

    if edit:
        try:
            await message.edit_text(text=text, reply_markup=kb, disable_web_page_preview=True)
        except TelegramBadRequest:
            pass
    else:
        await message.answer(text=text, reply_markup=kb, disable_web_page_preview=True)


async def change_expiry_time(expiry_time: int, email: str, session: AsyncSession) -> Exception | None:
    result = await session.execute(select(Key.client_id, Key.tariff_id, Key.server_id).where(Key.email == email))
    row = result.first()
    if not row:
        return ValueError(f"User with email {email} was not found")

    client_id, tariff_id, server_id = row
    if server_id is None:
        return ValueError(f"Key with client_id {client_id} was not found")

    traffic_limit = 0
    device_limit = None
    key_subgroup = None
    if tariff_id:
        result = await session.execute(
            select(Tariff.traffic_limit, Tariff.device_limit, Tariff.subgroup_title).where(
                Tariff.id == tariff_id, Tariff.is_active.is_(True)
            )
        )
        tariff = result.first()
        if tariff:
            traffic_limit = int(tariff[0]) if tariff[0] is not None else 0
            device_limit = int(tariff[1]) if tariff[1] is not None else 0
            key_subgroup = tariff[2]

    servers = await get_servers(session=session)

    if server_id in servers:
        target_cluster = server_id
    else:
        target_cluster = None
        for cluster_name, cluster_servers in servers.items():
            if any(s.get("server_name") == server_id for s in cluster_servers):
                target_cluster = cluster_name
                break

        if not target_cluster:
            return ValueError(f"No suitable cluster found for server {server_id}")

    await renew_key_in_cluster(
        cluster_id=target_cluster,
        email=email,
        client_id=client_id,
        new_expiry_time=expiry_time,
        total_gb=traffic_limit,
        session=session,
        hwid_device_limit=device_limit,
        reset_traffic=False,
        target_subgroup=key_subgroup,
        old_subgroup=key_subgroup,
    )

    await update_key_expiry(session, client_id, expiry_time)
    return None


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_traffic"), IsAdminFilter())
async def handle_user_traffic(
    callback_query: types.CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: Any,
):
    """
    Обработчик кнопки "📊 Трафик".
    Получает трафик пользователя и отправляет администратору.
    """
    tg_id = callback_data.tg_id
    email = callback_data.data

    await callback_query.message.edit_text("⏳ Получаем данные о трафике, пожалуйста, подождите...")

    traffic_data = await get_user_traffic(session, tg_id, email)

    if traffic_data["status"] == "error":
        await callback_query.message.edit_text(traffic_data["message"], reply_markup=build_editor_kb(tg_id, True))
        return

    total_traffic = 0

    result_text = f"📊 <b>Трафик подписки {email}:</b>\n\n"

    for server, traffic in traffic_data["traffic"].items():
        if isinstance(traffic, str):
            result_text += f"❌ {server}: {traffic}\n"
        else:
            result_text += f"🌍 {server}: <b>{traffic} ГБ</b>\n"
            total_traffic += traffic

    result_text += f"\n🔢 <b>Общий трафик:</b> {total_traffic:.2f} ГБ"

    await callback_query.message.edit_text(result_text, reply_markup=build_editor_kb(tg_id, True))


@router.callback_query(AdminPanelCallback.filter(F.action == "restore_trials"), IsAdminFilter())
async def confirm_restore_trials(callback_query: types.CallbackQuery):
    """
    Меню подтверждения перед восстановлением пробников.
    """
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Подтвердить",
        callback_data=AdminPanelCallback(action="confirm_restore_trials").pack(),
    )
    builder.row(build_admin_back_btn())

    await callback_query.message.edit_text(
        text="⚠ Вы уверены, что хотите восстановить пробники для пользователей? \n\n"
        "Только для тех, у кого нет подписок (активных или истекших)!",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "confirm_restore_trials"), IsAdminFilter())
async def restore_trials(callback_query: types.CallbackQuery, session: AsyncSession):
    users_result = await session.execute(select(User.tg_id).where(User.trial == 1))
    users_with_trial_used = [row[0] for row in users_result.all()]

    users_to_reset = []
    for tg_id in users_with_trial_used:
        has_keys = await session.execute(select(Key.tg_id).where(Key.tg_id == tg_id).limit(1))
        if not has_keys.scalar():
            users_to_reset.append(tg_id)

    if users_to_reset:
        stmt = update(User).where(User.tg_id.in_(users_to_reset)).values(trial=0)
        await session.execute(stmt)
        await session.commit()

    builder = InlineKeyboardBuilder()
    builder.row(build_admin_back_btn())

    await callback_query.message.edit_text(
        text=f"✅ Пробники восстановлены для {len(users_to_reset)} пользователей без подписок.",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_export_referrals"),
    IsAdminFilter(),
)
async def handle_users_export_referrals(
    callback_query: types.CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: Any,
):
    """
    Обработчик: получает tg_id реферера из callback_data,
    вызывает export_referrals_csv и отправляет файл или отвечает,
    что рефералов нет.
    """
    referrer_tg_id = callback_data.tg_id

    csv_file = await export_referrals_csv(referrer_tg_id, session)

    if csv_file is None:
        await callback_query.message.answer("У пользователя нет рефералов.")
        return

    await callback_query.message.answer_document(
        document=csv_file,
        caption=f"Список рефералов для пользователя {referrer_tg_id}.",
    )


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_create_key"), IsAdminFilter())
async def handle_create_key_start(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    state: FSMContext,
    session: AsyncSession,
):
    tg_id = callback_data.tg_id
    await state.update_data(tg_id=tg_id)

    if USE_COUNTRY_SELECTION:
        await state.set_state(UserEditorState.selecting_country)

        stmt = select(Server.server_name).distinct().order_by(Server.server_name)
        result = await session.execute(stmt)
        countries = [row[0] for row in result.all()]

        if not countries:
            await callback_query.message.edit_text(
                "❌ Нет доступных стран для создания ключа.",
                reply_markup=build_editor_kb(tg_id),
            )
            return

        builder = InlineKeyboardBuilder()
        for country in countries:
            builder.button(text=country, callback_data=country)
        builder.adjust(1)
        builder.row(build_admin_back_btn())

        await callback_query.message.edit_text(
            "🌍 <b>Выберите страну для создания ключа:</b>",
            reply_markup=builder.as_markup(),
        )
        return

    await state.set_state(UserEditorState.selecting_cluster)

    servers = await get_servers(session=session)
    cluster_names = list(servers.keys())

    if not cluster_names:
        await callback_query.message.edit_text(
            "❌ Нет доступных кластеров для создания ключа.",
            reply_markup=build_editor_kb(tg_id),
        )
        return

    builder = InlineKeyboardBuilder()
    for cluster in cluster_names:
        builder.button(text=f"🌐 {cluster}", callback_data=cluster)
    builder.adjust(2)
    builder.row(build_admin_back_btn())

    await callback_query.message.edit_text(
        "🌐 <b>Выберите кластер для создания ключа:</b>",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(UserEditorState.selecting_country, IsAdminFilter())
async def handle_create_key_country(callback_query: CallbackQuery, state: FSMContext, session):
    country = callback_query.data
    await state.update_data(country=country)
    await state.set_state(UserEditorState.selecting_duration)

    builder = InlineKeyboardBuilder()

    result = await session.execute(select(Server.cluster_name).where(Server.server_name == country))
    row = result.mappings().first()

    if not row:
        await callback_query.message.edit_text("❌ Сервер не найден.")
        return

    cluster_name = row["cluster_name"]
    await state.update_data(cluster_name=cluster_name)

    tariffs = await get_tariffs_for_cluster(session, cluster_name)

    for tariff in tariffs:
        if tariff["duration_days"] < 1:
            continue
        builder.button(text=f"{tariff['name']} — {tariff['price_rub']}₽", callback_data=f"tariff_{tariff['id']}")

    builder.adjust(1)
    builder.row(build_admin_back_btn())

    await callback_query.message.edit_text(
        text=f"🕒 <b>Выберите срок действия ключа для страны <code>{country}</code>:</b>",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(UserEditorState.selecting_cluster, IsAdminFilter())
async def handle_create_key_cluster(callback_query: CallbackQuery, state: FSMContext, session):
    cluster_name = callback_query.data

    data = await state.get_data()
    tg_id = data.get("tg_id")

    if not tg_id:
        await callback_query.message.edit_text("❌ Ошибка: tg_id клиента не найден.")
        return

    await state.update_data(cluster_name=cluster_name)
    await state.set_state(UserEditorState.selecting_duration)

    tariffs = await get_tariffs_for_cluster(session, cluster_name)

    builder = InlineKeyboardBuilder()
    for tariff in tariffs:
        if tariff["duration_days"] < 1:
            continue
        builder.button(text=f"{tariff['name']} — {tariff['price_rub']}₽", callback_data=f"tariff_{tariff['id']}")

    builder.adjust(1)
    builder.row(build_admin_back_btn())

    await callback_query.message.edit_text(
        text=f"🕒 <b>Выберите срок действия ключа для кластера <code>{cluster_name}</code>:</b>",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(UserEditorState.selecting_duration, IsAdminFilter())
async def handle_create_key_duration(callback_query: CallbackQuery, state: FSMContext, session):
    data = await state.get_data()
    tg_id = data.get("tg_id", callback_query.from_user.id)

    try:
        if not callback_query.data.startswith("tariff_"):
            raise ValueError("Некорректный callback_data")
        tariff_id = int(callback_query.data.replace("tariff_", ""))

        tariff = await get_tariff_by_id(session, tariff_id)
        if not tariff:
            raise ValueError("Тариф не найден.")

        duration_days = tariff["duration_days"]
        client_id = str(uuid.uuid4())
        email = await generate_random_email(session=session)
        expiry = datetime.now(tz=timezone.utc) + timedelta(days=duration_days)
        expiry_ms = int(expiry.timestamp() * 1000)

        if USE_COUNTRY_SELECTION and "country" in data:
            country = data["country"]
            await create_key_on_cluster(
                country,
                tg_id,
                client_id,
                email,
                expiry_ms,
                plan=tariff_id,
                session=session,
            )

            await state.clear()
            await callback_query.message.edit_text(
                f"✅ Ключ успешно создан для страны <b>{country}</b> на {duration_days} дней.",
                reply_markup=build_editor_kb(tg_id),
            )

        elif "cluster_name" in data:
            cluster_name = data["cluster_name"]
            await create_key_on_cluster(
                cluster_name,
                tg_id,
                client_id,
                email,
                expiry_ms,
                plan=tariff_id,
                session=session,
            )

            await state.clear()
            await callback_query.message.edit_text(
                f"✅ Ключ успешно создан в кластере <b>{cluster_name}</b> на {duration_days} дней.",
                reply_markup=build_editor_kb(tg_id),
            )

        else:
            await callback_query.message.edit_text("❌ Не удалось определить источник — страна или кластер.")

    except Exception as e:
        logger.error(f"[CreateKey] Ошибка при создании ключа: {e}")
        await callback_query.message.edit_text(
            "❌ Не удалось создать ключ. Попробуйте позже.",
            reply_markup=build_editor_kb(tg_id),
        )


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_reset_traffic"), IsAdminFilter())
async def handle_reset_traffic(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    tg_id = callback_data.tg_id
    email = callback_data.data

    stmt = select(Key.server_id, Key.client_id).where((Key.tg_id == tg_id) & (Key.email == email))
    result = await session.execute(stmt)
    record = result.first()

    if not record:
        await callback_query.message.edit_text("❌ Ключ не найден в базе данных.", reply_markup=build_editor_kb(tg_id))
        return

    cluster_id, _client_id = record

    try:
        await reset_traffic_in_cluster(cluster_id, email, session)
        await callback_query.message.edit_text(
            f"✅ Трафик для ключа <b>{email}</b> успешно сброшен.",
            reply_markup=build_editor_kb(tg_id),
        )
    except Exception as e:
        logger.error(f"Ошибка при сбросе трафика: {e}")
        await callback_query.message.edit_text(
            "❌ Произошла ошибка при сбросе трафика. Попробуйте позже.",
            reply_markup=build_editor_kb(tg_id),
        )


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_ban"), IsAdminFilter())
async def handle_user_ban(callback: CallbackQuery, callback_data: AdminUserEditorCallback, state: FSMContext):
    await state.clear()
    await state.update_data(tg_id=callback_data.tg_id)

    await callback.message.edit_text(
        text="🚫 Выберите тип блокировки пользователя:",
        reply_markup=build_user_ban_type_kb(callback_data.tg_id),
    )


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_ban_forever"), IsAdminFilter())
async def handle_ban_forever_start(callback: CallbackQuery, callback_data: AdminUserEditorCallback, state: FSMContext):
    await state.set_state(BanUserStates.waiting_for_forever_reason)
    await state.update_data(tg_id=callback_data.tg_id)

    kb = InlineKeyboardBuilder()
    kb.row(build_editor_btn("⬅️ Назад", tg_id=callback_data.tg_id, edit=True))

    await callback.message.edit_text(
        text="✏️ Введите причину <b>постоянной блокировки</b> (или <code>-</code>, чтобы пропустить):",
        reply_markup=kb.as_markup(),
    )


@router.message(BanUserStates.waiting_for_forever_reason, IsAdminFilter())
async def handle_ban_forever_reason_input(message: Message, state: FSMContext, session: AsyncSession):
    reason = message.text.strip()
    if reason == "-":
        reason = None

    user_data = await state.get_data()
    tg_id = user_data.get("tg_id")

    stmt = (
        pg_insert(ManualBan)
        .values(
            tg_id=tg_id,
            reason=reason,
            banned_by=message.from_user.id,
            until=None,
            banned_at=datetime.now(timezone.utc),
        )
        .on_conflict_do_update(
            index_elements=[ManualBan.tg_id],
            set_={
                "reason": reason,
                "until": None,
                "banned_by": message.from_user.id,
                "banned_at": datetime.now(timezone.utc),
            },
        )
    )

    await session.execute(stmt)
    await session.commit()
    await state.clear()

    await message.answer(
        text=(f"✅ Пользователь <code>{tg_id}</code> забанен навсегда.{f'\n📄 Причина: {reason}' if reason else ''}"),
        reply_markup=build_editor_kb(tg_id, edit=True),
    )


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_ban_temporary"), IsAdminFilter())
async def handle_ban_temporary(callback: CallbackQuery, callback_data: AdminUserEditorCallback, state: FSMContext):
    await state.set_state(BanUserStates.waiting_for_reason)
    await state.update_data(tg_id=callback_data.tg_id)

    kb = InlineKeyboardBuilder()
    kb.row(build_editor_btn("⬅️ Назад", tg_id=callback_data.tg_id, edit=True))

    await callback.message.edit_text(
        text="✏️ Введите причину <b>временной блокировки</b> (или <code>-</code>, чтобы пропустить):",
        reply_markup=kb.as_markup(),
    )


@router.message(BanUserStates.waiting_for_reason, IsAdminFilter())
async def handle_ban_reason_input(message: Message, state: FSMContext):
    await state.update_data(reason=message.text.strip())
    await state.set_state(BanUserStates.waiting_for_ban_duration)

    user_data = await state.get_data()
    tg_id = user_data.get("tg_id")

    kb = InlineKeyboardBuilder()
    kb.row(build_editor_btn("⬅️ Назад", tg_id=tg_id, edit=True))

    await message.answer(
        "⏳ Введите срок блокировки в днях (0 — навсегда):",
        reply_markup=kb.as_markup(),
    )


@router.message(BanUserStates.waiting_for_ban_duration, IsAdminFilter())
async def handle_ban_duration_input(message: Message, state: FSMContext, session: AsyncSession):
    user_data = await state.get_data()
    tg_id = user_data.get("tg_id")
    reason = user_data.get("reason")
    if reason == "-":
        reason = None

    try:
        days = int(message.text.strip())
        if days < 1:
            await message.answer("❗ Укажите срок минимум в 1 день.")
            return

        until = datetime.now(timezone.utc) + timedelta(days=days)

        stmt = (
            pg_insert(ManualBan)
            .values(
                tg_id=tg_id,
                reason=reason,
                banned_by=message.from_user.id,
                until=until,
                banned_at=datetime.now(timezone.utc),
            )
            .on_conflict_do_update(
                index_elements=[ManualBan.tg_id],
                set_={
                    "reason": reason,
                    "until": until,
                    "banned_at": datetime.now(timezone.utc),
                    "banned_by": message.from_user.id,
                },
            )
        )

        await session.execute(stmt)
        await session.commit()

        text = (
            f"✅ Пользователь <code>{tg_id}</code> временно забанен до <b>{until:%Y-%m-%d %H:%M}</b> по UTC."
            f"{f'\n📄 Причина: {reason}' if reason else ''}"
        )

        await message.answer(text=text, reply_markup=build_editor_kb(tg_id, edit=True))

    except ValueError:
        await message.answer("❗ Введите корректное число дней.")
    finally:
        await state.clear()


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_ban_shadow"), IsAdminFilter())
async def handle_ban_shadow(callback: CallbackQuery, callback_data: AdminUserEditorCallback, session: AsyncSession):
    stmt = (
        pg_insert(ManualBan)
        .values(
            tg_id=callback_data.tg_id,
            reason="shadow",
            banned_by=callback.from_user.id,
            until=None,
            banned_at=datetime.now(timezone.utc),
        )
        .on_conflict_do_update(
            index_elements=[ManualBan.tg_id],
            set_={
                "reason": "shadow",
                "until": None,
                "banned_by": callback.from_user.id,
                "banned_at": datetime.now(timezone.utc),
            },
        )
    )
    await session.execute(stmt)
    await session.commit()

    await callback.message.edit_text(
        text=f"👻 Пользователь <code>{callback_data.tg_id}</code> получил теневой бан.",
        reply_markup=build_editor_kb(callback_data.tg_id, edit=True),
    )


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_unban"), IsAdminFilter())
async def handle_user_unban(
    callback: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    await session.execute(delete(ManualBan).where(ManualBan.tg_id == callback_data.tg_id))
    await session.commit()

    text = (
        f"✅ Пользователь <code>{callback_data.tg_id}</code> разблокирован. Нажмите кнопку ниже для возврата в профиль."
    )

    await callback.message.edit_text(text=text, reply_markup=build_editor_kb(callback_data.tg_id, edit=True))


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_editor"), IsAdminFilter())
async def handle_users_editor(
    callback: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: Any,
    state: FSMContext,
):
    await process_user_search(
        callback.message,
        state=state,
        session=session,
        tg_id=callback_data.tg_id,
        edit=callback_data.edit,
    )

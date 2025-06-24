import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytz
from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import delete, func, or_, select, update, case
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from config import REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD, USE_COUNTRY_SELECTION, logger
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
from database.payments import (
    get_user_balance,
    get_referral_balance,
    get_payment_history,
    add_payment,
    update_user_balance
)
from filters.admin import IsAdminFilter
from handlers.buttons import BACK
from handlers.keys.key_utils import (
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
    build_editor_kb,
    build_hwid_menu_kb,
    build_key_delete_kb,
    build_key_edit_kb,
    build_user_delete_kb,
    build_user_edit_kb,
    build_users_balance_change_kb,
    build_users_balance_kb,
    build_users_key_expiry_kb,
    build_users_key_show_kb,
    build_editor_btn
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


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_hwid_menu"), IsAdminFilter()
)
async def handle_hwid_menu(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    email = callback_data.data
    tg_id = callback_data.tg_id

    client_id = await get_client_id_by_email(session, email)
    if not client_id:
        await callback_query.message.edit_text(
            "🚫 Не удалось найти client_id по email."
        )
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
            "🚫 Нет доступного сервера Remnawave.",
            reply_markup=build_editor_kb(tg_id)
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

    await callback_query.message.edit_text(
        text, reply_markup=build_hwid_menu_kb(email, tg_id)
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_hwid_reset"), IsAdminFilter()
)
async def handle_hwid_reset(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    email = callback_data.data
    tg_id = callback_data.tg_id

    client_id = await get_client_id_by_email(session, email)
    if not client_id:
        await callback_query.message.edit_text(
            "🚫 Не удалось найти client_id по email."
        )
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
            "🚫 Нет доступного сервера Remnawave.",
            reply_markup=build_editor_kb(tg_id)
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
    await callback_query.message.edit_text(
        text=text, reply_markup=build_admin_back_kb()
    )


@router.callback_query(
    AdminPanelCallback.filter(F.action == "search_key"),
    IsAdminFilter(),
)
async def handle_search_key(callback_query: CallbackQuery, state: FSMContext):
    await state.set_state(UserEditorState.waiting_for_key_name)
    await callback_query.message.edit_text(
        text="🔑 Введите имя ключа для поиска:", reply_markup=build_admin_back_kb()
    )


@router.message(UserEditorState.waiting_for_key_name, IsAdminFilter())
async def handle_key_name_input(message: Message, state: FSMContext, session: Any):
    kb = build_admin_back_kb()

    if not message.text:
        await message.answer(
            text="🚫 Пожалуйста, отправьте текстовое сообщение.", reply_markup=kb
        )
        return

    key_name = sanitize_key_name(message.text)
    key_details = await get_key_details(session, key_name)

    if not key_details:
        await message.answer(
            text="🚫 Пользователь с указанным именем ключа не найден.", reply_markup=kb
        )
        return

    await process_user_search(message, state, session, key_details["tg_id"])


@router.message(UserEditorState.waiting_for_user_data, IsAdminFilter())
async def handle_user_data_input(
    message: Message, state: FSMContext, session: AsyncSession
):
    kb = build_admin_back_kb()

    if message.forward_from:
        tg_id = message.forward_from.id
        await process_user_search(message, state, session, tg_id)
        return

    if not message.text:
        await message.answer(
            text="🚫 Пожалуйста, отправьте текстовое сообщение.", reply_markup=kb
        )
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
            f"⚠️ Сообщение слишком длинное.\n"
            f"Максимум: <b>{max_len}</b> символов, сейчас: <b>{len(text_message)}</b>.",
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
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="📤 Отправить", callback_data="send_user_message"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_user_message"),
            ]
        ]),
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
    await callback_query.message.edit_text(
        text="🚫 Отправка сообщения отменена.", reply_markup=build_editor_kb(tg_id)
    )
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
    await callback_query.message.edit_text(
        text="✅ Триал успешно восстановлен!", reply_markup=build_editor_kb(tg_id)
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_balance_edit"), IsAdminFilter()
)
async def handle_balance_change(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    """Show balance management menu"""
    tg_id = callback_data.tg_id
    user = await session.get(User, tg_id)
    
    if not user:
        await callback_query.answer("❌ Пользователь не найден", show_alert=True)
        return
    
    # Get balances
    balance = await get_user_balance(session, tg_id)
    ref_balance = await get_referral_balance(session, tg_id)
    
    # Get recent transactions
    transactions, _ = await get_payment_history(
        session=session,
        tg_id=tg_id,
        limit=5
    )
    
    # Format message
    text = (
        f"👤 <b>Управление балансом</b>\n"
        f"Пользователь: <code>{tg_id}</code>\n"
        f"Имя: {user.first_name or 'Не указано'}\n"
        f"Username: @{user.username or 'Нет'}\n\n"
        f"💰 <b>Основной баланс:</b> {balance:.2f}₽\n"
        f"🎁 <b>Реферальный баланс:</b> {ref_balance:.2f}₽\n\n"
        f"📊 <b>Последние операции:</b>"
    )
    
    if transactions:
        for t in transactions:
            amount = f"+{t['amount']:.2f}₽" if t['amount'] >= 0 else f"{t['amount']:.2f}₽"
            date = datetime.fromisoformat(t['created_at']).strftime("%d.%m %H:%M")
            desc = f" - {t['description']}" if t['description'] else ""
            text += f"\n• {date} | {amount} | {t['operation_type']}{desc}"
    else:
        text += "\nНет операций"
    
    # Send or update message
    await callback_query.message.edit_text(
        text=text,
        reply_markup=await build_users_balance_kb(session, tg_id)
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "balance_management"), IsAdminFilter()
)
async def handle_balance_management(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    state: FSMContext,
):
    """Show balance management options"""
    tg_id = callback_data.tg_id
    
    builder = InlineKeyboardBuilder()
    
    # Add balance management buttons
    builder.add(
        InlineKeyboardButton(
            text="➕ Пополнить баланс",
            callback_data=BalanceActionCallback(
                action="topup", user_id=tg_id
            ).pack()
        ),
        InlineKeyboardButton(
            text="➖ Списать с баланса",
            callback_data=BalanceActionCallback(
                action="deduct", user_id=tg_id
            ).pack()
        ),
        InlineKeyboardButton(
            text="✏️ Установить баланс",
            callback_data=BalanceActionCallback(
                action="set", user_id=tg_id
            ).pack()
        ),
    )
    
    # Add back button
    builder.add(
        InlineKeyboardButton(
            text="🔙 Назад",
            callback_data=AdminUserEditorCallback(
                action="users_balance_edit", tg_id=tg_id
            ).pack()
        )
    )
    
    builder.adjust(1, 1, 1, 1)
    
    await callback_query.message.edit_text(
        f"💰 <b>Управление балансом</b>\n"
        f"ID пользователя: <code>{tg_id}</code>\n\n"
        "Выберите действие:",
        reply_markup=builder.as_markup()
    )
    
    await callback.answer()


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "balance_history"), IsAdminFilter()
)
async def handle_balance_history_view(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    """Show transaction history"""
    tg_id = callback_data.tg_id
    page = 1  # Start with first page
    
    # Get paginated history
    transactions, total = await get_payment_history(
        session=session,
        tg_id=tg_id,
        limit=5,
        page=page
    )
    
    # Format message
    text = format_balance_history(transactions, page, total, tg_id)
    
    # Build pagination keyboard
    builder = InlineKeyboardBuilder()
    
    # Add pagination buttons if needed
    if total > 5:
        if page > 1:
            builder.add(
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=AdminUserEditorCallback(
                        action="balance_history_page",
                        tg_id=tg_id,
                        data=page - 1
                    ).pack()
                )
            )
        
        if page * 5 < total:
            builder.add(
                InlineKeyboardButton(
                    text="Вперед ➡️",
                    callback_data=AdminUserEditorCallback(
                        action="balance_history_page",
                        tg_id=tg_id,
                        data=page + 1
                    ).pack()
                )
            )
    
    # Add back button
    builder.add(
        InlineKeyboardButton(
            text="🔙 Назад",
            callback_data=AdminUserEditorCallback(
                action="users_balance_edit", tg_id=tg_id
            ).pack()
        )
    )
    
    builder.adjust(2, 1)
    
    # Send or update message
    try:
        await callback_query.message.edit_text(
            text=text,
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error showing balance history: {e}")
        await callback.answer("Произошла ошибка при загрузке истории", show_alert=True)
    
    await callback.answer()


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "balance_history_page"), IsAdminFilter()
)
async def handle_balance_history_page(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    """Handle pagination for balance history"""
    tg_id = callback_data.tg_id
    page = int(callback_data.data or 1)
    
    # Get paginated history
    transactions, total = await get_payment_history(
        session=session,
        tg_id=tg_id,
        limit=5,
        page=page
    )
    
    # Format message
    text = format_balance_history(transactions, page, total, tg_id)
    
    # Build pagination keyboard
    builder = InlineKeyboardBuilder()
    
    # Add pagination buttons if needed
    if total > 5:
        if page > 1:
            builder.add(
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=AdminUserEditorCallback(
                        action="balance_history_page",
                        tg_id=tg_id,
                        data=page - 1
                    ).pack()
                )
            )
        
        if page * 5 < total:
            builder.add(
                InlineKeyboardButton(
                    text="Вперед ➡️",
                    callback_data=AdminUserEditorCallback(
                        action="balance_history_page",
                        tg_id=tg_id,
                        data=page + 1
                    ).pack()
                )
            )
    
    # Add back button
    builder.add(
        InlineKeyboardButton(
            text="🔙 Назад",
            callback_data=AdminUserEditorCallback(
                action="users_balance_edit", tg_id=tg_id
            ).pack()
        )
    )
    
    builder.adjust(2, 1)
    
    # Update message
    try:
        await callback_query.message.edit_text(
            text=text,
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error updating history page: {e}")
        await callback.answer("Произошла ошибка при загрузке страницы", show_alert=True)
    
    await callback.answer()


@router.callback_query(
    BalanceActionCallback.filter(F.action.in_(["topup", "deduct", "set"])),
    IsAdminFilter()
)
async def handle_balance_action(
    callback: CallbackQuery,
    callback_data: BalanceActionCallback,
    state: FSMContext,
    session: AsyncSession
):
    """Handle balance actions (topup, deduct, set)"""
    action = callback_data.action
    user_id = callback_data.user_id
    
    # If amount is already provided, process the action immediately
    if callback_data.amount is not None:
        return await _process_balance_action(
            callback=callback,
            callback_data=callback_data,
            session=session
        )
    
    # Otherwise, ask for amount
    await state.set_state(UserEditorState.waiting_for_balance)
    await state.update_data(
        action=action,
        user_id=user_id,
        description=callback_data.description
    )
    
    action_texts = {
        "topup": "пополнить баланс",
        "deduct": "списать с баланса",
        "set": "установить баланс"
    }
    
    await callback.message.edit_text(
        f"💰 Введите сумму, которую хотите {action_texts[action]}:\n"
        "(Можно использовать дробные числа, например: 100.50)",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="🔙 Назад",
                callback_data=AdminUserEditorCallback(
                    action="balance_management",
                    tg_id=user_id
                ).pack()
            )
        ]])
    )
    await callback.answer()


async def _process_balance_action(
    callback: CallbackQuery,
    callback_data: BalanceActionCallback,
    session: AsyncSession
):
    """Process balance action with the provided amount"""
    user_id = callback_data.user_id
    amount = float(callback_data.amount)
    description = callback_data.description or ""
    
    try:
        if callback_data.action == "topup":
            await update_user_balance(
                session=session,
                tg_id=user_id,
                amount=amount,
                operation_type="manual_topup",
                description=description,
                admin_id=callback.from_user.id
            )
            text = f"✅ Баланс пользователя пополнен на {amount:.2f}₽"
            
        elif callback_data.action == "deduct":
            await update_user_balance(
                session=session,
                tg_id=user_id,
                amount=-amount,  # Negative amount for deduction
                operation_type="manual_deduct",
                description=description,
                admin_id=callback.from_user.id
            )
            text = f"✅ С баланса пользователя списано {amount:.2f}₽"
            
        elif callback_data.action == "set":
            current_balance = await get_user_balance(session, user_id)
            difference = amount - current_balance
            
            await update_user_balance(
                session=session,
                tg_id=user_id,
                amount=difference,
                operation_type="manual_set",
                description=f"Установлен баланс {amount:.2f}₽ (было {current_balance:.2f}₽)",
                admin_id=callback.from_user.id
            )
            text = f"✅ Баланс пользователя установлен на {amount:.2f}₽"
        
        await session.commit()
        
        # Show success message
        await callback.answer(text, show_alert=True)
        
        # Return to balance management
        await handle_balance_change(
            callback,
            AdminUserEditorCallback(
                action="users_balance_edit",
                tg_id=user_id
            ),
            session
        )
        
    except Exception as e:
        logger.error(f"Error processing balance action: {e}")
        await callback.answer("❌ Произошла ошибка при обновлении баланса", show_alert=True)


@router.message(UserEditorState.waiting_for_balance, IsAdminFilter())
async def handle_balance_input(message: Message, state: FSMContext):
    """Handle balance amount input"""
    try:
        amount = float(message.text.replace(',', '.'))
        if amount <= 0:
            raise ValueError("Amount must be positive")
            
        data = await state.get_data()
        action = data.get("action")
        user_id = data.get("user_id")
        description = data.get("description", "")
        
        # Process the action
        await _process_balance_action(
            callback=message,
            callback_data=BalanceActionCallback(
                action=action,
                user_id=user_id,
                amount=amount,
                description=description
            ),
            session=message.bot.session
        )
        
        # Clear state
        await state.clear()
        
    except (ValueError, TypeError):
        await message.answer(
            "❌ Пожалуйста, введите корректную сумму (например: 100 или 100.50)",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="🔙 Отмена",
                    callback_data=AdminUserEditorCallback(
                        action="balance_management",
                        tg_id=(await state.get_data()).get("user_id", 0)
                    ).pack()
                )
            ]])
        )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_key_edit"), IsAdminFilter()
)
async def handle_key_edit(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback | AdminUserKeyEditorCallback,
    session: Any,
    update: bool = False,
):
    email = callback_data.data
    key_details = await get_key_details(session, email)

    if not key_details:
        await callback_query.message.edit_text(
            text="🚫 Информация о ключе не найдена.",
            reply_markup=build_editor_kb(callback_data.tg_id),
        )
        return

    key_value = key_details.get("key") or key_details.get("remnawave_link") or "—"
    alias = key_details.get("alias")

    tariff_name = "—"
    if key_details.get("tariff_id"):
        result = await session.execute(
            select(Tariff.name, Tariff.group_code).where(Tariff.id == key_details["tariff_id"])
        )
        row = result.first()
        if row:
            tariff_name = f"{row[0]} ({row[1]})"

    text = (
        f"<b>🔑 Информация о ключе</b>"
        f"\n\n<code>{key_value}</code>"
        f"\n\n⏰ Дата истечения: <b>{key_details['expiry_date']} (UTC)</b>"
        f"\n🌐 Кластер: <b>{key_details['cluster_name']}</b>"
        f"\n🆔 ID клиента: <b>{key_details['tg_id']}</b>"
        f"\n📦 Тариф: <b>{tariff_name}</b>"
    )

    if alias:
        text += f"\n🏷️ Имя ключа: <b>{alias}</b>"

    if not update or not callback_data.edit:
        await callback_query.message.edit_text(
            text=text, reply_markup=build_key_edit_kb(key_details, email)
        )
    else:
        await callback_query.message.edit_text(
            text=text,
            reply_markup=await build_users_key_expiry_kb(
                session, callback_data.tg_id, email
            ),
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

    callback_data = AdminUserEditorCallback(
        action="users_key_edit", data=email, tg_id=tg_id
    )
    await handle_key_edit(
        callback_query=callback_query,
        callback_data=callback_data,
        session=session,
        update=False,
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_renew"), IsAdminFilter()
)
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
        select(Tariff)
        .where(Tariff.group_code == group_code, Tariff.is_active == True)
        .order_by(Tariff.id)
    )
    tariffs = result.scalars().all()

    if not tariffs:
        await callback_query.message.edit_text("❌ Нет активных тарифов в группе.")
        return

    builder = InlineKeyboardBuilder()
    for tariff in tariffs:
        builder.button(
            text=f"{tariff.name} – {int(tariff.price_rub)}₽",
            callback_data=f"confirm:{tariff.id}"
        )
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

    stmt = (
        update(Key)
        .where(Key.tg_id == tg_id, Key.email == email)
        .values(tariff_id=tariff_id)
    )
    await session.execute(stmt)
    await session.commit()
    await state.clear()

    callback_data = AdminUserEditorCallback(
        action="users_key_edit", data=email, tg_id=tg_id
    )

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
    data = await state.get_data()

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


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_expiry_edit"), IsAdminFilter()
)
async def handle_change_expiry(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    tg_id = callback_data.tg_id
    email = callback_data.data

    await callback_query.message.edit_reply_markup(
        reply_markup=await build_users_key_expiry_kb(session, tg_id, email)
    )


@router.callback_query(
    AdminUserKeyEditorCallback.filter(F.action == "add"), IsAdminFilter()
)
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
        await change_expiry_time(
            key_details["expiry_time"] + days * 24 * 3600 * 1000, email, session
        )
        await handle_key_edit(callback_query, callback_data, session, True)
        return

    await state.update_data(tg_id=tg_id, email=email, op_type="add")
    await state.set_state(UserEditorState.waiting_for_expiry_time)

    await callback_query.message.edit_text(
        text="✍️ Введите количество дней, которое хотите добавить к времени действия ключа:",
        reply_markup=build_users_key_show_kb(tg_id, email),
    )


@router.callback_query(
    AdminUserKeyEditorCallback.filter(F.action == "take"), IsAdminFilter()
)
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


@router.callback_query(
    AdminUserKeyEditorCallback.filter(F.action == "set"), IsAdminFilter()
)
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

    await callback_query.message.edit_text(
        text=text, reply_markup=build_users_key_show_kb(tg_id, email)
    )


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
        current_expiry_time = datetime.fromtimestamp(
            key_details["expiry_time"] / 1000, tz=MOSCOW_TZ
        )

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


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_update_key"), IsAdminFilter()
)
async def handle_update_key(
    callback_query: CallbackQuery, callback_data: AdminUserEditorCallback, session: Any
):
    tg_id = callback_data.tg_id
    email = callback_data.data

    await callback_query.message.edit_text(
        text=f"📡 Выберите кластер, на котором пересоздать ключ <b>{email}</b>:",
        reply_markup=await build_cluster_selection_kb(
            session, tg_id, email, action="confirm_admin_key_reissue"
        ),
    )


@router.callback_query(F.data.startswith("confirm_admin_key_reissue|"), IsAdminFilter())
async def confirm_admin_key_reissue(
    callback_query: CallbackQuery, session: Any, state: FSMContext
):
    _, tg_id, email, cluster_id = callback_query.data.split("|")
    tg_id = int(tg_id)

    try:
        servers = await get_servers(session)
        cluster_servers = servers.get(cluster_id, [])

        if USE_COUNTRY_SELECTION:
            unique_countries = {srv["server_name"] for srv in cluster_servers}
            await state.update_data(tg_id=tg_id, email=email, cluster_id=cluster_id)
            builder = InlineKeyboardBuilder()
            for country in sorted(unique_countries):
                builder.button(
                    text=country,
                    callback_data=f"admin_reissue_country|{tg_id}|{email}|{country}",
                )
            builder.row(
                InlineKeyboardButton(
                    text="Назад", callback_data=f"users_key_edit|{email}"
                )
            )
            await callback_query.message.edit_text(
                "🌍 Выберите сервер (страну) для пересоздания подписки:",
                reply_markup=builder.as_markup(),
            )
            return

        result = await session.execute(
            select(Key.remnawave_link).where(Key.email == email)
        )
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
async def admin_reissue_country(callback_query: CallbackQuery, session: AsyncSession):
    _, tg_id, email, country = callback_query.data.split("|")
    tg_id = int(tg_id)

    try:
        result = await session.execute(
            select(Key.remnawave_link, Key.tariff_id).where(Key.email == email)
        )
        remnawave_link, tariff_id = result.one_or_none() or (None, None)

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


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_delete_key"), IsAdminFilter()
)
async def handle_delete_key(
    callback_query: CallbackQuery, callback_data: AdminUserEditorCallback, session: Any
):
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
                    tasks.append(
                        delete_key_from_cluster(cluster_name, email, client_id, session)
                    )
            await asyncio.gather(*tasks, return_exceptions=True)

        await delete_key_from_servers()
        await delete_key(session, client_id)

        await callback_query.message.edit_text(
            text="✅ Ключ успешно удален.", reply_markup=kb
        )
    else:
        await callback_query.message.edit_text(
            text="🚫 Ключ не найден или уже удален.", reply_markup=kb
        )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_delete_user"), IsAdminFilter()
)
async def handle_delete_user(
    callback_query: CallbackQuery, callback_data: AdminUserEditorCallback
):
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

    result = await session.execute(
        select(Key.email, Key.client_id).where(Key.tg_id == tg_id)
    )
    key_records = result.all()

    async def delete_keys_from_servers():
        try:
            tasks = []
            servers = await get_servers(session=session)
            for email, client_id in key_records:
                for cluster_id, _cluster in servers.items():
                    tasks.append(
                        delete_key_from_cluster(cluster_id, email, client_id, session)
                    )
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            logger.error(
                f"Ошибка при удалении ключей с серверов для пользователя {tg_id}: {e}"
            )

    await delete_keys_from_servers()

    try:
        await delete_user_data(session, tg_id)

        await callback_query.message.edit_text(
            text=f"🗑️ Пользователь с ID {tg_id} был удален.",
            reply_markup=build_editor_kb(callback_data.tg_id),
        )
    except Exception as e:
        logger.error(
            f"Ошибка при удалении данных из базы данных для пользователя {tg_id}: {e}"
        )
        await callback_query.message.edit_text(
            text=f"❌ Произошла ошибка при удалении пользователя с ID {tg_id}. Попробуйте снова."
        )


async def process_user_search(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    tg_id: int,
    edit: bool = False,
) -> None:
    await state.clear()

    stmt_user = select(
        User.username, User.balance, User.created_at, User.updated_at
    ).where(User.tg_id == tg_id)
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
    created_at_str = created_at.astimezone(MOSCOW_TZ).strftime("%H:%M:%S %d.%m.%Y")
    updated_at_str = updated_at.astimezone(MOSCOW_TZ).strftime("%H:%M:%S %d.%m.%Y")

    stmt_ref_count = (
        select(func.count())
        .select_from(Referral)
        .where(Referral.referrer_tg_id == tg_id)
    )
    result_ref = await session.execute(stmt_ref_count)
    referral_count = result_ref.scalar_one()

    stmt_keys = select(Key).where(Key.tg_id == tg_id)
    result_keys = await session.execute(stmt_keys)
    key_records = result_keys.scalars().all()

    stmt_ban = (
        select(1)
        .where(
            (ManualBan.tg_id == tg_id)
            & (or_(ManualBan.until is None, ManualBan.until > func.now()))
        )
        .limit(1)
    )
    result_ban = await session.execute(stmt_ban)
    is_banned = result_ban.scalar_one_or_none() is not None

    text = (
        f"<b>📊 Информация о пользователе</b>"
        f"\n\n🆔 ID: <b>{tg_id}</b>"
        f"\n📄 Логин: <b>@{username}</b>"
        f"\n📅 Дата регистрации: <b>{created_at_str}</b>"
        f"\n🏃 Дата активности: <b>{updated_at_str}</b>"
        f"\n💰 Баланс: <b>{balance}</b>"
        f"\n👥 Количество рефералов: <b>{referral_count}</b>"
    )

    kb = build_user_edit_kb(tg_id, key_records, is_banned=is_banned)

    if edit:
        try:
            await message.edit_text(text=text, reply_markup=kb)
        except TelegramBadRequest:
            pass
    else:
        await message.answer(text=text, reply_markup=kb)


async def change_expiry_time(
    expiry_time: int, email: str, session: AsyncSession
) -> Exception | None:
    result = await session.execute(select(Key.client_id, Key.tariff_id, Key.server_id).where(Key.email == email))
    row = result.first()
    if not row:
        return ValueError(f"User with email {email} was not found")
    
    client_id, tariff_id, server_id = row
    if server_id is None:
        return ValueError(f"Key with client_id {client_id} was not found")

    traffic_limit = 0
    device_limit = None
    if tariff_id:
        result = await session.execute(
            select(Tariff.traffic_limit, Tariff.device_limit)
            .where(Tariff.id == tariff_id, Tariff.is_active.is_(True))
        )
        tariff = result.first()
        if tariff:
            traffic_limit = int(tariff[0]) if tariff[0] is not None else 0
            device_limit = int(tariff[1]) if tariff[1] is not None else None

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
    )
    
    await update_key_expiry(session, client_id, expiry_time)
    return None


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_traffic"), IsAdminFilter()
)
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

    await callback_query.message.edit_text(
        "⏳ Получаем данные о трафике, пожалуйста, подождите..."
    )

    traffic_data = await get_user_traffic(session, tg_id, email)

    if traffic_data["status"] == "error":
        await callback_query.message.edit_text(
            traffic_data["message"], reply_markup=build_editor_kb(tg_id, True)
        )
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

    await callback_query.message.edit_text(
        result_text, reply_markup=build_editor_kb(tg_id, True)
    )


@router.callback_query(
    AdminPanelCallback.filter(F.action == "restore_trials"), IsAdminFilter()
)
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
        "Только для тех, у кого нет активной подписки!",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(
    AdminPanelCallback.filter(F.action == "confirm_restore_trials"), IsAdminFilter()
)
async def restore_trials(callback_query: types.CallbackQuery, session: AsyncSession):
    active_keys_subq = (
        select(Key.tg_id)
        .where(Key.expiry_time > func.extract("epoch", func.now()) * 1000)
        .subquery()
    )
    stmt = (
        update(User)
        .where(~User.tg_id.in_(select(active_keys_subq.c.tg_id)))
        .where(User.trial != 0)
        .values(trial=0)
    )

    await session.execute(stmt)
    await session.commit()

    builder = InlineKeyboardBuilder()
    builder.row(build_admin_back_btn())

    await callback_query.message.edit_text(
        text="✅ Пробники успешно восстановлены для пользователей без активных подписок.",
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


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_create_key"), IsAdminFilter()
)
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
async def handle_create_key_country(
    callback_query: CallbackQuery, state: FSMContext, session
):
    country = callback_query.data
    await state.update_data(country=country)
    await state.set_state(UserEditorState.selecting_duration)

    builder = InlineKeyboardBuilder()

    result = await session.execute(
        select(Server.cluster_name).where(Server.server_name == country)
    )
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
        builder.button(
            text=f"{tariff['name']} — {tariff['price_rub']}₽",
            callback_data=f"tariff_{tariff['id']}"
        )

    builder.adjust(1)
    builder.row(build_admin_back_btn())

    await callback_query.message.edit_text(
        text=f"🕒 <b>Выберите срок действия ключа для страны <code>{country}</code>:</b>",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(UserEditorState.selecting_cluster, IsAdminFilter())
async def handle_create_key_cluster(
    callback_query: CallbackQuery, state: FSMContext, session
):
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
        builder.button(
            text=f"{tariff['name']} — {tariff['price_rub']}₽",
            callback_data=f"tariff_{tariff['id']}"
        )

    builder.adjust(1)
    builder.row(build_admin_back_btn())

    await callback_query.message.edit_text(
        text=f"🕒 <b>Выберите срок действия ключа для кластера <code>{cluster_name}</code>:</b>",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(UserEditorState.selecting_duration, IsAdminFilter())
async def handle_create_key_duration(
    callback_query: CallbackQuery, state: FSMContext, session
):
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
        email = generate_random_email()
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
            await callback_query.message.edit_text(
                "❌ Не удалось определить источник — страна или кластер."
            )

    except Exception as e:
        logger.error(f"[CreateKey] Ошибка при создании ключа: {e}")
        await callback_query.message.edit_text(
            "❌ Не удалось создать ключ. Попробуйте позже.",
            reply_markup=build_editor_kb(tg_id),
        )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_reset_traffic"), IsAdminFilter()
)
async def handle_reset_traffic(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    tg_id = callback_data.tg_id
    email = callback_data.data

    stmt = select(Key.server_id, Key.client_id).where(
        (Key.tg_id == tg_id) & (Key.email == email)
    )
    result = await session.execute(stmt)
    record = result.first()

    if not record:
        await callback_query.message.edit_text(
            "❌ Ключ не найден в базе данных.", reply_markup=build_editor_kb(tg_id)
        )
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


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_ban"), IsAdminFilter()
)
async def handle_user_ban(
    callback: CallbackQuery, callback_data: AdminUserEditorCallback, state: FSMContext
):
    await state.set_state(BanUserStates.waiting_for_reason)
    await state.update_data(tg_id=callback_data.tg_id)

    kb = InlineKeyboardBuilder()
    kb.row(build_editor_btn("⬅️ Назад", tg_id=callback_data.tg_id, edit=True))

    await callback.message.edit_text(
        text="✏️ Введите причину блокировки (или <code>-</code>, чтобы пропустить):",
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
async def handle_ban_duration_input(
    message: Message, state: FSMContext, session: AsyncSession
):
    user_data = await state.get_data()
    tg_id = user_data.get("tg_id")
    reason = user_data.get("reason")
    if reason == "-":
        reason = None

    try:
        days = int(message.text.strip())

        until = None
        if days > 0:
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
            f"✅ Пользователь <code>{tg_id}</code> забанен "
            f"{'навсегда' if not until else f'до {until:%Y-%m-%d %H:%M}'}."
            " Нажмите кнопку ниже для возврата в профиль."
        )

        await message.answer(text=text, reply_markup=build_editor_kb(tg_id, edit=True))

    except ValueError:
        await message.answer("❗ Введите корректное число дней.")
    finally:
        await state.clear()


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_unban"), IsAdminFilter()
)
async def handle_user_unban(
    callback: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    await session.execute(
        delete(ManualBan).where(ManualBan.tg_id == callback_data.tg_id)
    )
    await session.commit()

    text = f"✅ Пользователь <code>{callback_data.tg_id}</code> разблокирован. Нажмите кнопку ниже для возврата в профиль."

    await callback.message.edit_text(
        text=text, reply_markup=build_editor_kb(callback_data.tg_id, edit=True)
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_editor"), IsAdminFilter()
)
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

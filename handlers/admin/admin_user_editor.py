import asyncio
from datetime import datetime
from typing import Any

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import TOTAL_GB
from utils.database import get_client_id_by_email, get_servers_from_db, restore_trial, update_key_expiry
from filters.admin import IsAdminFilter
from handlers.keys.key_utils import delete_key_from_cluster, delete_key_from_db, renew_key_in_cluster
from handlers.utils import sanitize_key_name
from logger import logger

router = Router()


class UserEditorState(StatesGroup):
    waiting_for_tg_id = State()
    waiting_for_username = State()
    displaying_user_info = State()
    waiting_for_new_balance = State()
    waiting_for_key_name = State()
    waiting_for_expiry_time = State()


@router.callback_query(F.data == "search_by_tg_id", IsAdminFilter())
async def prompt_tg_id(callback_query: CallbackQuery, state: FSMContext):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="user_editor"))
    await callback_query.message.answer("🔍 Введите Telegram ID клиента:", reply_markup=builder.as_markup())
    await state.set_state(UserEditorState.waiting_for_tg_id)


@router.callback_query(F.data == "search_by_username", IsAdminFilter())
async def prompt_username(callback_query: CallbackQuery, state: FSMContext):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="user_editor"))
    await callback_query.message.answer("🔍 Введите Username клиента:", reply_markup=builder.as_markup())
    await state.set_state(UserEditorState.waiting_for_username)


@router.message(UserEditorState.waiting_for_username, IsAdminFilter())
async def handle_username_input(message: types.Message, state: FSMContext, session: Any):
    username = message.text.strip().lstrip("@")
    user_record = await session.fetchrow("SELECT tg_id FROM users WHERE username = $1", username)

    if not user_record:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="user_editor"))
        await message.answer("🔍 Пользователь с указанным username не найден. 🚫", reply_markup=builder.as_markup())
        await state.clear()
        return

    tg_id = user_record["tg_id"]
    username = await session.fetchval("SELECT username FROM users WHERE tg_id = $1", tg_id)
    balance = await session.fetchval("SELECT balance FROM connections WHERE tg_id = $1", tg_id)
    key_records = await session.fetch("SELECT email FROM keys WHERE tg_id = $1", tg_id)
    referral_count = await session.fetchval("SELECT COUNT(*) FROM referrals WHERE referrer_tg_id = $1", tg_id)

    if balance is None:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="user_editor"))
        await message.answer("🚫 Пользователь с указанным tg_id не найден. 🔍", reply_markup=builder.as_markup())
        await state.clear()
        return

    builder = InlineKeyboardBuilder()

    for (email,) in key_records:
        builder.row(InlineKeyboardButton(text=f"🔑 {email}", callback_data=f"edit_key_{email}"))

    builder.row(
        InlineKeyboardButton(
            text="📝 Изменить баланс",
            callback_data=f"change_balance_{tg_id}",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="🔄 Восстановить пробник",
            callback_data=f"restore_trial_{tg_id}",
        )
    )

    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="user_editor"))

    user_info = (
        f"📊 Информация о пользователе:\n\n"
        f"🆔 ID пользователя: <b>{tg_id}</b>\n"
        f"👤 Логин пользователя: <b>@{username}</b>\n"
        f"💰 Баланс: <b>{balance}</b>\n"
        f"👥 Количество рефералов: <b>{referral_count}</b>\n"
        f"🔑 Ключи (для редактирования нажмите на ключ):"
    )
    await message.answer(user_info, reply_markup=builder.as_markup())
    await state.set_state(UserEditorState.displaying_user_info)


@router.message(UserEditorState.waiting_for_tg_id, F.text.isdigit(), IsAdminFilter())
async def handle_tg_id_input(message: types.Message, state: FSMContext, session: Any):
    tg_id = int(message.text)
    username = await session.fetchval("SELECT username FROM users WHERE tg_id = $1", tg_id)
    balance = await session.fetchval("SELECT balance FROM connections WHERE tg_id = $1", tg_id)
    key_records = await session.fetch("SELECT email FROM keys WHERE tg_id = $1", tg_id)
    referral_count = await session.fetchval("SELECT COUNT(*) FROM referrals WHERE referrer_tg_id = $1", tg_id)

    if balance is None:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="user_editor"))
        await message.answer("❌ Пользователь с указанным tg_id не найден. 🔍", reply_markup=builder.as_markup())
        await state.clear()
        return

    builder = InlineKeyboardBuilder()

    for (email,) in key_records:
        builder.row(InlineKeyboardButton(text=f"🔑 {email}", callback_data=f"edit_key_{email}"))

    builder.row(
        InlineKeyboardButton(
            text="📝 Изменить баланс",
            callback_data=f"change_balance_{tg_id}",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="🔄 Восстановить пробник",
            callback_data=f"restore_trial_{tg_id}",
        )
    )

    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="user_editor"))

    user_info = (
        f"📊 Информация о пользователе:\n\n"
        f"🆔 ID пользователя: <b>{tg_id}</b>\n"
        f"👤 Логин пользователя: <b>@{username}</b>\n"
        f"💰 Баланс: <b>{balance}</b>\n"
        f"👥 Количество рефералов: <b>{referral_count}</b>\n"
        f"🔑 Ключи (для редактирования нажмите на ключ):"
    )
    await message.answer(user_info, reply_markup=builder.as_markup())
    await state.set_state(UserEditorState.displaying_user_info)


@router.callback_query(F.data.startswith("restore_trial_"), IsAdminFilter())
async def handle_restore_trial(callback_query: types.CallbackQuery, session: Any):
    tg_id = int(callback_query.data.split("_")[2])

    await restore_trial(tg_id, session)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Назад в меню администратора", callback_data="admin"))

    await callback_query.message.answer("✅ Триал успешно восстановлен.", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("change_balance_"), IsAdminFilter())
async def process_balance_change(callback_query: CallbackQuery, state: FSMContext):
    tg_id = int(callback_query.data.split("_")[2])
    await state.update_data(tg_id=tg_id)
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="user_editor"))
    await callback_query.message.answer("💸 Введите новую сумму баланса:", reply_markup=builder.as_markup())
    await state.set_state(UserEditorState.waiting_for_new_balance)


@router.message(UserEditorState.waiting_for_new_balance, IsAdminFilter())
async def handle_new_balance_input(message: types.Message, state: FSMContext, session: Any):
    if not message.text.isdigit() or int(message.text) < 0:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="user_editor"))
        await message.answer(
            "❌ Пожалуйста, введите корректную сумму для изменения баланса.", reply_markup=builder.as_markup()
        )
        return

    new_balance = int(message.text)
    user_data = await state.get_data()
    tg_id = user_data.get("tg_id")

    await session.execute(
        "UPDATE connections SET balance = $1 WHERE tg_id = $2",
        new_balance,
        tg_id,
    )

    response_message = f"✅ Баланс успешно изменен на <b>{new_balance}</b>."

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🔙 Назад в меню администратора",
            callback_data="admin",
        )
    )
    await message.answer(response_message, reply_markup=builder.as_markup())
    await state.clear()


async def get_key_details(email, session):
    record = await session.fetchrow(
        """
        SELECT k.key, k.expiry_time, k.server_id, c.tg_id, c.balance
        FROM keys k
        JOIN connections c ON k.tg_id = c.tg_id
        WHERE k.email = $1
        """,
        email,
    )

    if not record:
        return None

    servers = await get_servers_from_db()

    cluster_name = "Неизвестный кластер"
    for cluster_name, cluster_servers in servers.items():
        if any(server['inbound_id'] == record['server_id'] for server in cluster_servers):
            cluster_name = cluster_name
            break

    expiry_date = datetime.utcfromtimestamp(record['expiry_time'] / 1000)
    current_date = datetime.utcnow()
    time_left = expiry_date - current_date

    if time_left.total_seconds() <= 0:
        days_left_message = "<b>Ключ истек.</b>"
    elif time_left.days > 0:
        days_left_message = f"Осталось дней: <b>{time_left.days}</b>"
    else:
        hours_left = time_left.seconds // 3600
        days_left_message = f"Осталось часов: <b>{hours_left}</b>"

    return {
        'key': record['key'],
        'expiry_date': expiry_date.strftime("%d %B %Y года"),
        'days_left_message': days_left_message,
        'server_name': cluster_name,
        'balance': record['balance'],
        'tg_id': record['tg_id'],
    }


@router.callback_query(F.data.startswith("edit_key_"), IsAdminFilter())
async def process_key_edit(callback_query: CallbackQuery, session: Any):
    email = callback_query.data.split("_", 2)[2]
    key_details = await get_key_details(email, session)

    if not key_details:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="user_editor"))
        await callback_query.message.answer(
            "🔍 <b>Информация о ключе не найдена.</b> 🚫", reply_markup=builder.as_markup()
        )
        return

    response_message = (
        f"🔑 Ключ: <code>{key_details['key']}</code>\n"
        f"⏰ Дата истечения: <b>{key_details['expiry_date']}</b>\n"
        f"💰 Баланс пользователя: <b>{key_details['balance']}</b>\n"
        f"🌐 Кластер: <b>{key_details['server_name']}</b>"
    )

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="ℹ️ Получить информацию о юзере",
            callback_data=f"user_info|{key_details['tg_id']}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="⏳ Изменить время истечения",
            callback_data=f"change_expiry|{email}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="❌ Удалить ключ",
            callback_data=f"delete_key_admin|{email}",
        )
    )
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin"))

    await callback_query.message.answer(response_message, reply_markup=builder.as_markup())


@router.callback_query(F.data == "search_by_key_name", IsAdminFilter())
async def prompt_key_name(callback_query: CallbackQuery, state: FSMContext):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="user_editor"))
    await callback_query.message.answer("🔑 Введите имя ключа:", reply_markup=builder.as_markup())
    await state.set_state(UserEditorState.waiting_for_key_name)


@router.message(UserEditorState.waiting_for_key_name, IsAdminFilter())
async def handle_key_name_input(message: types.Message, state: FSMContext, session: Any):
    key_name = sanitize_key_name(message.text)
    key_details = await get_key_details(key_name, session)

    if not key_details:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="user_editor"))
        await message.answer(
            "🚫 Пользователь с указанным именем ключа не найден.",
            reply_markup=builder.as_markup(),
        )
        await state.clear()
        return

    response_message = (
        f"🔑 Ключ: <code>{key_details['key']}</code>\n"
        f"⏰ Дата истечения: <b>{key_details['expiry_date']}</b>\n"
        f"💰 Баланс пользователя: <b>{key_details['balance']}</b>\n"
        f"🌐 Сервер: <b>{key_details['server_name']}</b>"
    )

    key_buttons = InlineKeyboardBuilder()
    key_buttons.row(
        InlineKeyboardButton(
            text="ℹ️ Получить информацию о юзере",
            callback_data=f"user_info|{key_details['tg_id']}",
        )
    )
    key_buttons.row(
        InlineKeyboardButton(
            text="⏳ Изменить время истечения",
            callback_data=f"change_expiry|{key_name}",
        )
    )
    key_buttons.row(
        InlineKeyboardButton(
            text="❌ Удалить ключ",
            callback_data=f"delete_key_admin|{key_name}",
        )
    )
    key_buttons.row(InlineKeyboardButton(text="🔙 Назад", callback_data="user_editor"))

    await message.answer(response_message, reply_markup=key_buttons.as_markup())
    await state.clear()


@router.callback_query(F.data.startswith("change_expiry|"), IsAdminFilter())
async def prompt_expiry_change(callback_query: CallbackQuery, state: FSMContext):
    email = callback_query.data.split("|")[1]
    await callback_query.message.answer(
        f"⏳ Введите новое время истечения для ключа <b>{email}</b> в формате <code>YYYY-MM-DD HH:MM:SS</code>:"
    )
    await state.update_data(email=email)
    await state.set_state(UserEditorState.waiting_for_expiry_time)


@router.message(UserEditorState.waiting_for_expiry_time, IsAdminFilter())
async def handle_expiry_time_input(message: types.Message, state: FSMContext, session: Any):
    user_data = await state.get_data()
    email = user_data.get("email")

    if not email:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="user_editor"))
        await message.answer("📧 Email не найден в состоянии. 🚫", reply_markup=builder.as_markup())
        await state.clear()
        return

    try:
        expiry_time_str = message.text
        expiry_time = int(datetime.strptime(expiry_time_str, "%Y-%m-%d %H:%M:%S").timestamp() * 1000)

        client_id = await get_client_id_by_email(email)
        if client_id is None:
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="user_editor"))
            await message.answer(f"🚫 Клиент с email {email} не найден. 🔍", reply_markup=builder.as_markup())
            await state.clear()
            return

        record = await session.fetchrow("SELECT server_id FROM keys WHERE client_id = $1", client_id)
        if not record:
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="user_editor"))
            await message.answer("🚫 Клиент не найден в базе данных. 🔍", reply_markup=builder.as_markup())
            await state.clear()
            return

        clusters = await get_servers_from_db()

        async def update_key_on_all_servers():
            tasks = []
            for cluster_name, cluster_servers in clusters.items():
                for server in cluster_servers:
                    tasks.append(
                        asyncio.create_task(
                            renew_key_in_cluster(
                                cluster_name,
                                email,
                                client_id,
                                expiry_time,
                                total_gb=TOTAL_GB,
                            )
                        )
                    )
            await asyncio.gather(*tasks)

        await update_key_on_all_servers()

        await update_key_expiry(client_id, expiry_time)

        response_message = (
            f"✅ Время истечения ключа для клиента {client_id} ({email}) успешно обновлено на всех серверах."
        )

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin"))
        await message.answer(response_message, reply_markup=builder.as_markup())
    except ValueError:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="user_editor"))
        await message.answer(
            "❌ Пожалуйста, используйте формат: YYYY-MM-DD HH:MM:SS.", reply_markup=builder.as_markup()
        )
    except Exception as e:
        logger.error(e)
    await state.clear()


@router.callback_query(F.data.startswith("delete_key_admin|"), IsAdminFilter())
async def process_callback_delete_key(callback_query: types.CallbackQuery, session: Any):
    email = callback_query.data.split("|")[1]
    client_id = await session.fetchval("SELECT client_id FROM keys WHERE email = $1", email)

    if client_id is None:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="user_editor"))
        await callback_query.message.answer("🔍 Ключ не найден. 🚫", reply_markup=builder.as_markup())
        return

    builder = InlineKeyboardBuilder()
    builder.row(
        types.InlineKeyboardButton(
            text="✅ Да, удалить",
            callback_data=f"confirm_delete_admin|{client_id}",
        )
    )
    builder.row(types.InlineKeyboardButton(text="❌ Нет, отменить", callback_data="user_editor"))
    await callback_query.message.answer(
        "<b>❓ Вы уверены, что хотите удалить ключ?</b>",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("confirm_delete_admin|"), IsAdminFilter())
async def process_callback_confirm_delete(callback_query: types.CallbackQuery, session: Any):
    client_id = callback_query.data.split("|")[1]
    record = await session.fetchrow("SELECT email FROM keys WHERE client_id = $1", client_id)

    if record:
        email = record["email"]
        response_message = "✅ Ключ успешно удален."
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="view_keys"))

        clusters = await get_servers_from_db()

        async def delete_key_from_servers(email, client_id):
            tasks = []
            for cluster_name, cluster_servers in clusters.items():
                for server in cluster_servers:
                    tasks.append(delete_key_from_cluster(cluster_name, email, client_id))
            await asyncio.gather(*tasks)

        await delete_key_from_servers(email, client_id)
        await delete_key_from_db(client_id, session)

        await callback_query.message.answer(response_message, reply_markup=builder.as_markup())
    else:
        response_message = "🚫 Ключ не найден или уже удален."
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="view_keys"))
        await callback_query.message.answer(response_message, reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("user_info|"), IsAdminFilter())
async def handle_user_info(callback_query: types.CallbackQuery, state: FSMContext, session: Any):
    tg_id = int(callback_query.data.split("|")[1])
    username = await session.fetchval("SELECT username FROM users WHERE tg_id = $1", tg_id)
    balance = await session.fetchval("SELECT balance FROM connections WHERE tg_id = $1", tg_id)
    key_records = await session.fetch("SELECT email FROM keys WHERE tg_id = $1", tg_id)
    referral_count = await session.fetchval("SELECT COUNT(*) FROM referrals WHERE referrer_tg_id = $1", tg_id)

    builder = InlineKeyboardBuilder()

    for (email,) in key_records:
        builder.row(InlineKeyboardButton(text=f"🔑 {email}", callback_data=f"edit_key_{email}"))

    builder.row(
        InlineKeyboardButton(
            text="📝 Изменить баланс",
            callback_data=f"change_balance_{tg_id}",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="🔄 Восстановить пробник",
            callback_data=f"restore_trial_{tg_id}",
        )
    )

    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="user_editor"))

    user_info = (
        f"📊 Информация о пользователе:\n\n"
        f"🆔 ID пользователя: <b>{tg_id}</b>\n"
        f"👤 Логин пользователя: <b>@{username}</b>\n"
        f"💰 Баланс: <b>{balance}</b>\n"
        f"👥 Количество рефералов: <b>{referral_count}</b>\n"
        f"🔑 Ключи (для редактирования нажмите на ключ):"
    )
    await callback_query.message.answer(user_info, reply_markup=builder.as_markup())
    await state.set_state(UserEditorState.displaying_user_info)

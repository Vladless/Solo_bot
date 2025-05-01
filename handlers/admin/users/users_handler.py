import asyncio
import time
import uuid

from datetime import datetime, timedelta, timezone
from typing import Any

import pytz

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import RENEWAL_PRICES, TOTAL_GB, USE_COUNTRY_SELECTION
from database import (
    delete_key,
    delete_user_data,
    get_balance,
    get_client_id_by_email,
    get_key_details,
    get_servers,
    set_user_balance,
    update_balance,
    update_key_expiry,
    update_trial,
)
from filters.admin import IsAdminFilter
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
from utils.csv_export import export_referrals_csv

from ..panel.keyboard import AdminPanelCallback, build_admin_back_btn, build_admin_back_kb
from .keyboard import (
    AdminUserEditorCallback,
    AdminUserKeyEditorCallback,
    build_cluster_selection_kb,
    build_editor_kb,
    build_key_delete_kb,
    build_key_edit_kb,
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
    selecting_cluster = State()
    selecting_duration = State()
    selecting_country = State()


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
    key_details = await get_key_details(key_name, session)

    if not key_details:
        await message.answer(text="🚫 Пользователь с указанным именем ключа не найден.", reply_markup=kb)
        return

    await process_user_search(message, state, session, key_details["tg_id"])


@router.message(UserEditorState.waiting_for_user_data, IsAdminFilter())
async def handle_user_data_input(message: Message, state: FSMContext, session: Any):
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

        user = await session.fetchrow("SELECT tg_id FROM users WHERE username = $1", username)

        if not user:
            await message.answer(
                text="🚫 Пользователь с указанным Username не найден!",
                reply_markup=kb,
            )
            return

        tg_id = user["tg_id"]

    await process_user_search(message, state, session, tg_id)


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_send_message"),
    IsAdminFilter(),
)
async def handle_send_message(
    callback_query: types.CallbackQuery, callback_data: AdminUserEditorCallback, state: FSMContext
):
    tg_id = callback_data.tg_id

    await callback_query.message.edit_text(
        text="✉️ Введите текст сообщения, которое вы хотите отправить пользователю:", reply_markup=build_editor_kb(tg_id)
    )

    await state.update_data(tg_id=tg_id)
    await state.set_state(UserEditorState.waiting_for_message_text)


@router.message(UserEditorState.waiting_for_message_text, IsAdminFilter())
async def handle_message_text_input(message: Message, state: FSMContext):
    data = await state.get_data()
    tg_id = data.get("tg_id")

    try:
        await message.bot.send_message(chat_id=tg_id, text=message.text)
        await message.answer(text="✅ Сообщение успешно отправлено.", reply_markup=build_editor_kb(tg_id))
    except Exception as e:
        await message.answer(text=f"❌ Не удалось отправить сообщение: {e}", reply_markup=build_editor_kb(tg_id))

    await state.clear()


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_trial_restore"),
    IsAdminFilter(),
)
async def handle_trial_restore(
    callback_query: types.CallbackQuery, callback_data: AdminUserEditorCallback, session: Any
):
    tg_id = callback_data.tg_id

    await update_trial(tg_id, 0, session)
    await callback_query.message.edit_text(text="✅ Триал успешно восстановлен!", reply_markup=build_editor_kb(tg_id))


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_balance_edit"), IsAdminFilter())
async def handle_balance_change(callback_query: CallbackQuery, callback_data: AdminUserEditorCallback, session: Any):
    tg_id = callback_data.tg_id

    records = await session.fetch(
        """
       SELECT amount, payment_system, status, created_at
       FROM payments
       WHERE tg_id = $1
       ORDER BY created_at DESC
       LIMIT 5
       """,
        tg_id,
    )

    balance = await get_balance(tg_id)

    balance = int(balance)

    text = (
        f"<b>💵 Изменение баланса</b>"
        f"\n\n🆔 ID: <b>{tg_id}</b>"
        f"\n💰 Баланс: <b>{balance}Р</b>"
        f"\n📊 Последние операции (5):"
    )

    if records:
        for record in records:
            amount = record["amount"]
            payment_system = record["payment_system"]
            status = record["status"]
            date = record["created_at"].strftime("%Y-%m-%d %H:%M:%S")
            text += (
                f"\n<blockquote>💸 Сумма: {amount} | {payment_system}"
                f"\n📌 Статус: {status}"
                f"\n⏳ Дата: {date}</blockquote>"
            )
    else:
        text += "\n <i>🚫 Отсутствуют</i>"

    await callback_query.message.edit_text(text=text, reply_markup=build_users_balance_kb(tg_id))


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_balance_add"), IsAdminFilter())
async def handle_balance_add(
    callback_query: CallbackQuery, callback_data: AdminUserEditorCallback, state: FSMContext, session: Any
):
    tg_id = callback_data.tg_id
    amount = callback_data.data

    if amount is not None:
        amount = int(amount)
        if amount >= 0:
            await update_balance(tg_id, amount, session, is_admin=True)
        else:
            current_balance = await get_balance(tg_id)
            new_balance = max(0, current_balance + amount)
            await set_user_balance(tg_id, new_balance, session)

        await handle_balance_change(callback_query, callback_data, session)
        return

    await state.update_data(tg_id=tg_id, op_type="add")
    await state.set_state(UserEditorState.waiting_for_balance)

    await callback_query.message.edit_text(
        text="✍️ Введите сумму, которую хотите добавить на баланс пользователя:",
        reply_markup=build_users_balance_change_kb(tg_id),
    )


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_balance_take"), IsAdminFilter())
async def handle_balance_take(callback_query: CallbackQuery, callback_data: AdminUserEditorCallback, state: FSMContext):
    tg_id = callback_data.tg_id

    await state.update_data(tg_id=tg_id, op_type="take")
    await state.set_state(UserEditorState.waiting_for_balance)

    await callback_query.message.edit_text(
        text="✍️ Введите сумму, которую хотите вычесть из баланса пользователя:",
        reply_markup=build_users_balance_change_kb(tg_id),
    )


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_balance_set"), IsAdminFilter())
async def handle_balance_set(callback_query: CallbackQuery, callback_data: AdminUserEditorCallback, state: FSMContext):
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
            text="🚫 Пожалуйста, введите корректную сумму!", reply_markup=build_users_balance_change_kb(tg_id)
        )
        return

    amount = int(message.text)

    if op_type == "add":
        text = f"✅ К балансу пользователя добавлено <b>{amount}Р</b>"
        await update_balance(tg_id, amount, session)
    elif op_type == "take":
        current_balance = await get_balance(tg_id)
        new_balance = max(0, current_balance - amount)
        deducted = current_balance if amount > current_balance else amount
        text = f"✅ Из баланса пользователя было вычтено <b>{deducted}Р</b>"
        await set_user_balance(tg_id, new_balance, session)
    else:
        text = f"✅ Баланс пользователя изменен на <b>{amount}Р</b>"
        await set_user_balance(tg_id, amount, session)

    await message.answer(text=text, reply_markup=build_users_balance_change_kb(tg_id))


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_key_edit"), IsAdminFilter())
async def handle_key_edit(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback | AdminUserKeyEditorCallback,
    session: Any,
    update: bool = False,
):
    email = callback_data.data
    key_details = await get_key_details(email, session)

    if not key_details:
        await callback_query.message.edit_text(
            text="🚫 Информация о ключе не найдена.",
            reply_markup=build_editor_kb(callback_data.tg_id),
        )
        return

    key_value = key_details.get("key") or key_details.get("remnawave_link") or "—"

    text = (
        f"<b>🔑 Информация о ключе</b>"
        f"\n\n<code>{key_value}</code>"
        f"\n\n⏰ Дата истечения: <b>{key_details['expiry_date']} (UTC)</b>"
        f"\n🌐 Кластер: <b>{key_details['cluster_name']}</b>"
        f"\n🆔 ID клиента: <b>{key_details['tg_id']}</b>"
    )

    if not update or not callback_data.edit:
        await callback_query.message.edit_text(text=text, reply_markup=build_key_edit_kb(key_details, email))
    else:
        await callback_query.message.edit_text(
            text=text, reply_markup=build_users_key_expiry_kb(callback_data.tg_id, email)
        )


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_expiry_edit"), IsAdminFilter())
async def handle_change_expiry(callback_query: CallbackQuery, callback_data: AdminUserEditorCallback):
    tg_id = callback_data.tg_id
    email = callback_data.data

    await callback_query.message.edit_reply_markup(reply_markup=build_users_key_expiry_kb(tg_id, email))


@router.callback_query(AdminUserKeyEditorCallback.filter(F.action == "add"), IsAdminFilter())
async def handle_expiry_add(
    callback_query: CallbackQuery, callback_data: AdminUserKeyEditorCallback, state: FSMContext, session: Any
):
    tg_id = callback_data.tg_id
    email = callback_data.data
    month = callback_data.month

    key_details = await get_key_details(email, session)

    if not key_details:
        await callback_query.message.edit_text(
            text="🚫 Информация о ключе не найдена.",
            reply_markup=build_editor_kb(tg_id),
        )
        return

    if month:
        await change_expiry_time(key_details["expiry_time"] + month * 30 * 24 * 3600 * 1000, email, session)
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
    callback_query: CallbackQuery, callback_data: AdminUserKeyEditorCallback, state: FSMContext
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
    callback_query: CallbackQuery, callback_data: AdminUserKeyEditorCallback, state: FSMContext, session: Any
):
    tg_id = callback_data.tg_id
    email = callback_data.data

    key_details = await get_key_details(email, session)

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

    key_details = await get_key_details(email, session)

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
async def confirm_admin_key_reissue(callback_query: CallbackQuery, session: Any):
    _, tg_id, email, cluster_id = callback_query.data.split("|")
    tg_id = int(tg_id)

    try:
        await update_subscription(tg_id, email, session, cluster_override=cluster_id)
        await handle_key_edit(
            callback_query, AdminUserEditorCallback(tg_id=tg_id, data=email, action="view_key"), session, True
        )
    except Exception as e:
        logger.error(f"Ошибка при перевыпуске ключа {email}: {e}")
        await callback_query.message.answer(f"❗ Ошибка: {e}")


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_delete_key"), IsAdminFilter())
async def handle_delete_key(callback_query: CallbackQuery, callback_data: AdminUserEditorCallback, session: Any):
    email = callback_data.data
    client_id = await session.fetchval("SELECT client_id FROM keys WHERE email = $1", email)

    if client_id is None:
        await callback_query.message.edit_text(
            text="🚫 Ключ не найден!", reply_markup=build_editor_kb(callback_data.tg_id)
        )
        return

    await callback_query.message.edit_text(
        text="❓ Вы уверены, что хотите удалить ключ?", reply_markup=build_key_delete_kb(callback_data.tg_id, email)
    )


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_delete_key_confirm"), IsAdminFilter())
async def handle_delete_key_confirm(
    callback_query: types.CallbackQuery, callback_data: AdminUserEditorCallback, session: Any
):
    email = callback_data.data
    record = await session.fetchrow("SELECT client_id FROM keys WHERE email = $1", email)

    kb = build_editor_kb(callback_data.tg_id)

    if record:
        client_id = record["client_id"]
        clusters = await get_servers()

        async def delete_key_from_servers():
            tasks = []
            for cluster_name, cluster_servers in clusters.items():
                for _ in cluster_servers:
                    tasks.append(delete_key_from_cluster(cluster_name, email, client_id))
            await asyncio.gather(*tasks, return_exceptions=True)

        await delete_key_from_servers()
        await delete_key(client_id, session)

        await callback_query.message.edit_text(text="✅ Ключ успешно удален.", reply_markup=kb)
    else:
        await callback_query.message.edit_text(text="🚫 Ключ не найден или уже удален.", reply_markup=kb)


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_delete_user"), IsAdminFilter())
async def handle_delete_user(callback_query: CallbackQuery, callback_data: AdminUserEditorCallback):
    tg_id = callback_data.tg_id
    await callback_query.message.edit_text(
        text=f"❗️ Вы уверены, что хотите удалить пользователя с ID {tg_id}?", reply_markup=build_user_delete_kb(tg_id)
    )


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_delete_user_confirm"), IsAdminFilter())
async def handle_delete_user_confirm(
    callback_query: types.CallbackQuery, callback_data: AdminUserEditorCallback, session: Any
):
    tg_id = callback_data.tg_id
    key_records = await session.fetch("SELECT email, client_id FROM keys WHERE tg_id = $1", tg_id)

    async def delete_keys_from_servers():
        try:
            tasks = []
            for email, client_id in key_records:
                servers = await get_servers()
                for cluster_id, _cluster in servers.items():
                    tasks.append(delete_key_from_cluster(cluster_id, email, client_id))
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            logger.error(f"Ошибка при удалении ключей с серверов для пользователя {tg_id}: {e}")

    await delete_keys_from_servers()

    try:
        await delete_user_data(session, tg_id)
        await callback_query.message.edit_text(
            text=f"🗑️ Пользователь с ID {tg_id} был удален.", reply_markup=build_editor_kb(callback_data.tg_id)
        )
    except Exception as e:
        logger.error(f"Ошибка при удалении данных из базы данных для пользователя {tg_id}: {e}")
        await callback_query.message.edit_text(
            text=f"❌ Произошла ошибка при удалении пользователя с ID {tg_id}. Попробуйте снова."
        )


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_editor"), IsAdminFilter())
async def handle_editor(
    callback_query: types.CallbackQuery, callback_data: AdminUserEditorCallback, state: FSMContext, session: Any
):
    await process_user_search(callback_query.message, state, session, callback_data.tg_id, callback_data.edit)


async def process_user_search(
    message: types.Message, state: FSMContext, session: Any, tg_id: int, edit: bool = False
) -> None:
    await state.clear()

    user_data = await session.fetchrow(
        "SELECT username, balance, created_at, updated_at FROM users WHERE tg_id = $1", tg_id
    )
    if not user_data:
        await message.answer(
            text="🚫 Пользователь с указанным ID не найден!",
            reply_markup=build_admin_back_kb(),
        )
        return

    balance = int(user_data["balance"] or 0)
    username = user_data["username"]
    created_at = user_data["created_at"].astimezone(MOSCOW_TZ).strftime("%H:%M:%S %d.%m.%Y")
    updated_at = user_data["updated_at"].astimezone(MOSCOW_TZ).strftime("%H:%M:%S %d.%m.%Y")

    referral_count = await session.fetchval("SELECT COUNT(*) FROM referrals WHERE referrer_tg_id = $1", tg_id)
    key_records = await session.fetch("SELECT email, expiry_time FROM keys WHERE tg_id = $1", tg_id)

    text = (
        f"<b>📊 Информация о пользователе</b>"
        f"\n\n🆔 ID: <b>{tg_id}</b>"
        f"\n📄 Логин: <b>@{username}</b>"
        f"\n📅 Дата регистрации: <b>{created_at}</b>"
        f"\n🏃 Дата активности: <b>{updated_at}</b>"
        f"\n💰 Баланс: <b>{balance}</b>"
        f"\n👥 Количество рефералов: <b>{referral_count}</b>"
    )

    kb = build_user_edit_kb(tg_id, key_records)

    if edit:
        try:
            await message.edit_text(text=text, reply_markup=kb)
        except TelegramBadRequest:
            pass
    else:
        await message.answer(text=text, reply_markup=kb)


async def change_expiry_time(expiry_time: int, email: str, session: Any) -> Exception | None:
    client_id = await get_client_id_by_email(email)

    if client_id is None:
        return ValueError(f"User with email {email} was not found")

    server_id = await session.fetchrow("SELECT server_id FROM keys WHERE client_id = $1", client_id)

    if not server_id:
        return ValueError(f"User with client_id {server_id} was not found")

    clusters = await get_servers()
    added_days = max((expiry_time - int(time.time() * 1000)) / (1000 * 86400), 1)
    total_gb = int((added_days / 30) * TOTAL_GB * 1024**3)

    async def update_key_on_all_servers():
        tasks = [
            asyncio.create_task(
                renew_key_in_cluster(
                    cluster_name,
                    email,
                    client_id,
                    expiry_time,
                    total_gb=total_gb,
                )
            )
            for cluster_name in clusters
        ]

        await asyncio.gather(*tasks, return_exceptions=True)

    await update_key_on_all_servers()
    await update_key_expiry(client_id, expiry_time, session)


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_traffic"), IsAdminFilter())
async def handle_user_traffic(
    callback_query: types.CallbackQuery, callback_data: AdminUserEditorCallback, session: Any
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
    builder.button(text="✅ Подтвердить", callback_data=AdminPanelCallback(action="confirm_restore_trials").pack())
    builder.row(build_admin_back_btn())

    await callback_query.message.edit_text(
        text="⚠ Вы уверены, что хотите восстановить пробники для пользователей? \n\n"
        "Только для тех, у кого нет активной подписки!",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "confirm_restore_trials"), IsAdminFilter())
async def restore_trials(callback_query: types.CallbackQuery, session: Any):
    """
    Восстанавливает пробники для пользователей, у которых нет активной подписки.
    """
    query = """
        UPDATE users
        SET trial = 0
        WHERE tg_id IN (
            SELECT u.tg_id
            FROM users u
            LEFT JOIN (
                SELECT tg_id
                FROM keys
                WHERE expiry_time > EXTRACT(EPOCH FROM NOW()) * 1000
            ) k ON u.tg_id = k.tg_id
            WHERE k.tg_id IS NULL AND u.trial != 0
        )
    """
    await session.execute(query)

    builder = InlineKeyboardBuilder()
    builder.row(build_admin_back_btn())

    await callback_query.message.edit_text(
        text="✅ Пробники успешно восстановлены для пользователей без активных подписок.",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_export_referrals"), IsAdminFilter())
async def handle_users_export_referrals(
    callback_query: types.CallbackQuery, callback_data: AdminUserEditorCallback, session: Any
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
        document=csv_file, caption=f"Список рефералов для пользователя {referrer_tg_id}."
    )


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_create_key"), IsAdminFilter())
async def handle_create_key_start(
    callback_query: CallbackQuery, callback_data: AdminUserEditorCallback, state: FSMContext, session: Any
):
    tg_id = callback_data.tg_id
    await state.update_data(tg_id=tg_id)

    if USE_COUNTRY_SELECTION:
        await state.set_state(UserEditorState.selecting_country)

        rows = await session.fetch("SELECT DISTINCT server_name FROM servers ORDER BY server_name")
        countries = [row["server_name"] for row in rows]

        if not countries:
            await callback_query.message.edit_text(
                "❌ Нет доступных стран для создания ключа.", reply_markup=build_editor_kb(tg_id)
            )
            return

        builder = InlineKeyboardBuilder()
        for country in countries:
            builder.button(text=country, callback_data=country)
        builder.adjust(1)
        builder.row(build_admin_back_btn())

        await callback_query.message.edit_text(
            "🌍 <b>Выберите страну для создания ключа:</b>", reply_markup=builder.as_markup()
        )
        return

    await state.set_state(UserEditorState.selecting_cluster)

    servers = await get_servers(session)
    cluster_names = list(servers.keys())

    if not cluster_names:
        await callback_query.message.edit_text(
            "❌ Нет доступных кластеров для создания ключа.", reply_markup=build_editor_kb(tg_id)
        )
        return

    builder = InlineKeyboardBuilder()
    for cluster in cluster_names:
        builder.button(text=f"🌐 {cluster}", callback_data=cluster)
    builder.row(build_admin_back_btn())

    await callback_query.message.edit_text(
        "🌐 <b>Выберите кластер для создания ключа:</b>", reply_markup=builder.as_markup()
    )


@router.callback_query(UserEditorState.selecting_country, IsAdminFilter())
async def handle_create_key_country(callback_query: CallbackQuery, state: FSMContext):
    country = callback_query.data
    await state.update_data(country=country)
    await state.set_state(UserEditorState.selecting_duration)

    builder = InlineKeyboardBuilder()
    for months, _ in RENEWAL_PRICES.items():
        builder.button(text=f"{months} мес.", callback_data=str(months))
    builder.adjust(1)
    builder.row(build_admin_back_btn())

    await callback_query.message.edit_text(
        text=f"🕒 <b>Выберите срок действия ключа для страны {country}:</b>", reply_markup=builder.as_markup()
    )


@router.callback_query(UserEditorState.selecting_cluster, IsAdminFilter())
async def handle_create_key_cluster(callback_query: CallbackQuery, state: FSMContext):
    cluster_name = callback_query.data

    await state.update_data(cluster_name=cluster_name)
    await state.set_state(UserEditorState.selecting_duration)

    builder = InlineKeyboardBuilder()
    for months, _ in RENEWAL_PRICES.items():
        builder.button(text=f"{months} мес.", callback_data=str(months))
    builder.adjust(1)
    builder.row(build_admin_back_btn())

    await callback_query.message.edit_text(
        text=f"🕒 <b>Выберите срок действия ключа для кластера {cluster_name}:</b>", reply_markup=builder.as_markup()
    )


@router.callback_query(UserEditorState.selecting_duration, IsAdminFilter())
async def handle_create_key_duration(callback_query: CallbackQuery, state: FSMContext, session: Any):
    try:
        months = int(callback_query.data)
        data = await state.get_data()
        tg_id = data["tg_id"]

        client_id = str(uuid.uuid4())
        email = generate_random_email()
        expiry = datetime.now(tz=timezone.utc) + timedelta(days=30 * months)
        expiry_ms = int(expiry.timestamp() * 1000)

        if USE_COUNTRY_SELECTION and "country" in data:
            country = data["country"]
            await create_key_on_cluster(country, tg_id, client_id, email, expiry_ms, plan=months, session=session)

            await state.clear()
            await callback_query.message.edit_text(
                f"✅ Ключ успешно создан для страны <b>{country}</b> на {months} мес.",
                reply_markup=build_editor_kb(tg_id),
            )

        elif "cluster_name" in data:
            cluster_name = data["cluster_name"]
            await create_key_on_cluster(cluster_name, tg_id, client_id, email, expiry_ms, plan=months, session=session)

            await state.clear()
            await callback_query.message.edit_text(
                f"✅ Ключ успешно создан в кластере <b>{cluster_name}</b> на {months} мес.",
                reply_markup=build_editor_kb(tg_id),
            )

        else:
            await callback_query.message.edit_text("❌ Не удалось определить источник — страна или кластер.")

    except Exception as e:
        logger.error(f"Ошибка при создании ключа: {e}")
        await callback_query.message.edit_text(
            "❌ Не удалось создать ключ. Попробуйте позже.", reply_markup=build_editor_kb(data.get("tg_id", 0))
        )


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_reset_traffic"), IsAdminFilter())
async def handle_reset_traffic(callback_query: CallbackQuery, callback_data: AdminUserEditorCallback, session: Any):
    tg_id = callback_data.tg_id
    email = callback_data.data

    record = await session.fetchrow(
        "SELECT server_id, client_id FROM keys WHERE tg_id = $1 AND email = $2",
        tg_id,
        email,
    )

    if not record:
        await callback_query.message.edit_text("❌ Ключ не найден в базе данных.", reply_markup=build_editor_kb(tg_id))
        return

    cluster_id = record["server_id"]

    try:
        await reset_traffic_in_cluster(cluster_id, email)
        await callback_query.message.edit_text(
            f"✅ Трафик для ключа <b>{email}</b> успешно сброшен.", reply_markup=build_editor_kb(tg_id)
        )
    except Exception as e:
        logger.error(f"Ошибка при сбросе трафика: {e}")
        await callback_query.message.edit_text(
            "❌ Произошла ошибка при сбросе трафика. Попробуйте позже.", reply_markup=build_editor_kb(tg_id)
        )

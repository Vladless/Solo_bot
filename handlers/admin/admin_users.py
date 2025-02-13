import asyncio
from datetime import datetime, timezone
from typing import Any

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import TOTAL_GB
from database import (
    delete_key,
    delete_user_data,
    get_balance,
    get_client_id_by_email,
    get_servers,
    update_balance,
    update_key_expiry,
    update_trial,
)
from filters.admin import IsAdminFilter
from handlers.keys.key_utils import delete_key_from_cluster, get_user_traffic, renew_key_in_cluster, update_subscription
from handlers.utils import sanitize_key_name
from keyboards.admin.panel_kb import AdminPanelCallback, build_admin_back_kb
from keyboards.admin.users_kb import (
    AdminUserEditorCallback,
    AdminUserKeyEditorCallback,
    build_editor_kb,
    build_key_delete_kb,
    build_key_edit_kb,
    build_user_delete_kb,
    build_user_edit_kb,
    build_user_key_kb,
    build_users_balance_change_kb,
    build_users_balance_kb,
    build_users_key_expiry_kb,
    build_users_key_show_kb,
)
from logger import logger

router = Router()


class UserEditorState(StatesGroup):
    # search
    waiting_for_user_data = State()
    waiting_for_key_name = State()
    # updating data
    waiting_for_balance = State()
    waiting_for_expiry_time = State()
    waiting_for_message_text = State()


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
        # Удаление '@' символа в начале сообщения
        username = message.text.strip().lstrip("@")
        # Удаление начала ссылки на профиль
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

    if amount:
        await update_balance(tg_id, int(amount), session, is_admin=True)
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
        text = f"✅ Из баланса пользователя было вычтено <b>{amount}Р</b>"
        await update_balance(tg_id, -amount, session)
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

    text = (
        f"<b>🔑 Информация о ключе</b>"
        f"\n\n<code>{key_details['key']}</code>"
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

    if op_type == "add":
        days = int(message.text)
        text = f"✅ Ко времени действия ключа добавлено <b>{days} дн.</b>"
        await change_expiry_time(key_details["expiry_time"] + days * 24 * 3600 * 1000, email, session)
    elif op_type == "take":
        days = int(message.text)
        text = f"✅ Из времени действия ключа вычтено <b>{days} дн.</b>"
        await change_expiry_time(key_details["expiry_time"] - days * 24 * 3600 * 1000, email, session)
    else:
        try:
            expiry_time = int(datetime.strptime(message.text, "%Y-%m-%d %H:%M").timestamp() * 1000)
            text = f"✅ Время действия ключа изменено на <b>{message.text}</b>"
            await change_expiry_time(expiry_time, email, session)
        except ValueError:
            text = "🚫 Пожалуйста, используйте корректный формат даты!"
        except Exception as e:
            text = f"❗ Произошла ошибка во время изменения времени действия ключа: {e}"

    await message.answer(text=text, reply_markup=build_users_key_show_kb(tg_id, email))


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_update_key"), IsAdminFilter())
async def handle_update_key(callback_query: CallbackQuery, callback_data: AdminUserEditorCallback, session: Any):
    tg_id = callback_data.tg_id
    email = callback_data.data

    try:
        await update_subscription(tg_id, email, session)
        await handle_key_edit(callback_query, callback_data, session, True)
    except TelegramBadRequest:
        pass
    except Exception as e:
        logger.error(f"Ошибка при обновлении ключа {email} администратором: {e}")
        await callback_query.message.answer(
            text=f"❗ Произошла ошибка при обновлении ключа: {e}", reply_markup=build_user_key_kb(tg_id, email)
        )


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
            await asyncio.gather(*tasks)

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
            await asyncio.gather(*tasks)
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

    balance = await session.fetchval("SELECT balance FROM connections WHERE tg_id = $1", tg_id)

    if balance is None:
        await message.answer(
            text="🚫 Пользователь с указанным ID не найден!",
            reply_markup=build_admin_back_kb(),
        )
        return

    balance = int(balance)

    username = await session.fetchval("SELECT username FROM users WHERE tg_id = $1", tg_id)
    key_records = await session.fetch("SELECT email, expiry_time FROM keys WHERE tg_id = $1", tg_id)
    referral_count = await session.fetchval("SELECT COUNT(*) FROM referrals WHERE referrer_tg_id = $1", tg_id)

    text = (
        f"<b>📊 Информация о пользователе</b>"
        f"\n\n🆔 ID: <b>{tg_id}</b>"
        f"\n📄 Логин: <b>@{username}</b>"
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


async def get_key_details(email, session):
    record = await session.fetchrow(
        """
        SELECT k.client_id, k.key, k.expiry_time, k.server_id, c.tg_id, c.balance
        FROM keys k
        JOIN connections c ON k.tg_id = c.tg_id
        WHERE k.email = $1
        """,
        email,
    )

    if not record:
        return None

    cluster_name = record["server_id"]
    expiry_date = datetime.fromtimestamp(record["expiry_time"] / 1000, tz=timezone.utc)

    return {
        "client_id": record["client_id"],
        "balance": record["balance"],
        "tg_id": record["tg_id"],
        "key": record["key"],
        "cluster_name": cluster_name,
        "expiry_time": record["expiry_time"],
        "expiry_date": expiry_date.strftime("%d %B %Y года %H:%M"),
    }


async def change_expiry_time(expiry_time: int, email: str, session: Any) -> Exception | None:
    client_id = await get_client_id_by_email(email)

    if client_id is None:
        return ValueError(f"User with email {email} was not found")

    server_id = await session.fetchrow("SELECT server_id FROM keys WHERE client_id = $1", client_id)

    if not server_id:
        return ValueError(f"User with client_id {server_id} was not found")

    clusters = await get_servers()

    async def update_key_on_all_servers():
        tasks = [
            asyncio.create_task(
                renew_key_in_cluster(
                    cluster_name,
                    email,
                    client_id,
                    expiry_time,
                    total_gb=TOTAL_GB,
                )
            )
            for cluster_name in clusters
        ]

        await asyncio.gather(*tasks)

    await update_key_on_all_servers()
    await update_key_expiry(client_id, expiry_time, session)


async def set_user_balance(tg_id: int, balance: int, session: Any) -> None:
    try:
        await session.execute(
            "UPDATE connections SET balance = $1 WHERE tg_id = $2",
            balance,
            tg_id,
        )
    except Exception as e:
        logger.error(f"Ошибка при установке баланса для пользователя {tg_id}: {e}")


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

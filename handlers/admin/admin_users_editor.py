import asyncio
from datetime import datetime
from typing import Any

from aiogram import Bot, F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery
from config import TOTAL_GB

from database import delete_user_data, get_client_id_by_email, get_servers_from_db, restore_trial, update_key_expiry
from filters.admin import IsAdminFilter
from handlers.keys.key_utils import (
    delete_key_from_cluster,
    delete_key_from_db,
    renew_key_in_cluster,
)
from handlers.utils import sanitize_key_name
from keyboards.admin.panel_kb import AdminPanelCallback, build_admin_back_kb
from keyboards.admin.users_editor_kb import build_user_edit_kb, build_key_edit_kb, build_key_delete_kb, \
    build_user_delete_kb, AdminUserEditorCallback
from logger import logger

router = Router()


class UserEditorState(StatesGroup):
    # search
    waiting_for_user_data = State()
    waiting_for_key_name = State()
    # updating data
    waiting_for_new_balance = State()
    waiting_for_expiry_time = State()
    waiting_for_message_text = State()


@router.callback_query(
    AdminPanelCallback.filter(F.action == "users_search"),
    IsAdminFilter(),
)
async def handle_users_search(
        callback_query: CallbackQuery,
        state: FSMContext
):
    text = (
        "🔍 Введите ID или Username пользователя для поиска:"
        "\n— ID - числовой айди пользователя"
        "\n— Username - юзернейм пользователя, начинающийся с @ или https://t.me/"
    )

    await state.set_state(UserEditorState.waiting_for_user_data)
    await callback_query.message.answer(
        text=text,
        reply_markup=build_admin_back_kb("users_editor")
    )


@router.callback_query(
    AdminPanelCallback.filter(F.action == "users_search_key"),
    IsAdminFilter(),
)
async def handle_users_search_key(
        callback_query: CallbackQuery,
        state: FSMContext
):
    await state.set_state(UserEditorState.waiting_for_key_name)
    await callback_query.message.answer(
        text="🔑 Введите имя ключа для поиска:",
        reply_markup=build_admin_back_kb("users_editor")
    )


@router.message(
    UserEditorState.waiting_for_user_data,
    IsAdminFilter()
)
async def handle_user_data_input(
        message: types.Message,
        state: FSMContext,
        session: Any
):
    kb = build_admin_back_kb("users_editor")

    if not message.text:
        await message.reply(
            text="💢 Пожалуйста, отправьте текстовое сообщение.",
            reply_markup=kb
        )
        return

    if message.text.isdigit():
        tg_id = int(message.text)
    else:
        # Удаление '@' символа в начале сообщения
        username = message.text.strip().lstrip('@')
        # Удаление начала ссылки на профиль
        username = username.replace('https://t.me/', '')

        user = await session.fetchrow(
            "SELECT tg_id FROM users WHERE username = $1", username
        )

        if not user:
            await message.answer(
                text="🔍 Пользователь с указанным username не найден. 🚫",
                reply_markup=kb,
            )
            await state.clear()
            return

        tg_id = user["tg_id"]

    await process_user_search(message, state, session, tg_id)


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_send_message"),
    IsAdminFilter(),
)
async def handle_send_message(
        callback_query: types.CallbackQuery,
        callback_data: AdminUserEditorCallback,
        state: FSMContext
):
    tg_id = callback_data.data
    await callback_query.message.answer(
        "✉️ Введите текст сообщения, которое вы хотите отправить пользователю."
    )
    await state.update_data(target_tg_id=tg_id)
    await state.set_state(UserEditorState.waiting_for_message_text)


@router.message(
    UserEditorState.waiting_for_message_text,
    IsAdminFilter()
)
async def handle_message_text_input(
        message: types.Message,
        state: FSMContext,
        bot: Bot
):
    data = await state.get_data()
    target_tg_id = data.get("target_tg_id")

    if not target_tg_id:
        await message.answer("🚫 Ошибка: ID пользователя не найден.")
        await state.clear()
        return

    try:
        await bot.send_message(chat_id=target_tg_id, text=message.text)
        await message.answer("✅ Сообщение успешно отправлено.")
    except Exception as e:
        await message.answer(f"❌ Не удалось отправить сообщение: {e}")

    await state.clear()


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_trial_restore"),
    IsAdminFilter(),
)
async def handle_restore_trial(
        callback_query: types.CallbackQuery,
        callback_data: AdminUserEditorCallback,
        session: Any
):
    tg_id = int(callback_data.data)
    await restore_trial(tg_id, session)
    await callback_query.message.answer(
        text="✅ Триал успешно восстановлен.",
        reply_markup=build_admin_back_kb("admin")
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_balance_change"),
    IsAdminFilter()
)
async def process_balance_change(
        callback_query: CallbackQuery,
        callback_data: AdminUserEditorCallback,
        state: FSMContext
):
    tg_id = int(callback_data.data)
    await state.update_data(tg_id=tg_id)
    await callback_query.message.answer(
        text="💸 Введите новую сумму баланса:",
        reply_markup=build_admin_back_kb("users_editor")
    )
    await state.set_state(UserEditorState.waiting_for_new_balance)


@router.message(
    UserEditorState.waiting_for_new_balance,
    IsAdminFilter()
)
async def handle_new_balance_input(
        message: types.Message,
        state: FSMContext,
        session: Any
):
    if not message.text.isdigit() or int(message.text) < 0:
        await message.answer(
            text="❌ Пожалуйста, введите корректную сумму для изменения баланса.",
            reply_markup=build_admin_back_kb("users_editor"),
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

    await message.answer(
        text=f"✅ Баланс успешно изменен на <b>{new_balance}</b>.",
        reply_markup=build_admin_back_kb("admin")
    )
    await state.clear()


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_key_edit"),
    IsAdminFilter()
)
async def process_key_edit(
        callback_query: CallbackQuery,
        callback_data: AdminUserEditorCallback,
        session: Any
):
    email = callback_data.data
    key_details = await get_key_details(email, session)

    if not key_details:
        await callback_query.message.answer(
            text="🔍 <b>Информация о ключе не найдена.</b> 🚫",
            reply_markup=build_admin_back_kb("users_editor"),
        )
        return

    response_message = (
        f"🔑 Ключ: <code>{key_details['key']}</code>\n"
        f"⏰ Дата истечения: <b>{key_details['expiry_date']}</b>\n"
        f"💰 Баланс пользователя: <b>{key_details['balance']}</b>\n"
        f"🌐 Кластер: <b>{key_details['server_name']}</b>"
    )

    await callback_query.message.answer(
        text=response_message,
        reply_markup=build_key_edit_kb(key_details, email)
    )


@router.message(
    UserEditorState.waiting_for_key_name,
    IsAdminFilter()
)
async def handle_key_name_input(
        message: types.Message,
        state: FSMContext,
        session: Any
):
    key_name = sanitize_key_name(message.text)
    key_details = await get_key_details(key_name, session)

    if not key_details:
        await message.answer(
            text="🚫 Пользователь с указанным именем ключа не найден.",
            reply_markup=build_admin_back_kb("users_editor")
        )
        await state.clear()
        return

    response_message = (
        f"🔑 Ключ: <code>{key_details['key']}</code>\n"
        f"⏰ Дата истечения: <b>{key_details['expiry_date']}</b>\n"
        f"💰 Баланс пользователя: <b>{key_details['balance']}</b>\n"
        f"🌐 Сервер: <b>{key_details['server_name']}</b>"
    )

    await message.answer(
        text=response_message,
        reply_markup=build_key_edit_kb(key_details, key_name)
    )
    await state.clear()


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_change_expiry"),
    IsAdminFilter()
)
async def prompt_expiry_change(
        callback_query: CallbackQuery,
        callback_data: AdminUserEditorCallback,
        state: FSMContext
):
    email = callback_data.data
    await callback_query.message.answer(
        text=f"⏳ Введите новое время истечения для ключа <b>{email}</b> в формате <code>YYYY-MM-DD HH:MM:SS</code>:"
    )
    await state.update_data(email=email)
    await state.set_state(UserEditorState.waiting_for_expiry_time)


@router.message(
    UserEditorState.waiting_for_expiry_time,
    IsAdminFilter()
)
async def handle_expiry_time_input(
        message: types.Message,
        state: FSMContext,
        session: Any
):
    user_data = await state.get_data()
    email = user_data.get("email")

    if not email:
        await message.answer(
            text="📧 Email не найден в состоянии. 🚫",
            reply_markup=build_admin_back_kb("users_editor")
        )
        await state.clear()
        return

    try:
        expiry_time_str = message.text
        expiry_time = int(
            datetime.strptime(expiry_time_str, "%Y-%m-%d %H:%M:%S").timestamp() * 1000
        )

        client_id = await get_client_id_by_email(email)
        if client_id is None:
            await message.answer(
                text=f"🚫 Клиент с email {email} не найден. 🔍",
                reply_markup=build_admin_back_kb("users_editor"),
            )
            await state.clear()
            return

        record = await session.fetchrow(
            "SELECT server_id FROM keys WHERE client_id = $1", client_id
        )
        if not record:
            await message.answer(
                text="🚫 Клиент не найден в базе данных. 🔍",
                reply_markup=build_admin_back_kb("users_editor"),
            )
            await state.clear()
            return

        clusters = await get_servers_from_db()

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

        await update_key_expiry(client_id, expiry_time)

        response_message = f"✅ Время истечения ключа для клиента {client_id} ({email}) успешно обновлено на всех серверах."

        await message.answer(
            text=response_message,
            reply_markup=build_admin_back_kb("admin")
        )
    except ValueError:
        await message.answer(
            text="❌ Пожалуйста, используйте формат: YYYY-MM-DD HH:MM:SS.",
            reply_markup=build_admin_back_kb("users_editor"),
        )
    except Exception as e:
        logger.error(e)
    await state.clear()


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_delete_key"),
    IsAdminFilter()
)
async def process_callback_delete_key(
        callback_query: types.CallbackQuery,
        callback_data: AdminUserEditorCallback,
        session: Any
):
    email = callback_data.data
    client_id = await session.fetchval(
        "SELECT client_id FROM keys WHERE email = $1", email
    )

    if client_id is None:
        await callback_query.message.answer(
            text="🔍 Ключ не найден. 🚫",
            reply_markup=build_admin_back_kb("users_editor")
        )
        return

    await callback_query.message.answer(
        text="<b>❓ Вы уверены, что хотите удалить ключ?</b>",
        reply_markup=build_key_delete_kb(client_id),
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_delete_key_confirm"),
    IsAdminFilter()
)
async def process_callback_confirm_delete(
        callback_query: types.CallbackQuery,
        callback_data: AdminUserEditorCallback,
        session: Any
):
    client_id = callback_data.data
    record = await session.fetchrow(
        "SELECT email FROM keys WHERE client_id = $1", client_id
    )

    kb = build_admin_back_kb("users_editor")

    if record:
        clusters = await get_servers_from_db()

        async def delete_key_from_servers(email, client_id):
            tasks = []
            for cluster_name, cluster_servers in clusters.items():
                for server in cluster_servers:
                    tasks.append(
                        delete_key_from_cluster(cluster_name, email, client_id)
                    )
            await asyncio.gather(*tasks)

        await delete_key_from_servers(record["email"], client_id)
        await delete_key_from_db(client_id, session)

        await callback_query.message.answer(
            text="✅ Ключ успешно удален.",
            reply_markup=kb
        )
    else:
        await callback_query.message.answer(
            text="🚫 Ключ не найден или уже удален.",
            reply_markup=kb
        )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_delete_user"),
    IsAdminFilter()
)
async def delete_user(
        callback_query: types.CallbackQuery,
        callback_data: AdminUserEditorCallback,
        session: Any
):
    tg_id = int(callback_data.data)
    key_records = await session.fetch("SELECT email, client_id FROM keys WHERE tg_id = $1", tg_id)

    async def delete_keys_from_servers():
        try:
            tasks = []
            for email, client_id in key_records:
                servers = await get_servers_from_db()
                for cluster_id, cluster in servers.items():
                    tasks.append(delete_key_from_cluster(cluster_id, email, client_id))
            await asyncio.gather(*tasks)
        except Exception as e:
            logger.error(f"Ошибка при удалении ключей с серверов для пользователя {tg_id}: {e}")

    await delete_keys_from_servers()

    try:
        await delete_user_data(session, tg_id)
        await callback_query.message.answer(
            text=f"🗑️ Пользователь с ID {tg_id} был удален.",
            reply_markup=build_admin_back_kb("users_editor")
        )
    except Exception as e:
        logger.error(f"Ошибка при удалении данных из базы данных для пользователя {tg_id}: {e}")
        await callback_query.message.answer(
            text=f"❌ Произошла ошибка при удалении пользователя с ID {tg_id}. Попробуйте снова."
        )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_delete_user_confirm"),
    IsAdminFilter()
)
async def confirm_delete_user(
        callback_query: types.CallbackQuery,
        callback_data: AdminUserEditorCallback
):
    tg_id = int(callback_data.data)
    await callback_query.message.answer(
        text=f"Вы уверены, что хотите удалить пользователя с ID {tg_id}?",
        reply_markup=build_user_delete_kb(tg_id)
    )


async def process_user_search(message: types.Message, state: FSMContext, session: Any, tg_id: int) -> None:
    username = await session.fetchval(
        "SELECT username FROM users WHERE tg_id = $1", tg_id
    )
    balance = await session.fetchval(
        "SELECT balance FROM connections WHERE tg_id = $1", tg_id
    )

    if balance is None:
        await message.answer(
            text="🚫 Пользователь с указанным ID не найден!",
            reply_markup=build_admin_back_kb("users_editor"),
        )
        return

    key_records = await session.fetch("SELECT email FROM keys WHERE tg_id = $1", tg_id)
    referral_count = await session.fetchval(
        "SELECT COUNT(*) FROM referrals WHERE referrer_tg_id = $1", tg_id
    )

    text = (
        f"📊 Информация о пользователе:\n\n"
        f"🆔 ID пользователя: <b>{tg_id}</b>\n"
        f"👤 Логин пользователя: <b>@{username}</b>\n"
        f"💰 Баланс: <b>{balance}</b>\n"
        f"👥 Количество рефералов: <b>{referral_count}</b>\n"
        f"🔑 Ключи (для редактирования нажмите на ключ):"
    )

    await message.answer(
        text=text,
        reply_markup=build_user_edit_kb(tg_id, key_records)
    )

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
        if any(
                server["inbound_id"] == record["server_id"] for server in cluster_servers
        ):
            cluster_name = cluster_name
            break

    expiry_date = datetime.utcfromtimestamp(record["expiry_time"] / 1000)
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
        "key": record["key"],
        "expiry_date": expiry_date.strftime("%d %B %Y года"),
        "days_left_message": days_left_message,
        "server_name": cluster_name,
        "balance": record["balance"],
        "tg_id": record["tg_id"],
    }

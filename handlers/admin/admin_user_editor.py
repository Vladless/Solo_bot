import asyncio
from datetime import datetime

import asyncpg
from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import bot
from config import CLUSTERS, DATABASE_URL, TOTAL_GB
from database import get_client_id_by_email, restore_trial, update_key_expiry
from filters.admin import IsAdminFilter
from handlers.admin.admin_panel import back_to_admin_menu
from handlers.keys.key_utils import delete_key_from_cluster, renew_key_in_cluster
from handlers.utils import sanitize_key_name
from logger import logger

router = Router()


class UserEditorState(StatesGroup):
    waiting_for_tg_id = State()
    displaying_user_info = State()
    waiting_for_new_balance = State()
    waiting_for_key_name = State()
    waiting_for_expiry_time = State()


@router.callback_query(F.data == "search_by_tg_id", IsAdminFilter())
async def prompt_tg_id(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.message.edit_text("🔍 Введите Telegram ID клиента:")
    await state.set_state(UserEditorState.waiting_for_tg_id)


@router.message(UserEditorState.waiting_for_tg_id, F.text.isdigit(), IsAdminFilter())
async def handle_tg_id_input(message: types.Message, state: FSMContext):
    tg_id = int(message.text)

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        balance = await conn.fetchval(
            "SELECT balance FROM connections WHERE tg_id = $1", tg_id
        )
        key_records = await conn.fetch("SELECT email FROM keys WHERE tg_id = $1", tg_id)
        referral_count = await conn.fetchval(
            "SELECT COUNT(*) FROM referrals WHERE referrer_tg_id = $1", tg_id
        )

        if balance is None:
            await message.reply("Пользователь с указанным tg_id не найден.")
            await state.clear()
            return

        builder = InlineKeyboardBuilder()

        for (email,) in key_records:
            builder.row(
                InlineKeyboardButton(
                    text=f"🔑 {email}", callback_data=f"edit_key_{email}"
                )
            )

        builder.row(
            InlineKeyboardButton(
                text="📝 Изменить баланс", callback_data=f"change_balance_{tg_id}"
            )
        )

        builder.row(
            InlineKeyboardButton(
                text="🔄 Восстановить пробник", callback_data=f"restore_trial_{tg_id}"
            )
        )

        builder.row(
            InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_user_editor")
        )

        user_info = (
            f"📊 Информация о пользователе:\n"
            f"💰 Баланс: <b>{balance}</b>\n"
            f"👥 Количество рефералов: <b>{referral_count}</b>\n"
            f"🔑 Ключи (для редактирования нажмите на ключ):"
        )
        await message.reply(
            user_info, reply_markup=builder.as_markup(), parse_mode="HTML"
        )
        await state.set_state(UserEditorState.displaying_user_info)

    finally:
        await conn.close()


@router.callback_query(F.data.startswith("restore_trial_"), IsAdminFilter())
async def handle_restore_trial(callback_query: types.CallbackQuery):
    tg_id = int(callback_query.data.split("_")[2])

    await restore_trial(tg_id)

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🔙 Назад в меню администратора", callback_data="back_to_user_editor"
        )
    )

    await callback_query.message.edit_text(
        "✅ Триал успешно восстановлен.", reply_markup=builder.as_markup()
    )


@router.callback_query(F.data.startswith("change_balance_"), IsAdminFilter())
async def process_balance_change(callback_query: CallbackQuery, state: FSMContext):
    tg_id = int(callback_query.data.split("_")[2])
    await state.update_data(tg_id=tg_id)

    await callback_query.message.edit_text("💸 Введите новую сумму баланса:")
    await callback_query.answer()
    await state.set_state(UserEditorState.waiting_for_new_balance)


@router.message(UserEditorState.waiting_for_new_balance, IsAdminFilter())
async def handle_new_balance_input(message: types.Message, state: FSMContext):
    if not message.text.isdigit() or int(message.text) < 0:
        await message.reply(
            "❌ Пожалуйста, введите корректную сумму для изменения баланса."
        )
        return

    new_balance = int(message.text)
    user_data = await state.get_data()
    tg_id = user_data.get("tg_id")

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute(
            "UPDATE connections SET balance = $1 WHERE tg_id = $2", new_balance, tg_id
        )

        response_message = f"✅ Баланс успешно изменен на <b>{new_balance}</b>."

        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(
                text="🔙 Назад в меню администратора",
                callback_data="back_to_user_editor",
            )
        )
        await message.reply(
            response_message, reply_markup=builder.as_markup(), parse_mode="HTML"
        )

    finally:
        await conn.close()

    await state.clear()


@router.callback_query(F.data.startswith("edit_key_"), IsAdminFilter())
async def process_key_edit(callback_query: CallbackQuery):
    email = callback_query.data.split("_", 2)[2]

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            record = await conn.fetchrow(
                """
                SELECT k.key, k.expiry_time, k.server_id 
                FROM keys k
                WHERE k.email = $1
            """,
                email,
            )

            if record:
                key = record["key"]
                expiry_time = record["expiry_time"]
                server_id = record["server_id"]
                server_name = "Неизвестный сервер"

                for cluster in CLUSTERS.values():
                    if server_id in cluster:
                        server_name = cluster[server_id].get(
                            "name", "Неизвестный сервер"
                        )
                        break

                expiry_date = datetime.utcfromtimestamp(expiry_time / 1000)
                current_date = datetime.utcnow()
                time_left = expiry_date - current_date

                if time_left.total_seconds() <= 0:
                    days_left_message = "<b>Ключ истек.</b>"
                elif time_left.days > 0:
                    days_left_message = f"Осталось дней: <b>{time_left.days}</b>"
                else:
                    hours_left = time_left.seconds // 3600
                    days_left_message = f"Осталось часов: <b>{hours_left}</b>"

                formatted_expiry_date = expiry_date.strftime("%d %B %Y года")

                response_message = (
                    f"Ключ: <pre>{key}</pre>\n"
                    f"Дата истечения: <b>{formatted_expiry_date}</b>\n"
                    f"{days_left_message}\n"
                    f"Сервер: <b>{server_name}</b>"
                )

                builder = InlineKeyboardBuilder()
                builder.row(
                    InlineKeyboardButton(
                        text="⏳ Изменить время истечения",
                        callback_data=f"change_expiry|{email}",
                    ),
                    InlineKeyboardButton(
                        text="❌ Удалить ключ",
                        callback_data=f"delete_key_admin|{email}",
                    ),
                )
                builder.row(
                    InlineKeyboardButton(
                        text="🔙 Назад", callback_data="back_to_user_editor"
                    )
                )
                await callback_query.message.edit_text(
                    response_message,
                    reply_markup=builder.as_markup(),
                    parse_mode="HTML",
                )
            else:
                await callback_query.message.edit_text(
                    "<b>Информация о ключе не найдена.</b>", parse_mode="HTML"
                )

        finally:
            await conn.close()

    except Exception as e:
        logger.error(f"Ошибка при получении информации о ключе: {e}")
    await callback_query.answer()


@router.callback_query(F.data == "search_by_key_name", IsAdminFilter())
async def prompt_key_name(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.message.edit_text("🔑 Введите имя ключа:")
    await state.set_state(UserEditorState.waiting_for_key_name)


@router.message(UserEditorState.waiting_for_key_name, IsAdminFilter())
async def handle_key_name_input(message: types.Message, state: FSMContext):
    key_name = sanitize_key_name(message.text)

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        user_records = await conn.fetch(
            """
            SELECT c.tg_id, c.balance, k.email, k.key, k.expiry_time, k.server_id 
            FROM connections c 
            JOIN keys k ON c.tg_id = k.tg_id 
            WHERE k.email = $1
        """,
            key_name,
        )

        if not user_records:
            builder = InlineKeyboardBuilder()
            builder.row(
                InlineKeyboardButton(
                    text="🔙 Назад в меню администратора",
                    callback_data="back_to_user_editor",
                )
            )

            await message.reply(
                "🚫 Пользователь с указанным именем ключа не найден.",
                reply_markup=builder.as_markup(),
            )
            await state.clear()
            return

        response_messages = []
        key_buttons = InlineKeyboardBuilder()

        for record in user_records:
            balance = record["balance"]
            email = record["email"]
            key = record["key"]
            expiry_time = record["expiry_time"]
            server_id = record["server_id"]
            server_name = "Неизвестный сервер"

            for cluster in CLUSTERS.values():
                if server_id in cluster:
                    server_name = cluster[server_id].get("name", "Неизвестный сервер")
                    break

            expiry_date = datetime.utcfromtimestamp(expiry_time / 1000).strftime(
                "%d %B %Y"
            )

            response_messages.append(
                f"🔑 Ключ: <pre>{key}</pre>\n"
                f"⏰ Дата истечения: <b>{expiry_date}</b>\n"
                f"💰 Баланс пользователя: <b>{balance}</b>\n"
                f"🌐 Сервер: <b>{server_name}</b>"
            )

            key_buttons.row(
                InlineKeyboardButton(
                    text="⏳ Изменить время истечения",
                    callback_data=f"change_expiry|{email}",
                )
            )
            key_buttons.row(
                InlineKeyboardButton(
                    text="❌ Удалить ключ", callback_data=f"delete_key_admin|{email}"
                )
            )

        key_buttons.row(
            InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_user_editor")
        )

        await message.reply(
            "\n".join(response_messages),
            reply_markup=key_buttons.as_markup(),
            parse_mode="HTML",
        )

    finally:
        await conn.close()

    await state.clear()


@router.callback_query(F.data.startswith("change_expiry|"), IsAdminFilter())
async def prompt_expiry_change(callback_query: CallbackQuery, state: FSMContext):
    email = callback_query.data.split("|")[1]
    await callback_query.message.edit_text(
        f"⏳ Введите новое время истечения для ключа <b>{email}</b> в формате <code>YYYY-MM-DD HH:MM:SS</code>:",
        parse_mode="HTML",
    )
    await state.update_data(email=email)
    await state.set_state(UserEditorState.waiting_for_expiry_time)


@router.message(UserEditorState.waiting_for_expiry_time, IsAdminFilter())
async def handle_expiry_time_input(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    email = user_data.get("email")

    if not email:
        await message.reply("Email не найден в состоянии.")
        await state.clear()
        return

    try:
        expiry_time_str = message.text
        expiry_time = int(
            datetime.strptime(expiry_time_str, "%Y-%m-%d %H:%M:%S").timestamp() * 1000
        )

        client_id = await get_client_id_by_email(email)
        if client_id is None:
            await message.reply(f"Клиент с email {email} не найден.")
            await state.clear()
            return

        conn = await asyncpg.connect(DATABASE_URL)
        try:
            record = await conn.fetchrow(
                "SELECT server_id FROM keys WHERE client_id = $1", client_id
            )
            if not record:
                await message.reply("Клиент не найден в базе данных.")
                await state.clear()
                return

            async def update_key_on_all_servers():
                tasks = []
                for cluster_id in CLUSTERS:
                    tasks.append(
                        asyncio.create_task(
                            renew_key_in_cluster(
                                cluster_id,
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

            response_message = f"✅ Время истечения ключа для клиента {client_id} ({email}) успешно обновлено на всех серверах."

            builder = InlineKeyboardBuilder()
            builder.row(
                InlineKeyboardButton(
                    text="🔙 Назад", callback_data="back_to_user_editor"
                )
            )
            await message.reply(
                response_message, reply_markup=builder.as_markup(), parse_mode="HTML"
            )

        finally:
            await conn.close()

    except ValueError:
        await message.reply("❌ Пожалуйста, используйте формат: YYYY-MM-DD HH:MM:SS.")
    except Exception as e:
        await message.reply(f"Произошла ошибка: {e}")

    await state.clear()


@router.callback_query(F.data.startswith("delete_key_admin|"), IsAdminFilter())
async def process_callback_delete_key(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    email = callback_query.data.split("|")[1]

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        client_id = await conn.fetchval(
            "SELECT client_id FROM keys WHERE email = $1", email
        )

        if client_id is None:
            await bot.edit_message_text(
                "Ключ не найден.",
                chat_id=tg_id,
                message_id=callback_query.message.message_id,
            )
            return

        builder = InlineKeyboardBuilder()
        builder.row(
            types.InlineKeyboardButton(
                text="✅ Да, удалить", callback_data=f"confirm_delete_admin|{client_id}"
            )
        )
        builder.row(
            types.InlineKeyboardButton(
                text="❌ Нет, отменить", callback_data="view_keys"
            )
        )
        await bot.edit_message_text(
            "<b>❓ Вы уверены, что хотите удалить ключ?</b>",
            chat_id=tg_id,
            message_id=callback_query.message.message_id,
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
    finally:
        await conn.close()

    await callback_query.answer()


@router.callback_query(F.data.startswith("confirm_delete_admin|"), IsAdminFilter())
async def process_callback_confirm_delete(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    client_id = callback_query.data.split("|")[1]

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            record = await conn.fetchrow(
                "SELECT email FROM keys WHERE client_id = $1", client_id
            )

            if record:
                email = record["email"]
                response_message = "✅ Ключ успешно удален."
                builder = InlineKeyboardBuilder()
                builder.row(
                    InlineKeyboardButton(text="⬅️ Назад", callback_data="view_keys")
                )

                async def delete_key_from_servers(email, client_id):
                    tasks = []
                    for cluster_id in CLUSTERS:
                        tasks.append(
                            delete_key_from_cluster(cluster_id, email, client_id)
                        )
                    await asyncio.gather(*tasks)

                await delete_key_from_servers(email, client_id)
                await delete_key_from_db(client_id)

                await bot.edit_message_text(
                    response_message,
                    chat_id=tg_id,
                    message_id=callback_query.message.message_id,
                    reply_markup=builder.as_markup(),
                )
            else:
                response_message = "🚫 Ключ не найден или уже удален."
                builder = InlineKeyboardBuilder()
                builder.row(
                    InlineKeyboardButton(text="⬅️ Назад", callback_data="view_keys")
                )
                await bot.edit_message_text(
                    response_message,
                    chat_id=tg_id,
                    message_id=callback_query.message.message_id,
                    reply_markup=builder.as_markup(),
                )

        finally:
            await conn.close()

    except Exception as e:
        await bot.edit_message_text(
            f"Ошибка при удалении ключа: {e}",
            chat_id=tg_id,
            message_id=callback_query.message.message_id,
        )

    await callback_query.answer()


async def delete_key_from_db(client_id):
    """Удаление ключа из базы данных"""
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        await conn.execute("DELETE FROM keys WHERE client_id = $1", client_id)
    except Exception as e:
        logger.error(f"Ошибка при удалении ключа {client_id} из базы данных: {e}")
    finally:
        await conn.close()


@router.callback_query(F.data == "back_to_user_editor")
async def back_to_user_editor(callback_query: CallbackQuery):
    await back_to_admin_menu(callback_query)

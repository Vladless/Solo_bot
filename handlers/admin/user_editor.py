from datetime import datetime

import asyncpg
from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup)

from auth import login_with_credentials
from bot import bot
from client import delete_client, extend_client_key_admin
from config import ADMIN_PASSWORD, ADMIN_USERNAME, DATABASE_URL, SERVERS
from database import (get_client_id_by_email, get_tg_id_by_client_id,
                      update_key_expiry)
from handlers.admin.admin_panel import back_to_admin_menu

router = Router()

class UserEditorState(StatesGroup):
    waiting_for_tg_id = State()
    displaying_user_info = State()
    waiting_for_new_balance = State()  
    waiting_for_key_name = State()
    waiting_for_expiry_time = State() 

@router.callback_query(lambda c: c.data == "search_by_tg_id")
async def prompt_tg_id(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.message.edit_text("Введите tg_id клиента:")
    await state.set_state(UserEditorState.waiting_for_tg_id)

@router.message(UserEditorState.waiting_for_tg_id, F.text.isdigit())
async def handle_tg_id_input(message: types.Message, state: FSMContext):
    tg_id = int(message.text)

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        balance = await conn.fetchval("SELECT balance FROM connections WHERE tg_id = $1", tg_id)
        key_records = await conn.fetch("SELECT email FROM keys WHERE tg_id = $1", tg_id)
        referral_count = await conn.fetchval("SELECT COUNT(*) FROM referrals WHERE referrer_tg_id = $1", tg_id)

        if balance is None:
            await message.reply("Пользователь с указанным tg_id не найден.")
            await state.clear()
            return

        key_buttons = [
            [InlineKeyboardButton(text=email, callback_data=f"edit_key_{email}")]
            for email, in key_records
        ]
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            *key_buttons,
            [InlineKeyboardButton(text="📝 Изменить баланс", callback_data=f"change_balance_{tg_id}")],
            [InlineKeyboardButton(text="Назад", callback_data="back_to_user_editor")]
        ])

        user_info = (
            f"Информация о пользователе:\n"
            f"Баланс: <b>{balance}</b>\n"
            f"Количество рефералов: <b>{referral_count}</b>\n"
            f"Ключи (для редактирования нажмите на ключ):"
        )
        await message.reply(user_info, reply_markup=keyboard, parse_mode="HTML")
        await state.set_state(UserEditorState.displaying_user_info)

    finally:
        await conn.close()

@router.callback_query(lambda c: c.data.startswith('change_balance_'))
async def process_balance_change(callback_query: CallbackQuery, state: FSMContext):
    tg_id = int(callback_query.data.split('_')[2]) 
    await state.update_data(tg_id=tg_id) 

    await callback_query.message.edit_text("Введите новую сумму баланса:")
    await callback_query.answer()
    await state.set_state(UserEditorState.waiting_for_new_balance)  

@router.message(UserEditorState.waiting_for_new_balance)
async def handle_new_balance_input(message: types.Message, state: FSMContext):
    if not message.text.isdigit() or int(message.text) < 0:
        await message.reply("Пожалуйста, введите корректную сумму для изменения баланса.")
        return

    new_balance = int(message.text)
    user_data = await state.get_data()
    tg_id = user_data.get('tg_id')  

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute("UPDATE connections SET balance = $1 WHERE tg_id = $2", new_balance, tg_id)

        response_message = f"Баланс успешно изменен на <b>{new_balance}</b>."
        
        back_button = InlineKeyboardButton(text="Назад в меню админа", callback_data="back_to_user_editor")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[back_button]])

        await message.reply(response_message, reply_markup=keyboard, parse_mode="HTML")

    finally:
        await conn.close()

    await state.clear()  
 

@router.callback_query(lambda c: c.data.startswith('edit_key_'))
async def process_key_edit(callback_query: CallbackQuery):
    email = callback_query.data.split('_', 2)[2]

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            record = await conn.fetchrow('''
                SELECT k.key, k.expiry_time, k.server_id 
                FROM keys k
                WHERE k.email = $1
            ''', email)

            if record:
                key = record['key']
                expiry_time = record['expiry_time']
                server_id = record['server_id']
                server_name = SERVERS.get(server_id, {}).get('name', 'Неизвестный сервер')

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

                formatted_expiry_date = expiry_date.strftime('%d %B %Y года')

                response_message = (
                    f"Ключ: <pre>{key}</pre>\n"
                    f"Дата истечения: <b>{formatted_expiry_date}</b>\n"
                    f"{days_left_message}\n"
                    f"Сервер: <b>{server_name}</b>"
                )

                change_expiry_button = types.InlineKeyboardButton(text='⏳ Изменить время истечения', callback_data=f'change_expiry|{email}')
                delete_button = types.InlineKeyboardButton(text='❌ Удалить ключ', callback_data=f'delete_key_admin|{email}')

                keyboard = types.InlineKeyboardMarkup(
                    inline_keyboard=[
                        [change_expiry_button, delete_button],
                        [InlineKeyboardButton(text="Назад", callback_data="back_to_user_editor")]  # Кнопка "Назад"
                    ]
                )

                await callback_query.message.edit_text(response_message, reply_markup=keyboard, parse_mode="HTML")
            else:
                await callback_query.message.edit_text("<b>Информация о ключе не найдена.</b>", parse_mode="HTML")

        finally:
            await conn.close()

    except Exception as e:
        await handle_error(callback_query.from_user.id, callback_query, f"Ошибка при получении информации о ключе: {e}")

    await callback_query.answer()

@router.callback_query(lambda c: c.data == "search_by_key_name")
async def prompt_key_name(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.message.edit_text("Введите имя ключа:")
    await state.set_state(UserEditorState.waiting_for_key_name)

@router.message(UserEditorState.waiting_for_key_name)
async def handle_key_name_input(message: types.Message, state: FSMContext):
    key_name = message.text

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        user_records = await conn.fetch('''
            SELECT c.tg_id, c.balance, k.email, k.key, k.expiry_time, k.server_id 
            FROM connections c 
            JOIN keys k ON c.tg_id = k.tg_id 
            WHERE k.email = $1
        ''', key_name)

        if not user_records:
            await message.reply("Пользователь с указанным именем ключа не найден.")
            await state.clear()
            return

        response_messages = []
        key_buttons = []

        for record in user_records:
            tg_id = record['tg_id']
            balance = record['balance']
            email = record['email']
            key = record['key']
            expiry_time = record['expiry_time']
            server_id = record['server_id']
            server_name = SERVERS.get(server_id, {}).get('name', 'Неизвестный сервер')

            expiry_date = datetime.utcfromtimestamp(expiry_time / 1000).strftime('%d %B %Y')

            response_messages.append(
                f"Ключ: <pre>{key}</pre>\n"
                f"Дата истечения: <b>{expiry_date}</b>\n"
                f"Баланс пользователя: <b>{balance}</b>\n"
                f"Сервер: <b>{server_name}</b>"
            )

            change_expiry_button = InlineKeyboardButton(text='⏳ Изменить время истечения', callback_data=f'change_expiry|{email}')
            delete_button = InlineKeyboardButton(text='❌ Удалить ключ', callback_data=f'delete_key_admin|{email}')

            key_buttons.append([change_expiry_button, delete_button])

        key_buttons.append([InlineKeyboardButton(text="Назад", callback_data="back_to_user_editor")])

        keyboard = InlineKeyboardMarkup(inline_keyboard=key_buttons)

        await message.reply("\n".join(response_messages), reply_markup=keyboard, parse_mode="HTML")

    finally:
        await conn.close()

    await state.clear()

@router.callback_query(lambda c: c.data.startswith('change_expiry|'))
async def prompt_expiry_change(callback_query: CallbackQuery, state: FSMContext):
    email = callback_query.data.split('|')[1] 
    await callback_query.message.edit_text(
    f"Введите новое время истечения для ключа <b>{email}</b> в формате <code>YYYY-MM-DD HH:MM:SS</code>:",
    parse_mode="HTML"
)
    await state.update_data(email=email)
    await state.set_state(UserEditorState.waiting_for_expiry_time)  

@router.message(UserEditorState.waiting_for_expiry_time)
async def handle_expiry_time_input(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    email = user_data.get('email')  

    if not email:
        await message.reply("Email не найден в состоянии.")
        await state.clear()
        return

    try:
        expiry_time_str = message.text
        expiry_time = int(datetime.strptime(expiry_time_str, '%Y-%m-%d %H:%M:%S').timestamp() * 1000)

        client_id = await get_client_id_by_email(email)  
        if client_id is None:
            await message.reply(f"Клиент с email {email} не найден.")
            await state.clear()
            return

        await update_key_expiry(client_id, expiry_time)

        conn = await asyncpg.connect(DATABASE_URL)
        try:
            record = await conn.fetchrow('SELECT server_id FROM keys WHERE client_id = $1', client_id)
            if not record:
                await message.reply("Клиент не найден в базе данных.")
                await state.clear()
                return
            
            server_id = record['server_id']
            tg_id = await get_tg_id_by_client_id(client_id)

            session = await login_with_credentials(server_id, ADMIN_USERNAME, ADMIN_PASSWORD)

            print(f"Попытка обновить панель для server_id: {server_id}, tg_id: {tg_id}, client_id: {client_id}, email: {email}, expiryTime: {expiry_time}")

            success = await extend_client_key_admin(session, server_id, tg_id, client_id, email, expiry_time)

            print(f"Статус обновления панели: {'Успешно' if success else 'Не удалось'}")
            if success:
                response_message = (
                    f"Время истечения ключа для клиента {client_id} ({email}) успешно обновлено и синхронизировано с панелью."
                )
            else:
                response_message = (
                    f"Время истечения ключа для клиента {client_id} ({email}) обновлено, но не удалось синхронизировать с панелью."
                )

            back_button = InlineKeyboardButton(text="Назад", callback_data="back_to_user_editor")
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[back_button]])

            await message.reply(response_message, reply_markup=keyboard, parse_mode="HTML")
                
        finally:
            await conn.close()
    except ValueError:
        await message.reply("Пожалуйста, используйте формат: YYYY-MM-DD HH:MM:SS.")
    except Exception as e:
        await message.reply(f"Произошла ошибка: {e}")

    await state.clear()  

@router.callback_query(lambda c: c.data.startswith('delete_key_admin|'))
async def process_callback_delete_key(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    email = callback_query.data.split('|')[1]

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        client_id = await conn.fetchval('SELECT client_id FROM keys WHERE email = $1', email)

        if client_id is None:
            await bot.edit_message_text("Ключ не найден.", chat_id=tg_id, message_id=callback_query.message.message_id)
            return

        confirmation_keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text='✅ Да, удалить', callback_data=f'confirm_delete_admin|{client_id}')],
            [types.InlineKeyboardButton(text='❌ Нет, отменить', callback_data='view_keys')]
        ])

        await bot.edit_message_text("<b>Вы уверены, что хотите удалить ключ?</b>", chat_id=tg_id, message_id=callback_query.message.message_id, reply_markup=confirmation_keyboard, parse_mode="HTML")
    finally:
        await conn.close()

    await callback_query.answer()

@router.callback_query(lambda c: c.data.startswith('confirm_delete_admin|'))
async def process_callback_confirm_delete(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    client_id = callback_query.data.split('|')[1]

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            record = await conn.fetchrow('SELECT email, server_id FROM keys WHERE client_id = $1', client_id)

            if record:
                email = record['email']
                server_id = record['server_id']
                session = await login_with_credentials(server_id, ADMIN_USERNAME, ADMIN_PASSWORD)
                success = await delete_client(session, server_id, client_id)

                if success:
                    await conn.execute('DELETE FROM keys WHERE client_id = $1', client_id)
                    response_message = "Ключ был успешно удален."
                else:
                    response_message = "Ошибка при удалении клиента через API."

            else:
                response_message = "Ключ не найден или уже удален."

            back_button = types.InlineKeyboardButton(text='Назад', callback_data='view_keys')
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[back_button]])

            await bot.edit_message_text(response_message, chat_id=tg_id, message_id=callback_query.message.message_id, reply_markup=keyboard)

        finally:
            await conn.close()

    except Exception as e:
        await bot.edit_message_text(f"Ошибка при удалении ключа: {e}", chat_id=tg_id, message_id=callback_query.message.message_id)

    await callback_query.answer()

@router.callback_query(lambda c: c.data == "back_to_user_editor")
async def back_to_user_editor(callback_query: CallbackQuery):
    await back_to_admin_menu(callback_query)

async def handle_error(tg_id, callback_query, message):
    await bot.edit_message_text(message, chat_id=tg_id, message_id=callback_query.message.message_id, parse_mode="HTML")


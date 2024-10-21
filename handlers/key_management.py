import re
import uuid
from datetime import datetime, timedelta

import asyncpg
from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)

from auth import link, login_with_credentials
from bot import bot, dp
from client import add_client
from config import (ADMIN_ID, ADMIN_PASSWORD, ADMIN_USERNAME, DATABASE_URL,
                    SERVERS)
from database import add_connection, get_balance, store_key, update_balance
from handlers.backup_handler import backup_command
from handlers.instructions import send_instructions
from handlers.pay import ReplenishBalanceState, process_custom_amount_input
from handlers.profile import process_callback_view_profile
from handlers.start import start_command
from handlers.texts import KEY, KEY_TRIAL, NULL_BALANCE

router = Router()

def sanitize_key_name(key_name: str) -> str:
    return re.sub(r'[^a-z0-9@._-]', '', key_name.lower())

class Form(StatesGroup):
    waiting_for_server_selection = State()
    waiting_for_key_name = State()
    viewing_profile = State()
    waiting_for_message = State()

@dp.callback_query(F.data == 'create_key')
async def process_callback_create_key(callback_query: CallbackQuery, state: FSMContext):
    tg_id = callback_query.from_user.id

    server_buttons = []
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        for server_id, server in SERVERS.items():
            count = await conn.fetchval('SELECT COUNT(*) FROM keys WHERE server_id = $1', server_id)
            percent_full = (count / 60) * 100 if count <= 60 else 100  
            server_name = f"{server['name']} ({percent_full:.1f}%)"
            server_buttons.append([InlineKeyboardButton(text=server_name, callback_data=f'select_server|{server_id}')])
    finally:
        await conn.close()

    button_back = InlineKeyboardButton(text='⬅️ Назад', callback_data='view_profile')
    server_buttons.append([button_back]) 

    await callback_query.message.edit_text(
        "<b>⚙️ Выберите сервер для создания ключа:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=server_buttons)
    )
    
    await state.set_state(Form.waiting_for_server_selection)

    await callback_query.answer()


@dp.callback_query(F.data.startswith('select_server|'))
async def select_server(callback_query: CallbackQuery, state: FSMContext):
    server_id = callback_query.data.split('|')[1]
    await state.update_data(selected_server_id=server_id)

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        existing_connection = await conn.fetchrow('SELECT trial FROM connections WHERE tg_id = $1', callback_query.from_user.id)
    finally:
        await conn.close()

    trial_status = existing_connection['trial'] if existing_connection else 0

    if trial_status == 1:
        await callback_query.message.edit_text(
            KEY,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text='✅ Да, создать новый ключ', callback_data='confirm_create_new_key')],
                [InlineKeyboardButton(text='↩️ Назад', callback_data='cancel_create_key')]
            ])
        )
        await state.update_data(creating_new_key=True)
    else:
        await callback_query.message.edit_text(
            KEY_TRIAL,
            parse_mode="HTML"
        )
        await state.set_state(Form.waiting_for_key_name)

    await callback_query.answer()

@dp.callback_query(F.data == 'confirm_create_new_key')
async def confirm_create_new_key(callback_query: CallbackQuery, state: FSMContext):
    tg_id = callback_query.from_user.id
    data = await state.get_data()
    server_id = data.get('selected_server_id')

    balance = await get_balance(tg_id)
    if balance < 100:
        replenish_button = InlineKeyboardButton(text='Перейти в профиль', callback_data='view_profile')
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[replenish_button]])
        await callback_query.message.edit_text(
            NULL_BALANCE, 
            reply_markup=keyboard
        )
        await state.clear()
        return

    await callback_query.message.edit_text("🔑 Пожалуйста, введите имя нового ключа:")
    await state.set_state(Form.waiting_for_key_name)
    await state.update_data(creating_new_key=True)

    await callback_query.answer()

@dp.callback_query(F.data == 'cancel_create_key')
async def cancel_create_key(callback_query: CallbackQuery, state: FSMContext):
    await process_callback_view_profile(callback_query, state)
    await callback_query.answer()

@router.message(Command('start'))
async def handle_start(message: types.Message, state: FSMContext):
    await start_command(message)

@router.message(Command('menu'))
async def handle_menu(message: types.Message, state: FSMContext):
    await start_command(message)

@router.message(Command('send_trial'))
async def handle_send_trial_command(message: types.Message, state: FSMContext):
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            records = await conn.fetch('''
                SELECT tg_id FROM connections WHERE trial = 0
            ''')

            if records:
                for record in records:
                    tg_id = record['tg_id']
                    trial_message = (
                        "🎉 Вам предоставлен пробный период на 1 день!\n"
                        "Пожалуйста, воспользуйтесь им, чтобы протестировать наш сервис."
                    )
                    try:
                        await bot.send_message(chat_id=tg_id, text=trial_message)
                    except Exception as e:
                        if "Forbidden: bot was blocked by the user" in str(e):
                            print(f"Бот заблокирован пользователем с tg_id: {tg_id}")
                        else:
                            print(f"Ошибка при отправке сообщения пользователю {tg_id}: {e}")

                await message.answer("Сообщения о пробном периоде отправлены всем пользователям с не использованным ключом.")
            else:
                await message.answer("Нет пользователей с не использованными пробными ключами.")

        finally:
            await conn.close()

    except Exception as e:
        await message.answer(f"Ошибка при отправке сообщений: {e}")

@router.message(Command('send_to_all'))
async def send_message_to_all_clients(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.answer("У вас нет прав для выполнения этой команды.")
        return

    await message.answer("Введите текст сообщения, который вы хотите отправить всем клиентам:")
    await state.set_state(Form.waiting_for_message) 

@router.message(Form.waiting_for_message)
async def process_message_to_all(message: types.Message, state: FSMContext):
    text_message = message.text 

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        tg_ids = await conn.fetch('SELECT tg_id FROM connections')

        for record in tg_ids:
            tg_id = record['tg_id']
            try:
                await bot.send_message(chat_id=tg_id, text=text_message)
            except Exception as e:
                print(f"Ошибка при отправке сообщения пользователю {tg_id}: {e}. Пропускаем этого пользователя.")

        await message.answer("Сообщение было отправлено всем клиентам.")
    except Exception as e:
        print(f"Ошибка при подключении к базе данных: {e}")
        await message.answer("Произошла ошибка при отправке сообщения.")
    finally:
        await conn.close()

    await state.clear()

@router.message() 
async def handle_text(message: types.Message, state: FSMContext):
    current_state = await state.get_state()

    if message.text in ["/send_to_all"]:
        await send_message_to_all_clients(message, state) 
        return

    if message.text == "Мой профиль":
        callback_query = types.CallbackQuery(
            id="1",
            from_user=message.from_user,
            chat_instance='',
            data='view_profile',
            message=message
        )
        await process_callback_view_profile(callback_query, state)
        return

    if current_state == ReplenishBalanceState.entering_custom_amount.state:
        await process_custom_amount_input(message, state)
        return

    if current_state == Form.waiting_for_key_name.state:
        await handle_key_name_input(message, state)

    if message.text == "/backup":
        await backup_command(message)

    elif current_state is None:  
        await start_command(message)


async def handle_key_name_input(message: Message, state: FSMContext):
    tg_id = message.from_user.id
    key_name = sanitize_key_name(message.text)

    if not key_name:
        await message.bot.send_message(tg_id, "📝 Пожалуйста, назовите профиль на английском языке.")
        return

    data = await state.get_data()
    creating_new_key = data.get('creating_new_key', False)
    server_id = data.get('selected_server_id')

    session = await login_with_credentials(server_id, ADMIN_USERNAME, ADMIN_PASSWORD)
    client_id = str(uuid.uuid4())
    email = key_name.lower()
    current_time = datetime.utcnow()
    expiry_time = None

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        existing_connection = await conn.fetchrow('SELECT trial FROM connections WHERE tg_id = $1', tg_id)
    finally:
        await conn.close()

    trial_status = existing_connection['trial'] if existing_connection else 0

    if trial_status == 0:
        expiry_time = current_time + timedelta(days=1, hours=3)
    else:
        balance = await get_balance(tg_id)
        if balance < 100:
            replenish_button = InlineKeyboardButton(text='Перейти в профиль', callback_data='view_profile')
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[replenish_button]])
            await message.bot.send_message(tg_id, "❗️ Недостаточно средств на балансе для создания нового ключа.", reply_markup=keyboard)
            await state.clear()
            return

        await update_balance(tg_id, -100)
        expiry_time = current_time + timedelta(days=30, hours=3)

    expiry_timestamp = int(expiry_time.timestamp() * 1000)

    try:
        response = await add_client(session, server_id, client_id, email, tg_id, limit_ip=1, total_gb=0, expiry_time=expiry_timestamp, enable=True, flow="xtls-rprx-vision")
        
        if not response.get("success", True):
            error_msg = response.get("msg", "Неизвестная ошибка.")
            if "Duplicate email" in error_msg:
                await message.bot.send_message(tg_id, "❌ Этот email уже используется. Пожалуйста, выберите другое имя для ключа.")
                await state.set_state(Form.waiting_for_key_name)
                return
            else:
                raise Exception(error_msg)

        connection_link = await link(session, server_id, client_id, email)

        conn = await asyncpg.connect(DATABASE_URL)
        try:
            existing_connection = await conn.fetchrow('SELECT * FROM connections WHERE tg_id = $1', tg_id)

            if existing_connection:
                await conn.execute('UPDATE connections SET trial = 1 WHERE tg_id = $1', tg_id)
            else:
                await add_connection(tg_id, 0, 1)
        finally:
            await conn.close()

        await store_key(tg_id, client_id, email, expiry_timestamp, connection_link, server_id)

        remaining_time = expiry_time - current_time
        days = remaining_time.days
        hours, remainder = divmod(remaining_time.seconds, 3600)
        minutes, _ = divmod(remainder, 60)

        remaining_time_message = (
            f"Оставшееся время ключа: {days} день"
        )

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='📘 Инструкции по использованию', callback_data='instructions')],
            [InlineKeyboardButton(text='🔙 Перейти в профиль', callback_data='view_profile')]
        ])

        key_message = (
            "✅ Ключ успешно создан:\n"
            f"<pre>{connection_link}</pre>\n\n"
            f"{remaining_time_message}\n\n"
            "<i>Добавьте ключ в приложение по инструкции ниже:</i>"
        )

        await message.bot.send_message(tg_id, key_message, parse_mode="HTML", reply_markup=keyboard)

    except Exception as e:
        await message.bot.send_message(tg_id, f"❌ Ошибка при создании ключа: {e}")

    await state.clear()

@dp.callback_query(F.data == 'instructions')
async def handle_instructions(callback_query: CallbackQuery):
    await send_instructions(callback_query) 

@dp.callback_query(F.data == 'back_to_main')
async def handle_back_to_main(callback_query: CallbackQuery, state: FSMContext):
    await process_callback_view_profile(callback_query, state)
    await callback_query.answer()

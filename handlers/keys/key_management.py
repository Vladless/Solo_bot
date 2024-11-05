import uuid
from datetime import datetime, timedelta

from bot import dp, bot
import asyncpg
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)

from auth import login_with_credentials, link_subscription
from client import add_client
from config import (ADMIN_PASSWORD, ADMIN_USERNAME, DATABASE_URL,
                    SERVERS, APP_URL)
from database import add_connection, get_balance, store_key, update_balance
from handlers.instructions.instructions import send_instructions
from handlers.profile import process_callback_view_profile
from handlers.texts import KEY, KEY_TRIAL, NULL_BALANCE, key_message_success
from handlers.utils import sanitize_key_name

router = Router()

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

    await callback_query.message.delete()
    await bot.send_message(
        chat_id=tg_id,
        text="<b>⚙️ Выберите сервер для создания ключа:</b>",
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

async def handle_key_name_input(message: Message, state: FSMContext):
    tg_id = message.from_user.id
    key_name = sanitize_key_name(message.text)

    if not key_name:
        await message.bot.send_message(tg_id, "📝 Пожалуйста, назовите ключ устройства на английском языке.")
        return

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        existing_key = await conn.fetchrow('SELECT * FROM keys WHERE email = $1', key_name.lower())
        if existing_key:
            await message.bot.send_message(tg_id, "❌ Это имя уже используется. Пожалуйста, выберите другое имя для ключа.")
            await state.set_state(Form.waiting_for_key_name)
            return
    finally:
        await conn.close()

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
                await message.bot.send_message(tg_id, "❌ Это имя уже используется. Пожалуйста, выберите другое имя для ключа.")
                await state.set_state(Form.waiting_for_key_name)
                return
            else:
                raise Exception(error_msg)

        connection_link = await link_subscription(email, server_id)

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

        remaining_time_message = f"Оставшееся время ключа: {days} день"

        button_profile = InlineKeyboardButton(text='👤 Мой профиль', callback_data='view_profile')
        button_iphone = InlineKeyboardButton(
            text='🍏IPhone', 
            url=f'{APP_URL}/?url=v2raytun://import/{connection_link}'
        )
        button_android = InlineKeyboardButton(
            text='🤖Android', 
            url=f'{APP_URL}/?url=v2raytun://import-sub?url={connection_link}'
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [button_iphone, button_android],
            [button_profile]
        ])

        key_message = key_message_success(connection_link, remaining_time_message)

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

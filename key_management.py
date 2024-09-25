import asyncio
import re
import uuid
from datetime import datetime, timedelta

import asyncpg
from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)
from pytz import timezone

from auth import link, login_with_credentials
from bot import bot, dp
from client import add_client
from config import (ADMIN_CHAT_ID, ADMIN_PASSWORD, ADMIN_USERNAME, API_TOKEN,
                    DATABASE_URL)
from database import (add_connection, get_balance, has_active_key, store_key,
                      update_balance)
from handlers.profile import process_callback_view_profile
from handlers.start import start_command

router = Router()

# Удаляем специальные символы из имени ключа
def sanitize_key_name(key_name: str) -> str:
    return re.sub(r'[^a-z0-9@._-]', '', key_name.lower())

class Form(StatesGroup):
    waiting_for_key_name = State()
    viewing_profile = State()

@dp.callback_query(F.data == 'create_key')
async def process_callback_create_key(callback_query: CallbackQuery, state: FSMContext):
    tg_id = callback_query.from_user.id

    # Получаем данные о trial из базы данных
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        existing_connection = await conn.fetchrow('SELECT trial FROM connections WHERE tg_id = $1', tg_id)
    finally:
        await conn.close()

    trial_status = existing_connection['trial'] if existing_connection else 0

    if trial_status == 1:
        await callback_query.message.edit_text(
            "У вас уже был пробный ключ. Новый стоит 100 рублей и сразу на месяц. \n\n"
            "Хотите продолжить?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text='Да, создать новый ключ', callback_data='confirm_create_new_key')],
                [InlineKeyboardButton(text='Назад', callback_data='cancel_create_key')]
            ])
        )
        await state.update_data(creating_new_key=True)
    else:
        await callback_query.message.edit_text("Вам будет выдан пробный ключ. Пожалуйста, выберите имя для вашего ключа:")
        await state.set_state(Form.waiting_for_key_name)

    await callback_query.answer()

@dp.callback_query(F.data == 'confirm_create_new_key')
async def confirm_create_new_key(callback_query: CallbackQuery, state: FSMContext):
    tg_id = callback_query.from_user.id

    # Проверяем баланс перед созданием нового ключа
    balance = await get_balance(tg_id)
    if balance < 100:
        replenish_button = InlineKeyboardButton(text='Перейти в профиль', callback_data='view_profile')
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[replenish_button]])
        await callback_query.message.edit_text(
            "❗️ Недостаточно средств на балансе для создания нового ключа. "
            "Пожалуйста, пополните баланс.", 
            reply_markup=keyboard
        )
        await state.clear()
        return

    await callback_query.message.edit_text("🔑 Пожалуйста, выберите имя для вашего нового ключа:")
    await state.set_state(Form.waiting_for_key_name)
    await state.update_data(creating_new_key=True)

    await callback_query.answer()

@dp.callback_query(F.data == 'cancel_create_key')
async def cancel_create_key(callback_query: CallbackQuery, state: FSMContext):
    await process_callback_view_profile(callback_query, state)
    await callback_query.answer()

# Обработка текстовых сообщений
@dp.message()
async def handle_text(message: Message, state: FSMContext):
    current_state = await state.get_state()
    
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

    if message.text in ["/start", "/menu"]:
        await start_command(message)
        return
    
    if current_state == Form.waiting_for_key_name.state:
        await handle_key_name_input(message, state)

async def handle_key_name_input(message: Message, state: FSMContext):
    tg_id = message.from_user.id

    key_name = sanitize_key_name(message.text)

    if not key_name:
        await message.bot.send_message(tg_id, "📝 Пожалуйста, назовите профиль на английском языке.")
        return

    data = await state.get_data()
    creating_new_key = data.get('creating_new_key', False)

    session = login_with_credentials(ADMIN_USERNAME, ADMIN_PASSWORD)
    client_id = str(uuid.uuid4())
    email = key_name.lower()
    current_time = datetime.utcnow()
    expiry_time = None

    # Получаем статус пробного ключа из базы данных
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        existing_connection = await conn.fetchrow('SELECT trial FROM connections WHERE tg_id = $1', tg_id)
    finally:
        await conn.close()

    trial_status = existing_connection['trial'] if existing_connection else 0

    if trial_status == 0:
        # Создаем пробный ключ на 1 день
        expiry_time = int((current_time + timedelta(days=1, hours=3)).timestamp() * 1000)
    else:
        # Проверяем баланс перед созданием нового ключа
        balance = await get_balance(tg_id)
        if balance < 100:
            replenish_button = InlineKeyboardButton(text='Перейти в профиль', callback_data='view_profile')
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[replenish_button]])
            await message.bot.send_message(tg_id, "❗️ Недостаточно средств на балансе для создания нового ключа.", reply_markup=keyboard)
            await state.clear()
            return

        await update_balance(tg_id, -100)
        expiry_time = int((current_time + timedelta(days=30, hours=3)).timestamp() * 1000)

    try:
        # Попробуем добавить клиента
        response = add_client(session, client_id, email, tg_id, limit_ip=1, total_gb=0, expiry_time=expiry_time, enable=True, flow="xtls-rprx-vision")
        
        # Проверяем статус ответа от сервера
        if not response.get("success", True):
            error_msg = response.get("msg", "Неизвестная ошибка.")
            if "Duplicate email" in error_msg:
                await message.bot.send_message(tg_id, "❌ Этот email уже используется. Пожалуйста, выберите другое имя для ключа.")
                await state.set_state(Form.waiting_for_key_name)  # Возвращаем пользователя к вводу имени ключа
                return
            else:
                raise Exception(error_msg)

        # Если добавление клиента прошло успешно, получаем ссылку
        connection_link = link(session, client_id, email)

        # Проверка существующей записи
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            existing_connection = await conn.fetchrow('SELECT * FROM connections WHERE tg_id = $1', tg_id)

            if existing_connection:
                await conn.execute('UPDATE connections SET trial = 1 WHERE tg_id = $1', tg_id)
            else:
                await add_connection(tg_id, 0, 1)

        finally:
            await conn.close()

        await store_key(tg_id, client_id, email, expiry_time, connection_link)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='📖 Инструкции по использованию', callback_data='instructions')],
            [InlineKeyboardButton(text='🔙 Перейти в профиль', callback_data='view_profile')]
        ])

        key_message = (
            "✅ Ключ успешно создан:\n"
            f"<pre>{connection_link}</pre>"
        )
        await message.bot.send_message(tg_id, key_message, parse_mode="HTML", reply_markup=keyboard)

    except Exception as e:
        await message.bot.send_message(tg_id, f"❌ Ошибка при создании ключа: {e}")

    await state.clear()




@dp.callback_query(F.data == 'instructions')
async def handle_instructions(callback_query: CallbackQuery):
    instructions_message = (
        "*📋 Инструкции по использованию вашего ключа:*\n\n"
        "1. Скачайте приложение для вашего устройства:\n"
        "   - Для Android: [V2Ray](https://play.google.com/store/apps/details?id=com.v2ray.ang&hl=ru&pli=1)\n"
        "   - Для iPhone: [Streisand](https://apps.apple.com/ru/app/streisand/id6450534064)\n\n"
        "2. Скопируйте предоставленный ключ, который вы получили ранее.\n"
        "3. Откройте приложение и нажмите на плюсик сверху справа.\n"
        "4. Выберите 'Вставить из буфера обмена' для добавления ключа.\n\n"
        "💬 Если у вас возникнут вопросы, не стесняйтесь обращаться в поддержку."
    )

    back_button = InlineKeyboardButton(text='🔙 Назад', callback_data='back_to_main')
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[back_button]])

    await callback_query.message.edit_text(instructions_message, parse_mode='Markdown', reply_markup=keyboard)
    await callback_query.answer()

@dp.callback_query(F.data == 'back_to_main')
async def handle_back_to_main(callback_query: CallbackQuery, state: FSMContext):
    await process_callback_view_profile(callback_query, state)
    await callback_query.answer()

async def renew_expired_keys():
    while True:
        current_time = datetime.utcnow()
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            active_keys = await conn.fetch('SELECT tg_id FROM connections WHERE trial > 0')

        finally:
            await conn.close()

        for record in active_keys:
            tg_id = record['tg_id']
            balance = await get_balance(tg_id)
            if balance >= 100:
                new_expiry_time = int((current_time + timedelta(days=30)).timestamp() * 1000)

                conn = await asyncpg.connect(DATABASE_URL)
                try:
                    await conn.execute('UPDATE keys SET expiry_time = $1 WHERE tg_id = $2', new_expiry_time, tg_id)
                    await update_balance(tg_id, -100)
                finally:
                    await conn.close()

                print(f"Ключ для пользователя {tg_id} продлен на месяц и списано 100 рублей.")
            else:
                print(f"Недостаточно средств на балансе для пользователя {tg_id}. Предложение пополнить баланс.")
                replenish_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text='Пополнить баланс', callback_data='replenish_balance')]
                ])
                await bot.send_message(tg_id, "Ваш баланс недостаточен для продления ключа. Пожалуйста, пополните баланс.", reply_markup=replenish_keyboard)

        await asyncio.sleep(3600)
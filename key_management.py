from datetime import datetime, timedelta
import asyncio
import re
import uuid
import aiosqlite
from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from auth import login_with_credentials, link
from client import add_client, extend_client_key
from config import API_TOKEN, ADMIN_PASSWORD, ADMIN_USERNAME, ADMIN_CHAT_ID, DATABASE_PATH
from database import add_connection, has_active_key, get_balance, store_key, update_balance
from bot import dp
from handlers.start import start_command
from handlers.profile import process_callback_view_profile
from handlers.keys import process_callback_view_keys
from bot import bot

router = Router()

# Удаляем специальные символы из имени ключа
def sanitize_key_name(key_name: str) -> str:
    return re.sub(r'[^a-z0-9@._-]', '', key_name.lower())

class Form(StatesGroup):
    waiting_for_key_name = State()
    waiting_for_expiry_date = State()
    viewing_profile = State()

# Обработка нажатия кнопки создания ключа
@dp.callback_query(F.data == 'create_key')
async def process_callback_create_key(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.message.edit_text("Вам будет выдан пробный ключ. Пожалуйста, выберите имя для вашего ключа:")
    await state.set_state(Form.waiting_for_key_name)
    await callback_query.answer()

# Обработка текстовых сообщений
@dp.message()
async def handle_text(message: Message, state: FSMContext):
    current_state = await state.get_state()
    print(f"Received message: {message.text}, Current state: {current_state}")

    # Обработка команд и переходов
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

    if message.text in ["/start", "Меню"]:
        await start_command(message)
        return
    
    # Если ожидается имя ключа
    if current_state == Form.waiting_for_key_name.state:
        await handle_key_name_input(message, state)

# Обработка ввода имени ключа
async def handle_key_name_input(message: Message, state: FSMContext):
    tg_id = message.from_user.id
    key_name = sanitize_key_name(message.text)

    if not key_name:
        await message.reply("Имя профиля не указано. Введите имя на английском языке.")
        await state.clear()
        return

    if await has_active_key(tg_id):
        await message.reply("У вас уже есть активный ключ. Вы не можете создать новый.")
        await state.clear()
        return

    await state.update_data(key_name=key_name, tg_id=tg_id)

    session = login_with_credentials(ADMIN_USERNAME, ADMIN_PASSWORD)
    client_id = str(uuid.uuid4())
    email = key_name.lower()
    current_time = datetime.utcnow()
    expiry_time = int((current_time + timedelta(days=1)).timestamp() * 1000)

    try:
        # Создание клиента и получение ссылки
        add_client(session, client_id, email, tg_id, limit_ip=1, total_gb=0, expiry_time=expiry_time, enable=True, flow="xtls-rprx-vision")
        connection_link = link(session, client_id, email)

        await add_connection(tg_id, client_id, email, expiry_time, 0)
        await store_key(client_id, email, connection_link)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='Инструкции по использованию', callback_data='instructions')],
            [InlineKeyboardButton(text='Перейти в профиль', callback_data='view_profile')]
        ])

        key_message = f"Ключ создан:\n<pre>{connection_link}</pre>"
        await message.reply(key_message, parse_mode="HTML", reply_markup=keyboard)
    except Exception as e:
        await message.reply(f"Ошибка при создании ключа: {e}")
        print(f"Ошибка при создании ключа: {e}")

    await state.clear()

# Обработка кнопки "Инструкции"
@dp.callback_query(F.data == 'instructions')
async def handle_instructions(callback_query: CallbackQuery):
    instructions_message = (
        "*Инструкции по использованию вашего ключа:*\n\n"
        "1. Скачайте приложение для вашего устройства:\n"
        "   - Для Android: [V2Ray](https://play.google.com/store/apps/details?id=com.v2ray.ang&hl=ru&pli=1)\n"
        "   - Для iPhone: [Streisand](https://apps.apple.com/ru/app/streisand/id6450534064)\n\n"
        "2. Скопируйте предоставленный ключ, который вы получили ранее.\n"
        "3. Откройте приложение и нажмите на плюсик сверху справа.\n"
        "4. Выберите 'Вставить из буфера обмена' для добавления ключа.\n\n"
        "Если у вас возникнут вопросы, не стесняйтесь обращаться в поддержку."
    )

    back_button = InlineKeyboardButton(text='Назад', callback_data='back_to_main')
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[back_button]])

    await callback_query.message.edit_text(instructions_message, parse_mode='Markdown', reply_markup=keyboard)
    await callback_query.answer()

# Обработка кнопки "Назад"
@dp.callback_query(F.data == 'back_to_main')
async def handle_back_to_main(callback_query: CallbackQuery):
    tg_id = callback_query.from_user.id
    new_callback_query = CallbackQuery(
        id=callback_query.id,
        from_user=callback_query.from_user,
        chat_instance=callback_query.chat_instance,
        data='view_keys',
        message=callback_query.message
    )

    await process_callback_view_keys(new_callback_query)
    await callback_query.answer()

# Фоновая задача для продления ключей
async def renew_expired_keys():
    while True:
        current_time = datetime.utcnow()
        async with aiosqlite.connect(DATABASE_PATH) as db:
            async with db.execute('SELECT tg_id, client_id FROM connections WHERE expiry_time <= ?', (int(current_time.timestamp() * 1000),)) as cursor:
                expired_keys = await cursor.fetchall()

        for tg_id, client_id in expired_keys:
            balance = await get_balance(tg_id)
            if balance >= 100:
                new_expiry_time = int((current_time + timedelta(days=30)).timestamp() * 1000)

                async with aiosqlite.connect(DATABASE_PATH) as db:
                    await db.execute('UPDATE connections SET expiry_time = ? WHERE tg_id = ? AND client_id = ?', (new_expiry_time, tg_id, client_id))
                    await db.commit()

                await update_balance(tg_id, -100)
                await extend_client_key(client_id)

                print(f"Ключ для клиента {client_id} продлен на месяц и списано 100 рублей.")
            else:
                print(f"Недостаточно средств на балансе для клиента {client_id}. Предложение пополнить баланс.")
                replenish_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text='Пополнить баланс', callback_data='replenish_balance')]
                ])
                await bot.send_message(tg_id, "Ваш баланс недостаточен для продления ключа. Пожалуйста, пополните баланс.", reply_markup=replenish_keyboard)

        await asyncio.sleep(3600)

from aiogram import Bot, Dispatcher, Router, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, Message
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from auth import login_with_credentials, link
from client import add_client
from datetime import datetime, timedelta
from config import API_TOKEN, ADMIN_PASSWORD, ADMIN_USERNAME, ADMIN_CHAT_ID, DATABASE_PATH
from database import add_connection, has_active_key, get_active_key_email, get_balance, store_key, update_balance
from client import extend_client_key
import uuid
import aiosqlite
import re
from bot import dp
from handlers.start import start_command
from bot import bot
from handlers.profile import process_callback_view_profile
import asyncio


router = Router()

def escape_markdown(text: str) -> str:
    """
    Экранирование специальных символов для Markdown.
    """
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(r'([%s])' % re.escape(escape_chars), r'\\\1', text)

def sanitize_key_name(key_name: str) -> str:
    """
    Очищает имя ключа от недопустимых символов и приводит его к нижнему регистру.
    """
    # Оставляем только строчные буквы, цифры, символы @._-
    return re.sub(r'[^a-z0-9@._-]', '', key_name.lower())

class Form(StatesGroup):
    waiting_for_key_name = State()
    waiting_for_admin_confirmation = State()
    waiting_for_statistics = State()
    waiting_for_expiry_date = State()
    viewing_profile = State()

@dp.callback_query(F.data == 'create_key')
async def process_callback_create_key(callback_query: types.CallbackQuery, state: FSMContext):
    await bot.send_message(callback_query.from_user.id, "Введите имя вашего профиля VPN:")
    await state.set_state(Form.waiting_for_key_name)
    await callback_query.answer()

@dp.message()
async def handle_text(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    print(f"Received message: {message.text}, Current state: {current_state}")

    if message.text == "Мой профиль":
        # Создайте фейковый объект CallbackQuery для вызова функции
        callback_query = types.CallbackQuery(
            id="1",  # Используйте уникальный идентификатор
            from_user=message.from_user,
            chat_instance='',
            data='view_profile',
            message=message
        )
        await process_callback_view_profile(callback_query, state)
        return

    if message.text == "/start":
        await start_command(message)
        return

    if message.text == "В начало":
        await start_command(message)
        return
    
    if current_state == Form.waiting_for_key_name.state:
        tg_id = message.from_user.id
        key_name = sanitize_key_name(message.text)
        tg_name = message.from_user.username

        if not key_name:
            await bot.send_message(tg_id, "Имя клиента не указано.")
            await state.clear()
            return

        if await has_active_key(tg_id):
            await bot.send_message(tg_id, "У вас уже есть активный ключ. Вы не можете создать новый.")
            await state.clear()
            return

        # Сохраняем информацию о запросе в контексте состояния
        await state.update_data(key_name=key_name, tg_id=tg_id)

        # Отправляем запрос на подтверждение админу
        admin_message = (
            f"Новый запрос на создание ключа:\n"
            f"Телеграм-ID: {tg_id}\n"
            f"Имя ключа: {key_name}\n"
            f"Имя пользователя Telegram: @{tg_name}\n"
            f"Введите 'yes' для подтверждения или 'no' для отклонения."
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='Да', callback_data=f'confirm_key_{tg_id}_{key_name}')],
            [InlineKeyboardButton(text='Нет', callback_data=f'reject_key_{tg_id}')]
        ])
        await bot.send_message(ADMIN_CHAT_ID, admin_message, reply_markup=keyboard)

        await bot.send_message(tg_id, "Ожидайте подтверждения администратора.")
        await state.clear()

@dp.callback_query(F.data.startswith('confirm_key_'))
async def handle_admin_confirmation(callback_query: CallbackQuery, state: FSMContext):
    if callback_query.message.chat.id != int(ADMIN_CHAT_ID):
        return
    
    session = login_with_credentials(ADMIN_USERNAME, ADMIN_PASSWORD)
    data = callback_query.data.split('_', 3)
    tg_id = int(data[2])
    key_name = data[3]

    try:
        client_id = str(uuid.uuid4())
        email = key_name.lower()  # Приведение email к нижнему регистру
        limit_ip = 1
        total_gb = 0
        current_time = datetime.utcnow()
        
        # Определяем время истечения ключа
        if await has_active_key(tg_id):
            # Если есть активный ключ, устанавливаем срок действия на 30 дней
            expiry_time = int((current_time + timedelta(days=30)).timestamp() * 1000)
        else:
            # Первый ключ устанавливаем на 1 день
            expiry_time = int((current_time + timedelta(days=1)).timestamp() * 1000)
        
        enable = True
        flow = "xtls-rprx-vision"
        balance = 0

        # Проверка данных перед созданием ключа
        print(f"Creating key with client_id={client_id}, email={email}, tg_id={tg_id}")

        add_client(session, client_id, email, tg_id, limit_ip, total_gb, expiry_time, enable, flow)

        connection_link = link(session, client_id, email)

        await add_connection(tg_id, client_id, email, expiry_time, balance)
        await store_key(client_id, email, connection_link) 

        # Создаем кнопки
        instructions_button = InlineKeyboardButton(text='Инструкции по использованию', callback_data='instructions')
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[instructions_button]])

        # Отправляем сообщение с ключом и кнопкой
        key_message = f"Ключ создан:\n<pre>{connection_link}</pre>"
        await bot.send_message(tg_id, key_message, parse_mode="HTML", reply_markup=keyboard)
        await bot.send_message(callback_query.message.chat.id, "Ключ создан и отправлен клиенту.")
    except Exception as e:
        await bot.send_message(callback_query.message.chat.id, f"Ошибка при создании ключа: {e}")
        print(f"Ошибка при создании ключа: {e}")  # Отладочное сообщение в консоль

    await callback_query.answer()


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
    await callback_query.message.answer(
        instructions_message,
        parse_mode='Markdown'
    )
    await callback_query.answer()

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
                print(f"Недостаточно средств на балансе для клиента {client_id}.")

        await asyncio.sleep(3600)
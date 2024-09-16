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
from database import add_connection, has_active_key, get_active_key_email, get_balance, store_key
import uuid
import aiosqlite
import re

class Form(StatesGroup):
    waiting_for_key_name = State()
    waiting_for_admin_confirmation = State()
    waiting_for_statistics = State()
    waiting_for_expiry_date = State()
    viewing_profile = State() 

bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot=bot, storage=storage)
router = Router()

# Создаем сессию при старте бота
session = login_with_credentials(ADMIN_USERNAME, ADMIN_PASSWORD)

@dp.message(Command("start"))
async def start_command(message: Message):
    welcome_text = "Добро пожаловать! Вы можете создать ключ для подключения VPN, просмотреть статистику использования, узнать дату окончания ключа или просмотреть ваш профиль."

    button_create_key = InlineKeyboardButton(text='Создать ключ', callback_data='create_key')
    button_view_stats = InlineKeyboardButton(text='Посмотреть статистику', callback_data='view_stats')
    button_view_expiry = InlineKeyboardButton(text='Дата окончания ключа', callback_data='view_expiry')
    button_view_profile = InlineKeyboardButton(text='Мой профиль', callback_data='view_profile')
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [button_create_key],
        [button_view_stats],
        [button_view_expiry],
        [button_view_profile]
    ])
    
    await message.reply(welcome_text, reply_markup=keyboard)

@dp.callback_query(F.data == 'view_profile')
async def process_callback_view_profile(callback_query: types.CallbackQuery, state: FSMContext):
    tg_id = callback_query.from_user.id
    username = callback_query.from_user.username  # Получаем никнейм пользователя
    
    try:
        email = await get_active_key_email(tg_id)
        if email:
            async with aiosqlite.connect(DATABASE_PATH) as db:
                async with db.execute("SELECT expiry_time FROM connections WHERE tg_id = ? AND expiry_time > ?", 
                                      (tg_id, int(datetime.utcnow().timestamp() * 1000))) as cursor:
                    record = await cursor.fetchone()
                    if record:
                        expiry_time = record[0]
                        expiry_date = datetime.utcfromtimestamp(expiry_time / 1000).strftime("%Y-%m-%d %H:%M:%S")
                    else:
                        expiry_date = "Неизвестно"
            
            # Получите баланс клиента (замените пример на вашу реальную логику получения баланса)
            balance = await get_balance(tg_id)  # Предполагается, что у вас есть функция для получения баланса
            
            profile_message = (
                f"Профиль клиента:\n"
                f"Никнейм: @{username}\n"  # Добавляем никнейм
                f"Email: {email}\n"
                f"Дата окончания ключа: {expiry_date}\n"
                f"Баланс: {balance}\n"
            )
            
            # Кнопка для просмотра ключей
            button_view_keys = InlineKeyboardButton(text='Мои ключи', callback_data='view_keys')
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [button_view_keys]
            ])
            
            profile_message += "Вы можете просмотреть ваши ключи ниже:"
        else:
            profile_message = "У вас нет активных ключей."
            keyboard = None
    
    except Exception as e:
        profile_message = f"Ошибка при получении данных профиля: {e}"
        keyboard = None
    
    await callback_query.message.reply(profile_message, reply_markup=keyboard)
    await callback_query.answer()

@dp.callback_query(F.data == 'view_keys')
async def process_callback_view_keys(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            async with db.execute("SELECT email, expiry_time FROM connections WHERE tg_id = ?", (tg_id,)) as cursor:
                records = await cursor.fetchall()
                if records:
                    keys_info = "\n".join([f"Email: {email}, Дата окончания: {datetime.utcfromtimestamp(expiry_time / 1000).strftime('%Y-%m-%d %H:%M:%S')}" for email, expiry_time in records])
                else:
                    keys_info = "У вас нет ключей."
    
    except Exception as e:
        keys_info = f"Ошибка при получении ключей: {e}"
    
    await callback_query.message.reply(f"Ваши ключи:\n{keys_info}")
    await callback_query.answer()


@dp.callback_query(F.data == 'create_key')
async def process_callback_create_key(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.message.reply("Введите имя вашего профиля VPN:")
    await state.set_state(Form.waiting_for_key_name)
    await callback_query.answer()

@dp.message()
async def handle_text(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    
    if current_state == Form.waiting_for_key_name.state:
        tg_id = message.from_user.id
        key_name = message.text
        tg_name = message.from_user.username

        if not key_name:
            await message.reply("Имя клиента не указано.")
            await state.clear()
            return

        if await has_active_key(tg_id):
            await message.reply("У вас уже есть активный ключ. Вы не можете создать новый.")
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

        await state.set_state(Form.waiting_for_admin_confirmation)
        await message.reply("Ожидайте подтверждения администратора.")
        await state.clear()

@dp.callback_query(F.data.startswith('confirm_key_'))
async def handle_admin_confirmation(callback_query: CallbackQuery, state: FSMContext):
    if callback_query.message.chat.id != int(ADMIN_CHAT_ID):
        return

    data = callback_query.data.split('_', 3)
    tg_id = int(data[2])
    key_name = data[3]

    try:
        client_id = str(uuid.uuid4())
        email = key_name
        limit_ip = 1
        total_gb = 0
        current_time = datetime.utcnow()
        expiry_time = int((current_time + timedelta(days=30)).timestamp() * 1000)
        enable = True
        flow = "xtls-rprx-vision"
        balance = 0

        add_client(session, client_id, email, tg_id, limit_ip, total_gb, expiry_time, enable, flow)

        connection_link = link(session, client_id, email)

        await add_connection(tg_id, client_id, email, expiry_time, balance)
        await store_key(client_id, email, connection_link) 
        await bot.send_message(tg_id, f"Ключ создан:\n<pre>{connection_link}</pre>", parse_mode="HTML")
        await callback_query.message.reply("Ключ создан и отправлен клиенту.")
    except Exception as e:
        await callback_query.message.reply(f"Ошибка при создании ключа: {e}")

    await callback_query.answer()

@dp.callback_query(F.data.startswith('reject_key_'))
async def handle_rejection(callback_query: CallbackQuery, state: FSMContext):
    if callback_query.message.chat.id != int(ADMIN_CHAT_ID):
        return

    tg_id = int(callback_query.data.split('_', 3)[2])
    await bot.send_message(tg_id, "Создание ключа отклонено.")
    await callback_query.message.reply("Запрос отклонен.")
    await callback_query.answer()

@dp.callback_query(F.data == 'view_stats')
async def process_callback_view_stats(callback_query: types.CallbackQuery, state: FSMContext):
    tg_id = callback_query.from_user.id
    
    try:
        email = await get_active_key_email(tg_id)
        if email:
            # Получаем client_id из базы данных
            async with aiosqlite.connect(DATABASE_PATH) as db:
                async with db.execute("SELECT client_id FROM connections WHERE tg_id = ? AND expiry_time > ?", 
                                      (tg_id, int(datetime.utcnow().timestamp() * 1000))) as cursor:
                    record = await cursor.fetchone()
                    if record:
                        client_id = record[0]
                        # Передаем оба параметра в функцию link
                        connection_link = link(session, client_id, email)
                        
                        # Извлечение данных о загрузке и выгрузке из ссылки
                        up_match = re.search(r'up=(\d+)', connection_link)
                        down_match = re.search(r'down=(\d+)', connection_link)
                        
                        up = up_match.group(1) if up_match else "Неизвестно"
                        down = down_match.group(1) if down_match else "Неизвестно"
                        
                        statistics = f"Статистика вашего ключа:\nЗагрузка: {up} MB\nВыгрузка: {down} MB"
                    else:
                        statistics = "У вас нет активных ключей."
        else:
            statistics = "У вас нет активных ключей."
    
    except Exception as e:
        statistics = f"Ошибка при получении статистики: {e}"
    
    await callback_query.message.reply(f"Ваша статистика:\n{statistics}")
    await callback_query.answer()


@dp.callback_query(F.data == 'view_expiry')
async def process_callback_view_expiry(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    
    try:
        email = await get_active_key_email(tg_id)
        if email:
            async with aiosqlite.connect(DATABASE_PATH) as db:
                async with db.execute("SELECT expiry_time FROM connections WHERE tg_id = ? AND expiry_time > ?", 
                                      (tg_id, int(datetime.utcnow().timestamp() * 1000))) as cursor:
                    record = await cursor.fetchone()
                    if record:
                        expiry_time = record[0]
                        expiry_date = datetime.utcfromtimestamp(expiry_time / 1000).strftime("%Y-%m-%d %H:%M:%S")
                        message_text = f"Дата окончания вашего ключа: {expiry_date}"
                    else:
                        message_text = "У вас нет активных ключей."
        else:
            message_text = "У вас нет активных ключей."
    
    except Exception as e:
        message_text = f"Ошибка при получении даты окончания ключа: {e}"
    
    await callback_query.message.reply(message_text)
    await callback_query.answer()

@dp.callback_query(F.data == 'view_balance')
async def process_callback_view_balance(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            async with db.execute("SELECT balance FROM connections WHERE tg_id = ? AND expiry_time > ?", 
                                  (tg_id, int(datetime.utcnow().timestamp() * 1000))) as cursor:
                record = await cursor.fetchone()
                balance = record[0]
                message_text = f"Ваш баланс: {balance} RUB"
    
    except Exception as e:
        message_text = f"Ошибка при получении баланса: {e}"
    
    await callback_query.message.reply(message_text)
    await callback_query.answer()

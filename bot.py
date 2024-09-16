from aiogram import Bot, Dispatcher, Router, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from auth import login_with_credentials, link, get_clients
from client import add_client, generate_client_id
from datetime import datetime, timedelta
from config import API_TOKEN, ADMIN_PASSWORD, ADMIN_USERNAME
from database import add_connection, DATABASE_PATH
import uuid
import aiosqlite
import requests
import re

class Form(StatesGroup):
    waiting_for_key_name = State()
    waiting_for_statistics = State()

bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot=bot, storage=storage)
router = Router()

# Создаем сессию при старте бота
session = login_with_credentials(ADMIN_USERNAME, ADMIN_PASSWORD)

@dp.message(Command("start"))
async def start_command(message: Message):
    welcome_text = "Добро пожаловать! Вы можете создать ключ для подключения VPN или просмотреть статистику использования."
    
    button_create_key = InlineKeyboardButton(text='Создать ключ', callback_data='create_key')
    button_view_stats = InlineKeyboardButton(text='Посмотреть статистику', callback_data='view_stats')
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[button_create_key], [button_view_stats]])
    
    await message.reply(welcome_text, reply_markup=keyboard)

@dp.callback_query(F.data == 'create_key')
async def process_callback_create_key(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.message.reply("Введите имя вашего профиля VPN:")
    await state.set_state(Form.waiting_for_key_name)
    await callback_query.answer()

@dp.callback_query(F.data == 'view_stats')
async def process_callback_view_stats(callback_query: types.CallbackQuery, state: FSMContext):
    tg_id = callback_query.from_user.id
    
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            async with db.execute("SELECT email FROM connections WHERE tg_id = ? AND expiry_time > ?", (tg_id, int(datetime.utcnow().timestamp() * 1000))) as cursor:
                record = await cursor.fetchone()
                if record:
                    email = record[0]
                    connection_link = link(session, email)
                    
                    # Извлечение данных о загрузке и выгрузке из ссылки
                    up_match = re.search(r'up=(\d+)', connection_link)
                    down_match = re.search(r'down=(\d+)', connection_link)
                    
                    up = up_match.group(1) if up_match else "Неизвестно"
                    down = down_match.group(1) if down_match else "Неизвестно"
                    
                    statistics = f"Статистика вашего ключа:\nЗагрузка: {up} MB\nВыгрузка: {down} MB"
                else:
                    statistics = "У вас нет активных ключей."
    
    except Exception as e:
        statistics = f"Ошибка при получении статистики: {e}"
    
    await callback_query.message.reply(f"Ваша статистика:\n{statistics}")
    await callback_query.answer()

@dp.message()
async def handle_text(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    
    if current_state == Form.waiting_for_key_name.state:
        try:
            tg_id = message.from_user.id
            
            # Проверка на наличие активного ключа
            async with aiosqlite.connect(DATABASE_PATH) as db:
                async with db.execute("SELECT COUNT(*) FROM connections WHERE tg_id = ? AND expiry_time > ?", (tg_id, int(datetime.utcnow().timestamp() * 1000))) as cursor:
                    count = await cursor.fetchone()
                    if count[0] > 0:
                        await message.reply("У вас уже есть активный ключ. Один клиент может иметь только один активный ключ.")
                        return
            
            # Создание уникального ID клиента
            client_id = str(uuid.uuid4())
            email = message.text
            limit_ip = 1
            total_gb = 0
            current_time = datetime.utcnow()
            expiry_time = int((current_time + timedelta(days=30)).timestamp() * 1000)
            enable = True
            flow = "xtls-rprx-vision"

            add_client(session, client_id, email, tg_id, limit_ip, total_gb, expiry_time, enable, flow)

            # Получение ссылки на подключение
            connection_link = link(session, email)

            # Сохранение данных в базу данных
            await add_connection(tg_id, client_id, email, expiry_time)

            # Отправка ключа в виде цитаты
            await message.reply(f"Ключ создан:\n<pre>{connection_link}</pre>", parse_mode="HTML")

            # Сброс состояния
            await state.clear()
        except Exception as e:
            await message.reply(f"Ошибка: {e}")

async def main():
    dp.include_router(router)  # Подключение роутера
    await dp.start_polling(bot)  # Запуск поллинга

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())

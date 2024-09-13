from aiogram import Bot, Dispatcher, Router, F, types
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.filters import Command
import requests
# from database import add_user, get_user, update_subscription
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import asyncio
import json
from datetime import datetime, timedelta
import uuid


API_TOKEN = '7436953421:AAH9iUg86cLC8oxO_XZeqYEfSBhDGBodV5o'
API_URL = "https://vpn.pocomacho.ru:34268/solonet"
ADMIN_USERNAME = "paneladmin"
ADMIN_PASSWORD = "L7CMDASN9x7g"
ADD_CLIENT_URL = f"{API_URL}/panel/api/inbounds/addClient"

bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()

class Form(StatesGroup):
    waiting_for_key_name = State()

def login_with_credentials(username, password):
    auth_url = "https://vpn.pocomacho.ru:34268/solonet/login/"
    data = {
        "username": ADMIN_USERNAME,
        "password": ADMIN_PASSWORD
    }
    response = requests.post(auth_url, json=data)
    if response.status_code == 200:
        # Если API возвращает токен, используйте его в заголовке
        # return {"Authorization": f"Bearer {response.json().get('token')}"}

        # Если API использует сессии (например, с cookies)
        session = requests.Session()
        session.cookies.update(response.cookies)
        return session
    
    else:
        raise Exception(f"Ошибка авторизации: {response.status_code}, {response.text}")

def add_client(session, client_id, email, tg_id, limit_ip, total_gb, expiry_time, enable, flow):
    settings = {
        "clients": [
            {
                "id": client_id,
                "alterId": 0,
                "email": email,
                "limitIp": limit_ip,
                "totalGB": total_gb,
                "expiryTime": expiry_time,
                "enable": enable,
                "tgId": tg_id,
                "subId": "",
                "flow": flow
            }
        ]
    }
    
    data = {
        "id": 1,  # Можно изменить в зависимости от ваших требований
        "settings": json.dumps(settings)
    }
    
    headers = {
        "Accept": "application/json"
    }
    
    response = session.post(ADD_CLIENT_URL, headers=headers, json=data)
    
    if response.status_code == 200:
        return response.json()
    else:
        raise Exception(f"Ошибка при добавлении клиента: {response.status_code}, {response.text}")

# Обработчик команды /start
@dp.message(Command("start"))
async def start_command(message: Message):
    # Создаем кнопки
    button_create_key = InlineKeyboardButton(text='Создать ключ', callback_data='create_key')
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[button_create_key]])
    
    await message.reply("Выберите действие:", reply_markup=keyboard)

# Обработчик нажатия кнопки
@dp.callback_query(F.data == 'create_key')
async def process_callback_create_key(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.message.reply("Введите имя для нового ключа:")
    await state.set_state(Form.waiting_for_key_name)  # Устанавливаем состояние

    await callback_query.answer()

# Обработчик текстовых сообщений
@dp.message()
async def handle_text(message: types.Message, state: FSMContext):
    # Получаем текущее состояние
    current_state = await state.get_state()
    
    if current_state == Form.waiting_for_key_name.state:
        try:
            # Авторизация
            session = login_with_credentials(ADMIN_USERNAME, ADMIN_PASSWORD)

            # Параметры клиента
            client_id = str(uuid.uuid4())  # Пример UUID
            email = message.text
            limit_ip = 1
            tg_id = message.from_user.id
            total_gb = 0 # В байтах
            current_time = datetime.utcnow()
            expiry_time = int((current_time + timedelta(days=30)).timestamp() * 1000)    # Временная метка в миллисекундах
            enable = True
            flow = "xtls-rprx-vision"

            # Добавление клиента
            result = add_client(session, client_id, email, tg_id, limit_ip, total_gb, expiry_time, enable, flow)

            await message.reply(f"Ключ создан: {result}")

            # Сброс состояния
            await state.clear()
        except Exception as e:
            await message.reply(f"Произошла ошибка: {e}")

# Запуск бота
async def main():
    dp.include_router(router)  # Подключение роутера
    await dp.start_polling(bot)  # Запуск поллинга

if __name__ == '__main__':
    asyncio.run(main())

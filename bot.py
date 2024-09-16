from aiogram import Bot, Dispatcher, Router, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from auth import login_with_credentials, link
from client import add_client
from datetime import datetime, timedelta
from config import API_TOKEN, ADMIN_PASSWORD, ADMIN_USERNAME
from database import add_connection, has_active_key, get_active_key_email, DATABASE_PATH
import uuid
import re
import aiosqlite

class Form(StatesGroup):
    waiting_for_key_name = State()
    waiting_for_admin_confirmation = State()
    waiting_for_statistics = State()
    waiting_for_expiry_date = State()

bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot=bot, storage=storage)
router = Router()

# Создаем сессию при старте бота
session = login_with_credentials(ADMIN_USERNAME, ADMIN_PASSWORD)

# ID администратора
ADMIN_ID = 476217106  # Замените на ваш Telegram ID

@dp.message(Command("start"))
async def start_command(message: Message):
    welcome_text = "Добро пожаловать! Вы можете создать ключ для подключения VPN, просмотреть статистику использования или узнать дату окончания ключа."

    button_create_key = InlineKeyboardButton(text='Создать ключ', callback_data='create_key')
    button_view_stats = InlineKeyboardButton(text='Посмотреть статистику', callback_data='view_stats')
    button_view_expiry = InlineKeyboardButton(text='Дата окончания ключа', callback_data='view_expiry')
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[button_create_key], [button_view_stats], [button_view_expiry]])
    
    await message.reply(welcome_text, reply_markup=keyboard)

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
        
        if await has_active_key(tg_id):
            await message.reply("У вас уже есть активный ключ. Вы не можете создать новый.")
            await state.clear()
            return
        
        # Сохраняем введенное имя профиля
        profile_name = message.text
        await state.update_data(profile_name=profile_name)
        
        # Отправляем запрос на подтверждение админу
        admin_text = f"Пользователь {tg_id} запросил создание ключа для профиля '{profile_name}'. Подтвердите создание ключа (Да/Нет)."
        await bot.send_message(ADMIN_ID, admin_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Да", callback_data=f'confirm_create_{tg_id}_{profile_name}')],
            [InlineKeyboardButton(text="Нет", callback_data=f'reject_create_{tg_id}')]
        ]))
        
        await message.reply("Запрос на создание ключа отправлен администратору для подтверждения.")
        await state.set_state(Form.waiting_for_admin_confirmation)

    elif current_state == Form.waiting_for_admin_confirmation.state:
        await message.reply("Ожидание подтверждения от администратора.")
        
@dp.callback_query(F.data.startswith('confirm_create_'))
async def process_admin_confirmation(callback_query: types.CallbackQuery, state: FSMContext):
    _, tg_id, profile_name = callback_query.data.split('_', 2)
    tg_id = int(tg_id)
    
    try:
        client_id = str(uuid.uuid4())
        limit_ip = 1
        total_gb = 0
        current_time = datetime.utcnow()
        expiry_time = int((current_time + timedelta(days=30)).timestamp() * 1000)
        enable = True
        flow = "xtls-rprx-vision"

        add_client(session, client_id, profile_name, tg_id, limit_ip, total_gb, expiry_time, enable, flow)

        connection_link = link(session, profile_name)

        await add_connection(tg_id, client_id, profile_name, expiry_time)

        user_message = f"Ключ создан:\n<pre>{connection_link}</pre>"
        await bot.send_message(tg_id, user_message, parse_mode="HTML")
        
        await callback_query.message.reply("Ключ успешно создан и отправлен пользователю.")
    except Exception as e:
        await callback_query.message.reply(f"Ошибка при создании ключа: {e}")

    await state.clear()

@dp.callback_query(F.data.startswith('reject_create_'))
async def process_admin_rejection(callback_query: types.CallbackQuery, state: FSMContext):
    _, tg_id = callback_query.data.split('_', 1)
    tg_id = int(tg_id)
    
    await bot.send_message(tg_id, "Создание ключа было отклонено администратором.")
    await callback_query.message.reply("Запрос на создание ключа был отклонен.")
    
    await state.clear()

@dp.callback_query(F.data == 'view_stats')
async def process_callback_view_stats(callback_query: types.CallbackQuery, state: FSMContext):
    tg_id = callback_query.from_user.id
    
    try:
        email = await get_active_key_email(tg_id)
        if email:
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



async def main():
    dp.include_router(router)  # Подключение роутера
    await dp.start_polling(bot)  # Запуск поллинга

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())

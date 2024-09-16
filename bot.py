from aiogram import Bot, Dispatcher, Router, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from auth import login_with_credentials, link, get_clients
from client import add_client
from datetime import datetime, timedelta
from config import API_TOKEN, ADMIN_PASSWORD, ADMIN_USERNAME, ADMIN_CHAT_ID
from database import add_connection, has_active_key, get_active_key_email, get_key_expiry_time
import uuid
import aiosqlite

class Form(StatesGroup):
    waiting_for_key_name = State()
    waiting_for_admin_confirmation = State()
    key_to_create = State()  # Состояние для хранения ключа, который будет создан

bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot=bot, storage=storage)
router = Router()

# Создаем сессию при старте бота
session = login_with_credentials(ADMIN_USERNAME, ADMIN_PASSWORD)

@dp.message(Command("start"))
async def start_command(message: Message):
    welcome_text = "Добро пожаловать! Вы можете создать ключ для подключения VPN, просмотреть статистику использования или узнать дату окончания ключа."

    button_create_key = InlineKeyboardButton(text='Создать ключ', callback_data='create_key')
    button_view_stats = InlineKeyboardButton(text='Посмотреть статистику', callback_data='view_stats')
    button_view_expiry = InlineKeyboardButton(text='Дата окончания ключа', callback_data='view_expiry')
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[button_create_key], [button_view_stats], [button_view_expiry]])
    
    await message.reply(welcome_text, reply_markup=keyboard)

@router.callback_query(F.data == "view_expiry")
async def process_callback_view_expiry_date(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    
    # Получаем дату окончания ключа из базы данных
    expiry_time = await get_key_expiry_time(tg_id)
    
    if expiry_time:
        expiry_date = expiry_time.strftime('%Y-%m-%d %H:%M:%S')
        response_message = f"Дата окончания вашего ключа: {expiry_date}"
    else:
        response_message = "У вас нет активного ключа."

    await callback_query.message.reply(response_message)
    await callback_query.answer()

@dp.callback_query(F.data == 'create_key')
async def process_callback_create_key(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.message.reply("Введите имя вашего профиля VPN:")
    await state.set_state(Form.waiting_for_key_name)
    await state.update_data(tg_id=callback_query.from_user.id)  # Сохраняем tg_id для дальнейшего использования
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
        
        # Сохраняем имя ключа и tg_id для подтверждения
        await state.set_state(Form.waiting_for_admin_confirmation)
        await state.update_data(key_name=message.text, tg_id=tg_id)
        
        # Отправляем запрос на подтверждение админу
        admin_message = f"Запрос на создание ключа от пользователя {message.from_user.username}. Имя ключа: {message.text}. Подтвердите создание ключа."
        button_yes = InlineKeyboardButton(text='Подтвердить', callback_data='confirm_key')
        button_no = InlineKeyboardButton(text='Отклонить', callback_data='reject_key')
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[button_yes, button_no]])
        await bot.send_message(ADMIN_CHAT_ID, admin_message, reply_markup=keyboard)
        await message.reply("Ваш запрос на создание ключа отправлен администратору. Ожидайте подтверждения.")
        await state.clear()

@dp.message()
async def handle_text(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    
    if current_state == Form.waiting_for_key_name.state:
        tg_id = message.from_user.id
        
        if await has_active_key(tg_id):
            await message.reply("У вас уже есть активный ключ. Вы не можете создать новый.")
            await state.clear()
            return
        
        await state.set_state(Form.waiting_for_admin_confirmation)
        await state.update_data(key_name=message.text, tg_id=tg_id)  # Сохраняем имя ключа и tg_id для подтверждения
        
        # Отправляем запрос на подтверждение админу
        admin_message = f"Запрос на создание ключа от пользователя {message.from_user.username}. Имя ключа: {message.text}. Подтвердите создание ключа."
        button_yes = InlineKeyboardButton(text='Подтвердить', callback_data='confirm_key')
        button_no = InlineKeyboardButton(text='Отклонить', callback_data='reject_key')
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[button_yes, button_no]])
        await bot.send_message(476217106, admin_message, reply_markup=keyboard)
        await message.reply("Ваш запрос на создание ключа отправлен администратору. Ожидайте подтверждения.")
        await state.clear()

@dp.callback_query(F.data == 'confirm_key')
async def process_admin_confirmation(callback_query: types.CallbackQuery, state: FSMContext):
    user_data = await state.get_data()
    key_name = user_data.get('key_name')
    tg_id = user_data.get('tg_id')
    
    if not key_name or not tg_id:
        await callback_query.answer("Ошибка: не удалось получить данные для создания ключа.")
        return
    
    try:
        # Генерация уникального client_id и создание ключа
        client_id = str(uuid.uuid4())
        email = key_name
        limit_ip = 1
        total_gb = 0
        current_time = datetime.utcnow()
        expiry_time = int((current_time + timedelta(days=30)).timestamp() * 1000)
        enable = True
        flow = "xtls-rprx-vision"

        add_client(session, client_id, email, tg_id, limit_ip, total_gb, expiry_time, enable, flow)

        # Генерация ссылки для подключения
        connection_link = link(session, email)

        # Сохранение данных о подключении в базе данных
        await add_connection(tg_id, client_id, email, expiry_time)

        # Отправка ключа клиенту
        await bot.send_message(tg_id, f"Ключ создан:\n<pre>{connection_link}</pre>", parse_mode="HTML")
        await bot.send_message(callback_query.from_user.id, "Ключ успешно создан и отправлен клиенту.")
        
        await callback_query.answer("Ключ создан и отправлен клиенту.")
    except Exception as e:
        await bot.send_message(callback_query.from_user.id, f"Ошибка: {e}")
    
    await state.clear()

@dp.callback_query(F.data == 'view_stats')
async def process_callback_view_stats(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    
    # Проверяем, есть ли активный ключ для этого пользователя
    email = await get_active_key_email(tg_id)
    
    if not email:
        await callback_query.message.reply("У вас нет активного ключа для просмотра статистики.")
        return
    
    # Получаем статистику через API сервера
    try:
        clients = get_clients(session)  # Предположим, что get_clients возвращает список клиентов с их данными
        client_data = next((client for client in clients if client['email'] == email), None)
        
        if not client_data:
            await callback_query.message.reply("Не удалось получить статистику для этого пользователя.")
            return
        
        # Подготавливаем статистику для отображения
        used_gb = client_data.get('usedGB', 0)  # Использованные данные
        limit_gb = client_data.get('totalGB', 0)  # Лимит данных
        expiry_time = client_data.get('expiryTime', 0)  # Время окончания подписки
        
        expiry_date = datetime.utcfromtimestamp(expiry_time / 1000).strftime('%Y-%m-%d %H:%M:%S')
        
        stats_message = (f"Статистика использования:\n"
                         f"Использовано: {used_gb} ГБ\n"
                         f"Лимит: {limit_gb} ГБ\n"
                         f"Дата окончания ключа: {expiry_date}")
        
        await callback_query.message.reply(stats_message)
        
    except Exception as e:
        await callback_query.message.reply(f"Ошибка при получении статистики: {e}")

    await callback_query.answer()


@dp.callback_query(F.data == 'reject_key')
async def process_admin_rejection(callback_query: types.CallbackQuery, state: FSMContext):
    await bot.send_message(callback_query.from_user.id, "Создание ключа отклонено.")
    await callback_query.answer()
    await state.clear()

async def main():
    dp.include_router(router)  # Подключение роутера
    await dp.start_polling(bot)  # Запуск поллинга

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())

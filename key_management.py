from aiogram import Bot, Dispatcher, Router, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, Message, ReplyKeyboardMarkup, KeyboardButton
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
from bot import dp
from handlers.start import start_command
from bot import bot
from handlers.stats import session

router = Router()

def escape_markdown(text: str) -> str:
    """
    Экранирование специальных символов для Markdown.
    """
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(r'([%s])' % re.escape(escape_chars), r'\\\1', text)

class Form(StatesGroup):
    waiting_for_key_name = State()
    waiting_for_admin_confirmation = State()
    waiting_for_statistics = State()
    waiting_for_expiry_date = State()
    viewing_profile = State() 


@dp.callback_query(F.data == 'create_key')
async def process_callback_create_key(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.message.reply("Введите имя вашего профиля VPN:")
    await state.set_state(Form.waiting_for_key_name)
    await callback_query.answer()

@dp.message()
async def handle_text(message: types.Message, state: FSMContext):
    current_state = await state.get_state()

    if message.text == "В начало":
        await start_command(message)
        return
    
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

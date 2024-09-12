from aiogram import Bot, Dispatcher, Router, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from auth import login_with_credentials, link
from client import add_client, generate_client_id
from datetime import datetime, timedelta
from config import API_TOKEN, ADMIN_PASSWORD, ADMIN_USERNAME



class Form(StatesGroup):
    waiting_for_key_name = State()

bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot=bot, storage=storage)
router = Router()

@dp.message(Command("start"))
async def start_command(message: Message):
    button_create_key = InlineKeyboardButton(text='Создать ключ', callback_data='create_key')
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[button_create_key]])
    
    await message.reply("Выберите действие:", reply_markup=keyboard)

@dp.callback_query(F.data == 'create_key')
async def process_callback_create_key(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.message.reply("Введите имя вашего профиля VPN:")
    await state.set_state(Form.waiting_for_key_name)

    await callback_query.answer()

@dp.message()
async def handle_text(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    
    if current_state == Form.waiting_for_key_name.state:
        try:
            session = login_with_credentials(ADMIN_USERNAME, ADMIN_PASSWORD)

            tg_id = message.from_user.id

            email = message.text
            client_id = generate_client_id()
            email = message.text
            limit_ip = 1
            total_gb = 0
            current_time = datetime.utcnow()
            expiry_time = int((current_time + timedelta(days=30)).timestamp() * 1000)
            enable = True
            flow = "xtls-rprx-vision"

            add_client(session, client_id, email, tg_id, limit_ip, total_gb, expiry_time, enable, flow)
            connection_link = link(session, email)

            await message.reply(f"Ключ создан:\n<pre>{connection_link}</pre>", parse_mode="HTML")

            await state.clear()
        except Exception as e:
            await message.reply(f"Произошла ошибка: {e}")

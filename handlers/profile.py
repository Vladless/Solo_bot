import os

from aiogram import Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, BufferedInputFile

from bot import bot
from database import get_balance, get_key_count, get_referral_stats
from handlers.texts import profile_message_send, invite_message_send, CHANNEL_LINK, get_referral_link
from config import PAYMENT_METHOD
import logging


class ReplenishBalanceState(StatesGroup):
    choosing_transfer_method = State()
    waiting_for_admin_confirmation = State()

router = Router()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def process_callback_view_profile(callback_query: types.CallbackQuery, state: FSMContext):
    tg_id = callback_query.from_user.id
    username = callback_query.from_user.full_name  

    image_path = os.path.join(os.path.dirname(__file__), 'pic.jpg')

    if not os.path.isfile(image_path):
        await bot.send_message(tg_id, "Файл изображения не найден.")
        return

    try:
        key_count = await get_key_count(tg_id)
        balance = await get_balance(tg_id)
        if balance is None:
            balance = 0 

        profile_message = profile_message_send(username, tg_id, balance, key_count)
        
        profile_message += (
            f"<b>Обязательно подпишитесь на канал</b> <a href='{CHANNEL_LINK}'>здесь</a>\n"
        )
        
        if key_count == 0:
            profile_message += "\n<i>Нажмите ➕Устройство снизу, чтобы добавить устройство в VPN</i>"
        
        inline_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='➕ Устройство', callback_data='create_key'), InlineKeyboardButton(text='📱 Мои устр-ва', callback_data='view_keys')],
            [InlineKeyboardButton(text='💳 Пополнить баланс', callback_data='pay_freekassa' if PAYMENT_METHOD == 'freekassa' else 'replenish_balance')],
            [InlineKeyboardButton(text='👥 Пригласить', callback_data='invite'), InlineKeyboardButton(text='📘 Инструкции', callback_data='instructions')],
            [InlineKeyboardButton(text='⬅️ Назад', callback_data='back_to_menu')]
        ])

        # Попробуем удалить предыдущее сообщение
        try:
            await callback_query.message.delete()
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения: {e}")  # Логируем ошибку, если удаление не удалось

        with open(image_path, 'rb') as image_file:
            await bot.send_photo(
                chat_id=tg_id,
                photo=BufferedInputFile(image_file.read(), filename="pic.jpg"),
                caption=profile_message,
                parse_mode='HTML',
                reply_markup=inline_keyboard
            )

    except Exception as e:
        await bot.send_message(tg_id, f"❗️ Ошибка при получении данных профиля: {e}")

    await callback_query.answer()

@router.callback_query(lambda c: c.data == 'invite')
async def invite_handler(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    referral_link = get_referral_link(tg_id)
    
    referral_stats = await get_referral_stats(tg_id)
    
    invite_message = (
        invite_message_send(referral_link,referral_stats)
    )
    
    button_back = InlineKeyboardButton(text='⬅️ Назад', callback_data='view_profile')
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[button_back]])

    await callback_query.message.delete()

    await bot.send_message(
        chat_id=tg_id,
        text=invite_message,
        parse_mode='HTML',
        reply_markup=keyboard
    )

    await callback_query.answer()

@router.callback_query(lambda c: c.data == 'view_profile')
async def view_profile_handler(callback_query: types.CallbackQuery, state: FSMContext):
    await process_callback_view_profile(callback_query, state)

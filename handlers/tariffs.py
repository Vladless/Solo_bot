import os
from aiogram import F, Router
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import bot
from config import CHANNEL_EXISTS, CHANNEL_URL, SUPPORT_CHAT_URL
from handlers.buttons import BACK, INSTRUCTIONS, SUPPORT, CHANNEL, MAIN_MENU
from handlers.utils import edit_or_send_message
from handlers.start import show_start_menu

router = Router()

TARIFFS_TEXT = """
💥 <b>Хочешь свободу без границ? Лови наши тарифы — простые как два байта и честные как мама!</b>

📊 <b>Наши тарифы</b>

🔹 <b>Один девайс</b>
• 7 дней — 59₽
• 1 месяц — 149₽
• 3 месяца — 379₽ (-15%)
• 6 месяцев — 749₽ (-16%)
• 12 месяцев — 1349₽ (-25%)

🔹 <b>БЕЗЛИМИТ на 3 устройства</b>
• 30 дней — 299₽
• 90 дней — 799₽ (-11%)
• 180 дней — 1399₽ (-22%)
• 365 дней — 2499₽ (-30%)

💳 <b>Способы оплаты:</b> ЮMoney (карта) CryptoBot (крипта), звёзды Telegram
"""

@router.callback_query(F.data == "show_tariffs")
async def show_tariffs(callback_query: CallbackQuery, session, admin: bool):
    builder = InlineKeyboardBuilder()
    
    # Add personal account button
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
    
    # Add support and channel buttons in one row
    if CHANNEL_EXISTS:
        builder.row(
            InlineKeyboardButton(text=SUPPORT, url=SUPPORT_CHAT_URL),
            InlineKeyboardButton(text=CHANNEL, url=CHANNEL_URL),
        )
    else:
        builder.row(InlineKeyboardButton(text=SUPPORT, url=SUPPORT_CHAT_URL))
    
    # Add back button
    builder.row(InlineKeyboardButton(text=BACK, callback_data="start"))
    
    # Send the message with the same style as start menu
    image_path = os.path.join("img", "pic.jpg")  # Same image as start menu
    await edit_or_send_message(
        target_message=callback_query.message,
        text=TARIFFS_TEXT,
        reply_markup=builder.as_markup(),
        media_path=image_path
    )

from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import bot
from logger import logger


async def send_payment_success_notification(user_id: int, amount: float):
    try:
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile")
        )
        await bot.send_message(
            chat_id=user_id,
            text=f"Ваш баланс успешно пополнен на {amount} рублей. Спасибо за оплату!",
            reply_markup=builder.as_markup(),
        )
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления пользователю {user_id}: {e}")

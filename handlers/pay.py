from aiogram import F, Router
from aiogram.types import CallbackQuery

from keyboards.pay_kb import build_pay_kb

router = Router()


@router.callback_query(F.data == "pay")
async def handle_pay(callback_query: CallbackQuery):
    # Prepare text
    text = (
        "💸 <b>Выберите удобный способ пополнения баланса:</b>\n\n"
        "• Быстро и безопасно\n"
        "• Поддержка разных платежных систем\n"
        "• Моментальное зачисление средств 🚀"
    )

    # Build keyboard
    kb = build_pay_kb()

    # Answer message
    await callback_query.message.answer(
        text=text,
        reply_markup=kb,
    )

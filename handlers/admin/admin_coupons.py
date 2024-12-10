from typing import Any

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import create_coupon, delete_coupon_from_db, get_all_coupons
from filters.admin import IsAdminFilter
from logger import logger


class AdminCouponsState(StatesGroup):
    waiting_for_coupon_data = State()


router = Router()


@router.callback_query(F.data == "coupons_editor", IsAdminFilter())
async def show_coupon_management_menu(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="➕ Создать купон", callback_data="create_coupon"))
    builder.row(InlineKeyboardButton(text="Купоны", callback_data="coupons"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin"))
    await callback_query.message.answer("🛠 Меню управления купонами:", reply_markup=builder.as_markup())


@router.callback_query(F.data == "coupons", IsAdminFilter())
async def show_coupon_list(callback_query: types.CallbackQuery, session: Any):
    try:
        coupons = await get_all_coupons(session)

        if not coupons:
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="coupons_editor"))

            await callback_query.message.answer(
                "❌ На данный момент нет доступных купонов. 🚫\nВы можете вернуться в меню управления. 🔙",
                reply_markup=builder.as_markup(),
            )
            return

        coupon_list = "📜 Список всех купонов:\n\n"
        builder = InlineKeyboardBuilder()

        for coupon in coupons:
            coupon_list += (
                f"🏷️ <b>Код:</b> {coupon['code']}\n"
                f"💰 <b>Сумма:</b> {coupon['amount']} рублей\n"
                f"🔢 <b>Лимит использования:</b> {coupon['usage_limit']} раз\n"
                f"✅ <b>Использовано:</b> {coupon['usage_count']} раз\n\n"
            )

            builder.row(
                InlineKeyboardButton(
                    text=f"❌ Удалить {coupon['code']}",
                    callback_data=f"delete_coupon_{coupon['code']}",
                )
            )

        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="coupons_editor"))
        await callback_query.message.answer(coupon_list, reply_markup=builder.as_markup())

    except Exception as e:
        logger.error(f"Ошибка при получении списка купонов: {e}")


@router.callback_query(F.data.startswith("delete_coupon_"), IsAdminFilter())
async def handle_delete_coupon(callback_query: types.CallbackQuery, session: Any):
    coupon_code = callback_query.data[len("delete_coupon_") :]

    try:
        result = await delete_coupon_from_db(coupon_code, session)

        if result:
            await show_coupon_list(callback_query, session)
        else:
            await callback_query.message.answer(
                f"❌ Купон с кодом <b>{coupon_code}</b> не найден.",
            )
            await show_coupon_list(callback_query, session)

    except Exception as e:
        logger.error(f"Ошибка при удалении купона: {e}")


@router.callback_query(F.data == "create_coupon", IsAdminFilter())
async def handle_create_coupon(callback_query: types.CallbackQuery, state: FSMContext):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="coupons_editor"))

    await callback_query.message.answer(
        "🎫 <b>Введите данные для создания купона в формате:</b>\n\n"
        "📝 <i>код</i> 💰 <i>сумма</i> 🔢 <i>лимит</i>\n\n"
        "Пример: <b>'COUPON1 50 5'</b> 👈\n\n",
        reply_markup=builder.as_markup(),
    )
    await state.set_state(AdminCouponsState.waiting_for_coupon_data)


@router.message(AdminCouponsState.waiting_for_coupon_data, IsAdminFilter())
async def process_coupon_data(message: types.Message, state: FSMContext, session: Any):
    text = message.text.strip()

    parts = text.split()

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="coupons_editor"))

    if len(parts) != 3:
        await message.answer(
            "❌ <b>Некорректный формат!</b> 📝 Пожалуйста, введите данные в формате:\n"
            "🏷️ <b>код</b> 💰 <b>сумма</b> 🔢 <b>лимит</b>\n"
            "Пример: <b>'COUPON1 50 5'</b> 👈",
            reply_markup=builder.as_markup(),
        )
        return

    try:
        coupon_code = parts[0]
        coupon_amount = float(parts[1])
        usage_limit = int(parts[2])
    except ValueError:
        await message.answer(
            "⚠️ <b>Проверьте правильность введенных данных!</b>\n"
            "💱 Сумма должна быть числом, 🔢 а лимит — целым числом.",
            reply_markup=builder.as_markup(),
        )
        return

    try:
        await create_coupon(coupon_code, coupon_amount, usage_limit, session)

        result_message = (
            f"✅ Купон с кодом <b>{coupon_code}</b> успешно создан! 🎉\n"
            f"Сумма: <b>{coupon_amount} рублей</b> 💰\n"
            f"Лимит использования: <b>{usage_limit} раз</b> 🔢."
        )

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="coupons_editor"))

        await message.answer(result_message, reply_markup=builder.as_markup())
        await state.clear()

    except Exception as e:
        logger.error(f"Ошибка при создании купона: {e}")

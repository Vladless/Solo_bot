from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import create_coupon, delete_coupon_from_db, get_all_coupons
from logger import logger


class AdminCouponsState(StatesGroup):
    waiting_for_coupon_data = State()


router = Router()


@router.callback_query(F.data == "coupons_editor")
async def show_coupon_management_menu(callback_query: types.CallbackQuery):
    try:
        await callback_query.message.delete()
    except Exception as e:
        logger.error(f"Ошибка при удалении сообщения: {e}")

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="➕ Создать купон", callback_data="create_coupon")
    )
    builder.row(InlineKeyboardButton(text="Купоны", callback_data="coupons"))
    builder.row(
        InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_admin_menu")
    )

    markup = builder.as_markup()
    await callback_query.message.answer(
        "🛠 Меню управления купонами:", reply_markup=markup
    )
    await callback_query.answer()


@router.callback_query(F.data == "coupons")
async def show_coupon_list(callback_query: types.CallbackQuery):
    try:
        try:
            await callback_query.message.delete()
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения: {e}")

        coupons = await get_all_coupons()

        if not coupons:
            builder = InlineKeyboardBuilder()
            builder.row(
                InlineKeyboardButton(text="🔙 Назад", callback_data="coupons_editor")
            )
            markup = builder.as_markup()

            await callback_query.message.answer(
                "❌ На данный момент нет доступных купонов.\n"
                "Вы можете вернуться в меню управления.",
                parse_mode="HTML",
                reply_markup=markup,
            )
            await callback_query.answer()
            return

        coupon_list = "📜 Список всех купонов:\n\n"
        builder = InlineKeyboardBuilder()

        for coupon in coupons:
            coupon_list += (
                f"<b>Код:</b> {coupon['code']}\n"
                f"<b>Сумма:</b> {coupon['amount']} рублей\n"
                f"<b>Лимит использования:</b> {coupon['usage_limit']} раз\n"
                f"<b>Использовано:</b> {coupon['usage_count']} раз\n\n"
            )

            builder.row(
                InlineKeyboardButton(
                    text=f"❌ Удалить {coupon['code']}",
                    callback_data=f"delete_coupon_{coupon['code']}",
                )
            )

        builder.row(
            InlineKeyboardButton(text="🔙 Назад", callback_data="coupons_editor")
        )

        markup = builder.as_markup()
        await callback_query.message.answer(
            coupon_list, parse_mode="HTML", reply_markup=markup
        )

    except Exception as e:
        logger.error(f"Ошибка при получении списка купонов: {e}")
        await callback_query.message.answer(
            f"❌ Произошла ошибка при получении списка купонов: {e}", parse_mode="HTML"
        )
    await callback_query.answer()


@router.callback_query(F.data.startswith("delete_coupon_"))
async def handle_delete_coupon(callback_query: types.CallbackQuery):
    coupon_code = callback_query.data[len("delete_coupon_") :]

    try:
        result = await delete_coupon_from_db(coupon_code)

        if result:
            try:
                await callback_query.message.delete()
            except Exception as e:
                logger.error(f"Ошибка при удалении сообщения: {e}")

            await show_coupon_list(callback_query)
        else:
            await callback_query.message.answer(
                f"❌ Купон с кодом <b>{coupon_code}</b> не найден.", parse_mode="HTML"
            )
            await show_coupon_list(callback_query)

    except Exception as e:
        logger.error(f"Ошибка при удалении купона: {e}")
        await callback_query.message.answer(
            f"❌ Произошла ошибка при удалении купона: {e}", parse_mode="HTML"
        )
    await callback_query.answer()


@router.callback_query(F.data == "create_coupon")
async def handle_create_coupon(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        await callback_query.message.delete()
    except Exception as e:
        logger.error(f"Ошибка при удалении сообщения: {e}")

    await callback_query.message.answer(
        "<b>Введите данные для создания купона в формате:</b>\n\n"
        "<i>код</i> <i>сумма</i> <i>лимит</i>\n\n"
        "Пример: <b>'COUPON1 50 5'</b>\n\n",
        parse_mode="HTML",
    )
    await state.set_state(AdminCouponsState.waiting_for_coupon_data)
    await callback_query.answer()


@router.message(AdminCouponsState.waiting_for_coupon_data)
async def process_coupon_data(message: types.Message, state: FSMContext):
    text = message.text.strip()

    parts = text.split()
    if len(parts) != 3:
        await message.answer(
            "<b>❌ Некорректный формат!</b> Пожалуйста, введите данные в формате:\n"
            "<b>код</b> <b>сумма</b> <b>лимит</b>\n"
            "Пример: <b>'COUPON1 50 5'</b>",
            parse_mode="HTML",
        )
        return

    try:
        coupon_code = parts[0]
        coupon_amount = float(parts[1])
        usage_limit = int(parts[2])
    except ValueError:
        await message.answer(
            "<b>⚠️ Проверьте правильность введенных данных.</b>\n"
            "Сумма должна быть числом, а лимит — целым числом.",
            parse_mode="HTML",
        )
        return

    try:
        await create_coupon(coupon_code, coupon_amount, usage_limit)

        result_message = (
            f"✅ Купон с кодом <b>{coupon_code}</b> успешно создан!\n"
            f"Сумма: <b>{coupon_amount} рублей</b>\n"
            f"Лимит использования: <b>{usage_limit} раз</b>."
        )

        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="🔙 Назад", callback_data="coupons_editor")
        )
        markup = builder.as_markup()

        try:
            await message.delete()
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения: {e}")

        await message.answer(result_message, parse_mode="HTML", reply_markup=markup)

        await state.clear()

    except Exception as e:
        logger.error(f"Ошибка при создании купона: {e}")
        await message.answer(
            f"<b>❌ Ошибка при создании купона:</b> {e}", parse_mode="HTML"
        )


@router.callback_query(F.data == "back_to_coupons_menu")
async def back_to_coupons_menu(callback_query: types.CallbackQuery):
    """Возвращаем пользователя в меню управления купонами"""
    await show_coupon_management_menu(callback_query)

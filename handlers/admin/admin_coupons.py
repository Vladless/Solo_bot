from typing import Any

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import USERNAME_BOT
from database import create_coupon, delete_coupon, get_all_coupons
from filters.admin import IsAdminFilter
from keyboards.admin.coupons_kb import AdminCouponDeleteCallback, build_coupons_kb, build_coupons_list_kb
from keyboards.admin.panel_kb import AdminPanelCallback, build_admin_back_kb
from logger import logger

router = Router()


class AdminCouponsState(StatesGroup):
    waiting_for_coupon_data = State()


@router.callback_query(
    AdminPanelCallback.filter(F.action == "coupons"),
    IsAdminFilter(),
)
async def handle_coupons(
    callback_query: CallbackQuery,
):
    await callback_query.message.edit_text(text="🛠 Меню управления купонами:", reply_markup=build_coupons_kb())


@router.callback_query(
    AdminPanelCallback.filter(F.action == "coupons_create"),
    IsAdminFilter(),
)
async def handle_coupons_create(callback_query: CallbackQuery, state: FSMContext):
    text = (
        "🎫 <b>Введите данные для создания купона в формате:</b>\n\n"
        "📝 <i>код</i> 💰 <i>сумма</i> 🔢 <i>лимит</i>\n\n"
        "Пример: <b>'COUPON1 50 5'</b> 👈\n\n"
    )

    await callback_query.message.edit_text(
        text=text,
        reply_markup=build_admin_back_kb("coupons"),
    )
    await state.set_state(AdminCouponsState.waiting_for_coupon_data)


@router.message(AdminCouponsState.waiting_for_coupon_data, IsAdminFilter())
async def handle_coupon_data_input(message: Message, state: FSMContext, session: Any):
    text = message.text.strip()
    parts = text.split()

    kb = build_admin_back_kb("coupons")

    if len(parts) != 3:
        text = (
            "❌ <b>Некорректный формат!</b> 📝 Пожалуйста, введите данные в формате:\n"
            "🏷️ <b>код</b> 💰 <b>сумма</b> 🔢 <b>лимит</b>\n"
            "Пример: <b>'COUPON1 50 5'</b> 👈"
        )

        await message.answer(
            text=text,
            reply_markup=kb,
        )
        return

    try:
        coupon_code = parts[0]
        coupon_amount = float(parts[1])
        usage_limit = int(parts[2])
    except ValueError:
        text = "⚠️ <b>Проверьте правильность введенных данных!</b>\n💱 Сумма должна быть числом, а лимит — целым числом."

        await message.answer(
            text=text,
            reply_markup=kb,
        )
        return

    try:
        await create_coupon(coupon_code, coupon_amount, usage_limit, session)

        text = (
            f"✅ Купон с кодом <b>{coupon_code}</b> успешно создан!\n"
            f"💰 Сумма: <b>{coupon_amount} рублей</b> \n"
            f"🔢 Лимит использования: <b>{usage_limit} раз</b>\n"
            f"🔗 <b>Ссылка:</b> <code>https://t.me/{USERNAME_BOT}?start=coupons_{coupon_code}</code>\n"
        )

        await message.answer(text=text, reply_markup=kb)
        await state.clear()

    except Exception as e:
        logger.error(f"Ошибка при создании купона: {e}")


@router.callback_query(
    AdminPanelCallback.filter(F.action == "coupons_list"),
    IsAdminFilter(),
)
async def handle_coupons_list(callback_query: CallbackQuery, session: Any):
    try:
        if ":" in callback_query.data:
            parts = callback_query.data.split(":")
            if len(parts) > 1 and parts[1].isdigit():
                page = int(parts[1])
            else:
                page = 1
        else:
            page = 1

        per_page = 10
        result = await get_all_coupons(session, page, per_page)
        coupons = result["coupons"]
        total_pages = result["pages"]
        current_page = result["current_page"]

        if not coupons:
            await callback_query.message.edit_text(
                text="❌ На данный момент нет доступных купонов!",
                reply_markup=build_admin_back_kb("coupons"),
            )
            return

        kb = build_coupons_list_kb(coupons)
        coupon_list = "📜 Список всех купонов:\n\n"

        for coupon in coupons:
            coupon_list += (
                f"🏷️ <b>Код:</b> {coupon['code']}\n"
                f"💰 <b>Сумма:</b> {coupon['amount']} рублей\n"
                f"🔢 <b>Лимит использования:</b> {coupon['usage_limit']} раз\n"
                f"✅ <b>Использовано:</b> {coupon['usage_count']} раз\n"
                f"🔗 <b>Ссылка:</b> <code>https://t.me/{USERNAME_BOT}?start=coupons_{coupon['code']}</code>\n"
            )

        await callback_query.message.edit_text(text=coupon_list, reply_markup=kb)

    except Exception as e:
        logger.error(f"Ошибка при получении списка купонов: {e}")
        await callback_query.message.answer("Произошла ошибка при получении списка купонов.")


@router.callback_query(
    AdminCouponDeleteCallback.filter(),
    IsAdminFilter(),
)
async def handle_coupon_delete(callback_query: CallbackQuery, callback_data: AdminCouponDeleteCallback, session: Any):
    coupon_code = callback_data.coupon_code

    try:
        result = await delete_coupon(coupon_code, session)

        if result:
            await handle_coupons_list(callback_query, session)
        else:
            await callback_query.message.edit_text(
                text=f"❌ Купон с кодом <b>{coupon_code}</b> не найден.",
            )
            await handle_coupons_list(callback_query, session)

    except Exception as e:
        logger.error(f"Ошибка при удалении купона: {e}")

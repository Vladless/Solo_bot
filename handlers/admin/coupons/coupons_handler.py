from typing import Any

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import INLINE_MODE, USERNAME_BOT
from database import create_coupon, delete_coupon, get_all_coupons
from filters.admin import IsAdminFilter
from handlers.buttons import BACK
from handlers.utils import format_days
from logger import logger

from ..panel.keyboard import AdminPanelCallback, build_admin_back_kb
from .keyboard import (
    AdminCouponDeleteCallback,
    build_coupons_kb,
    build_coupons_list_kb,
    format_coupons_list,
)

router = Router()


class AdminCouponsState(StatesGroup):
    waiting_for_coupon_type = State()
    waiting_for_coupon_audience = State()
    waiting_for_balance_data = State()
    waiting_for_days_data = State()
    waiting_for_percent_data = State()


@router.callback_query(
    AdminPanelCallback.filter(F.action == "coupons"),
    IsAdminFilter(),
)
async def handle_coupons(callback_query: CallbackQuery):
    await callback_query.message.edit_text(text="🛠 Меню управления купонами:", reply_markup=build_coupons_kb())


@router.callback_query(
    AdminPanelCallback.filter(F.action == "coupons_create"),
    IsAdminFilter(),
)
async def handle_coupons_create(callback_query: CallbackQuery, state: FSMContext):
    text = "🎫 <b>Выберите тип купона:</b>"
    kb = InlineKeyboardBuilder()
    kb.button(text="💰 Баланс", callback_data="coupon_type_balance")
    kb.button(text="⏳ Время", callback_data="coupon_type_days")
    kb.button(text="📉 Процент", callback_data="coupon_type_percent")
    kb.button(text=BACK, callback_data=AdminPanelCallback(action="coupons").pack())
    kb.adjust(1)

    await callback_query.message.edit_text(text=text, reply_markup=kb.as_markup())
    await state.set_state(AdminCouponsState.waiting_for_coupon_type)


async def show_coupon_audience_step(callback_query: CallbackQuery, state: FSMContext, coupon_type: str):
    await state.update_data(coupon_type=coupon_type)

    text = "🎯 <b>Кому доступен купон?</b>"
    kb = InlineKeyboardBuilder()
    kb.button(text="👤 Всем", callback_data="coupon_audience_all")
    kb.button(text="🆕 Только новым", callback_data="coupon_audience_new")
    kb.button(text=BACK, callback_data=AdminPanelCallback(action="coupons").pack())
    kb.adjust(1)

    await callback_query.message.edit_text(text=text, reply_markup=kb.as_markup())
    await state.set_state(AdminCouponsState.waiting_for_coupon_audience)


@router.callback_query(F.data == "coupon_type_balance", IsAdminFilter())
async def handle_balance_coupon_selection(callback_query: CallbackQuery, state: FSMContext):
    await show_coupon_audience_step(callback_query, state, "balance")


@router.callback_query(F.data == "coupon_type_days", IsAdminFilter())
async def handle_days_coupon_selection(callback_query: CallbackQuery, state: FSMContext):
    await show_coupon_audience_step(callback_query, state, "days")


@router.callback_query(F.data == "coupon_type_percent", IsAdminFilter())
async def handle_percent_coupon_selection(callback_query: CallbackQuery, state: FSMContext):
    await show_coupon_audience_step(callback_query, state, "percent")


@router.callback_query(F.data.in_(("coupon_audience_all", "coupon_audience_new")), IsAdminFilter())
async def handle_coupon_audience(callback_query: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    coupon_type = data.get("coupon_type")
    if coupon_type not in ("balance", "days", "percent"):
        await callback_query.answer("Ошибка: тип купона не найден", show_alert=True)
        return

    await state.update_data(new_users_only=callback_query.data == "coupon_audience_new")

    kb = InlineKeyboardBuilder()
    kb.button(text=BACK, callback_data=AdminPanelCallback(action="coupons").pack())
    kb.adjust(1)

    if coupon_type == "balance":
        text = (
            "🎫 <b>Введите данные для создания купона в формате:</b>\n\n"
            "📝 <i>код</i> 💰 <i>сумма</i> 🔢 <i>лимит</i>\n\n"
            "Пример: <b>'COUPON1 50 5'</b>\n\n"
        )
        await callback_query.message.edit_text(text=text, reply_markup=kb.as_markup())
        await state.set_state(AdminCouponsState.waiting_for_balance_data)
        return

    if coupon_type == "days":
        text = (
            "🎫 <b>Введите данные для создания купона в формате:</b>\n\n"
            "📝 <i>код</i> ⏳ <i>дни</i> 🔢 <i>лимит</i>\n\n"
            "Пример: <b>'DAYS10 10 50'</b>\n\n"
        )
        await callback_query.message.edit_text(text=text, reply_markup=kb.as_markup())
        await state.set_state(AdminCouponsState.waiting_for_days_data)
        return

    text = (
        "🎫 <b>Введите данные для создания купона в формате:</b>\n\n"
        "📝 <i>код</i> 📉 <i>процент</i> 🔢 <i>лимит</i>\n\n"
        "Пример: <b>'SALE20 20 10'</b>\n"
        "Где 20 — это скидка 20%\n\n"
    )
    await callback_query.message.edit_text(text=text, reply_markup=kb.as_markup())
    await state.set_state(AdminCouponsState.waiting_for_percent_data)


@router.message(AdminCouponsState.waiting_for_balance_data, IsAdminFilter())
async def handle_balance_coupon_input(message: Message, state: FSMContext, session: Any):
    text = message.text.strip()
    parts = text.split()

    kb = InlineKeyboardBuilder()
    kb.button(text=BACK, callback_data=AdminPanelCallback(action="coupons").pack())
    kb.adjust(1)

    if len(parts) != 3:
        text = (
            "❌ <b>Некорректный формат!</b>\n"
            "🏷️ <b>код</b> 💰 <b>сумма</b> 🔢 <b>лимит</b>\n"
            "Пример: <b>'COUPON1 50 5'</b>"
        )
        await message.answer(text=text, reply_markup=kb.as_markup())
        return

    try:
        coupon_code = parts[0]
        coupon_amount = int(parts[1])
        usage_limit = int(parts[2])
        if coupon_amount <= 0:
            raise ValueError
        if usage_limit <= 0:
            raise ValueError
    except ValueError:
        text = "⚠️ <b>Проверьте данные!</b>\nСумма и лимит должны быть целыми числами больше 0."
        await message.answer(text=text, reply_markup=kb.as_markup())
        return

    try:
        data = await state.get_data()
        new_users_only = bool(data.get("new_users_only"))

        ok = await create_coupon(
            session,
            coupon_code,
            coupon_amount,
            usage_limit,
            days=None,
            new_users_only=new_users_only,
            percent=None,
        )
        if not ok:
            await message.answer("❌ Купон с таким кодом уже существует.", reply_markup=kb.as_markup())
            return

        coupon_link = f"https://t.me/{USERNAME_BOT}?start=coupons_{coupon_code}"
        audience_txt = "🆕 Только новым" if new_users_only else "👤 Всем"

        text = (
            f"✅ Купон <b>{coupon_code}</b> создан!\n"
            f"💰 Сумма: <b>{coupon_amount} рублей</b>\n"
            f"🔢 Лимит: <b>{usage_limit} раз</b>\n"
            f"🎯 Доступ: <b>{audience_txt}</b>\n"
            f"🔗 <b>Ссылка:</b> <code>{coupon_link}</code>\n"
        )

        kb = InlineKeyboardBuilder()
        if INLINE_MODE:
            kb.button(text="📤 Поделиться", switch_inline_query=f"coupon_{coupon_code}")
        kb.button(text=BACK, callback_data=AdminPanelCallback(action="coupons").pack())
        kb.adjust(1)

        await message.answer(text=text, reply_markup=kb.as_markup())
        await state.clear()
    except Exception as e:
        logger.error(f"Ошибка при создании купона: {e}")
        await message.answer("❌ Произошла ошибка при создании купона.", reply_markup=kb.as_markup())


@router.message(AdminCouponsState.waiting_for_days_data, IsAdminFilter())
async def handle_days_coupon_input(message: Message, state: FSMContext, session: Any):
    text = message.text.strip()
    parts = text.split()

    kb = InlineKeyboardBuilder()
    kb.button(text=BACK, callback_data=AdminPanelCallback(action="coupons").pack())
    kb.adjust(1)

    if len(parts) != 3:
        text = (
            "❌ <b>Некорректный формат!</b>\n🏷️ <b>код</b> ⏳ <b>дни</b> 🔢 <b>лимит</b>\nПример: <b>'DAYS10 10 50'</b>"
        )
        await message.answer(text=text, reply_markup=kb.as_markup())
        return

    try:
        coupon_code = parts[0]
        days = int(parts[1])
        usage_limit = int(parts[2])
        if days <= 0:
            raise ValueError
        if usage_limit <= 0:
            raise ValueError
    except ValueError:
        text = "⚠️ <b>Проверьте данные!</b>\nДни и лимит должны быть целыми числами больше 0."
        await message.answer(text=text, reply_markup=kb.as_markup())
        return

    try:
        data = await state.get_data()
        new_users_only = bool(data.get("new_users_only"))

        ok = await create_coupon(
            session,
            coupon_code,
            0,
            usage_limit,
            days=days,
            new_users_only=new_users_only,
            percent=None,
        )
        if not ok:
            await message.answer("❌ Купон с таким кодом уже существует.", reply_markup=kb.as_markup())
            return

        coupon_link = f"https://t.me/{USERNAME_BOT}?start=coupons_{coupon_code}"
        audience_txt = "🆕 Только новым" if new_users_only else "👤 Всем"

        text = (
            f"✅ Купон <b>{coupon_code}</b> создан!\n"
            f"⏳ <b>{format_days(days)}</b>\n"
            f"🔢 Лимит: <b>{usage_limit} раз</b>\n"
            f"🎯 Доступ: <b>{audience_txt}</b>\n"
            f"🔗 <b>Ссылка:</b> <code>{coupon_link}</code>\n"
        )

        kb = InlineKeyboardBuilder()
        if INLINE_MODE:
            kb.button(text="📤 Поделиться", switch_inline_query=f"coupon_{coupon_code}")
        kb.button(text=BACK, callback_data=AdminPanelCallback(action="coupons").pack())
        kb.adjust(1)

        await message.answer(text=text, reply_markup=kb.as_markup())
        await state.clear()
    except Exception as e:
        logger.error(f"Ошибка при создании купона: {e}")
        await message.answer("❌ Произошла ошибка при создании купона.", reply_markup=kb.as_markup())


@router.message(AdminCouponsState.waiting_for_percent_data, IsAdminFilter())
async def handle_percent_coupon_input(message: Message, state: FSMContext, session: Any):
    text = message.text.strip()
    parts = text.split()

    kb = InlineKeyboardBuilder()
    kb.button(text=BACK, callback_data=AdminPanelCallback(action="coupons").pack())
    kb.adjust(1)

    if len(parts) != 3:
        text = (
            "❌ <b>Некорректный формат!</b>\n"
            "🏷️ <b>код</b> 📉 <b>процент</b> 🔢 <b>лимит</b>\n"
            "Пример: <b>'SALE20 20 10'</b>"
        )
        await message.answer(text=text, reply_markup=kb.as_markup())
        return

    try:
        coupon_code = parts[0]
        percent = int(parts[1])
        usage_limit = int(parts[2])
        if percent <= 0 or percent > 100:
            raise ValueError
        if usage_limit <= 0:
            raise ValueError
    except ValueError:
        text = "⚠️ <b>Проверьте данные!</b>\nПроцент должен быть 1..100, лимит — целое число больше 0."
        await message.answer(text=text, reply_markup=kb.as_markup())
        return

    try:
        data = await state.get_data()
        new_users_only = bool(data.get("new_users_only"))

        ok = await create_coupon(
            session,
            coupon_code,
            0,
            usage_limit,
            days=None,
            new_users_only=new_users_only,
            percent=percent,
        )
        if not ok:
            await message.answer("❌ Купон с таким кодом уже существует.", reply_markup=kb.as_markup())
            return

        audience_txt = "🆕 Только новым" if new_users_only else "👤 Всем"

        text = (
            f"✅ Купон <b>{coupon_code}</b> создан!\n"
            f"📉 Скидка: <b>{percent}%</b>\n"
            f"🔢 Лимит: <b>{usage_limit} раз</b>\n"
            f"🎯 Доступ: <b>{audience_txt}</b>\n"
        )

        kb = InlineKeyboardBuilder()
        kb.button(text=BACK, callback_data=AdminPanelCallback(action="coupons").pack())
        kb.adjust(1)

        await message.answer(text=text, reply_markup=kb.as_markup())
        await state.clear()
    except Exception as e:
        logger.error(f"Ошибка при создании купона: {e}")
        await message.answer("❌ Произошла ошибка при создании купона.", reply_markup=kb.as_markup())


@router.callback_query(
    AdminPanelCallback.filter(F.action == "coupons_list"),
    IsAdminFilter(),
)
async def handle_coupons_list(callback_query: CallbackQuery, session: Any):
    try:
        data = AdminPanelCallback.unpack(callback_query.data)
        page = data.page if data.page is not None else 1
        await update_coupons_list(callback_query.message, session, page)
    except Exception as e:
        logger.error(f"Ошибка при получении списка купонов: {e}")
        await callback_query.message.edit_text("Произошла ошибка при получении списка купонов.")


@router.callback_query(AdminCouponDeleteCallback.filter(F.confirm.is_(None)), IsAdminFilter())
async def handle_coupon_delete(
    callback_query: CallbackQuery,
    callback_data: AdminCouponDeleteCallback,
    session: Any,
):
    coupon_code = callback_data.coupon_code
    kb = InlineKeyboardBuilder()
    kb.button(
        text="✅ Да, удалить",
        callback_data=AdminCouponDeleteCallback(coupon_code=coupon_code, confirm=True).pack(),
    )
    kb.button(
        text="❌ Нет, отменить",
        callback_data=AdminCouponDeleteCallback(coupon_code=coupon_code, confirm=False).pack(),
    )
    kb.adjust(1)

    await callback_query.message.edit_text(
        f"Вы уверены, что хотите удалить купон <b>{coupon_code}</b>?",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(AdminCouponDeleteCallback.filter(F.confirm.is_not(None)), IsAdminFilter())
async def confirm_coupon_delete(
    callback_query: CallbackQuery,
    callback_data: AdminCouponDeleteCallback,
    session: Any,
):
    coupon_code = callback_data.coupon_code
    confirm = callback_data.confirm

    if confirm:
        try:
            result = await delete_coupon(session, coupon_code)
            if not result:
                await callback_query.message.edit_text(
                    f"❌ Купон с кодом {coupon_code} не найден.",
                    reply_markup=build_admin_back_kb("coupons"),
                )
                return
        except Exception as e:
            logger.error(f"Ошибка при удалении купона: {e}")
            await callback_query.message.edit_text(
                "Произошла ошибка при удалении купона.",
                reply_markup=build_admin_back_kb("coupons"),
            )
            return

    await update_coupons_list(callback_query.message, session)


async def update_coupons_list(message, session: Any, page: int = 1):
    per_page = 10
    result = await get_all_coupons(session, page, per_page)
    coupons = result["coupons"]

    if not coupons:
        await message.edit_text(
            text="❌ На данный момент нет доступных купонов!",
            reply_markup=build_admin_back_kb("coupons"),
        )
        return

    kb = build_coupons_list_kb(coupons, result["current_page"], result["pages"])
    text = format_coupons_list(coupons, USERNAME_BOT)
    await message.edit_text(text=text, reply_markup=kb)


@router.inline_query(F.query.startswith("coupon_"),)
async def inline_coupon_handler(inline_query: InlineQuery, session: Any):
    if not INLINE_MODE:
        return

    coupon_code = inline_query.query.split("coupon_")[1]

    coupons = await get_all_coupons(session, page=1, per_page=10)
    coupon = next((c for c in coupons["coupons"] if c["code"] == coupon_code), None)

    if not coupon:
        await inline_query.answer(
            results=[],
            switch_pm_text="Купон не найден",
            switch_pm_parameter="coupons",
            cache_time=1,
        )
        return

    percent_value = coupon.get("percent")
    if percent_value is not None and int(percent_value) > 0:
        await inline_query.answer(
            results=[],
            switch_pm_text="Процентные купоны не публикуются ссылкой",
            switch_pm_parameter="coupons",
            cache_time=1,
        )
        return

    coupon_link = f"https://t.me/{USERNAME_BOT}?start=coupons_{coupon_code}"
    title = f"Купон {coupon['code']}"

    days_value = coupon.get("days")
    amount_value = coupon.get("amount") or 0

    if days_value is not None and int(days_value) > 0:
        days_int = int(days_value)
        description = f"Продли подписку на {format_days(days_int)}!"
        message_text = (
            f"🎫 <b>Купон:</b> {coupon['code']}\n"
            f"⏳ <b>Продление:</b> {format_days(days_int)}\n"
            f"👇 Нажми, чтобы активировать!"
        )
    elif int(amount_value) > 0:
        amount_int = int(amount_value)
        description = f"Получи {amount_int} рублей!"
        message_text = (
            f"🎫 <b>Купон:</b> {coupon['code']}\n💰 <b>Бонус:</b> {amount_int} рублей\n👇 Нажми, чтобы активировать!"
        )
    else:
        description = "Купон"
        message_text = f"🎫 <b>Купон:</b> {coupon['code']}\n👇 Нажми, чтобы активировать!"

    builder = InlineKeyboardBuilder()
    builder.button(text="Активировать купон", url=coupon_link)

    result = InlineQueryResultArticle(
        id=coupon_code,
        title=title,
        description=description,
        input_message_content=InputTextMessageContent(message_text=message_text, parse_mode=ParseMode.HTML),
        reply_markup=builder.as_markup(),
    )

    await inline_query.answer(results=[result], cache_time=86400, is_personal=True)

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

from core.bootstrap import MODES_CONFIG
from database import create_coupon, delete_coupon, get_all_coupons
from filters.admin import HasPermission, IsAdminFilter
from filters.permissions import PERM_COUPONS
from handlers.utils import format_days, safe_answer_inline_query
from logger import logger
from settings.buttons import BACK
from settings.config import INLINE_MODE, USERNAME_BOT

from ..panel.headers import menu_text, quote
from ..panel.keyboard import AdminPanelCallback, build_admin_back_kb
from .keyboard import (
    AdminCouponDeleteCallback,
    build_coupons_kb,
    build_coupons_list_kb,
    format_coupons_list,
)


router = Router()
router.callback_query.filter(HasPermission(PERM_COUPONS))
router.message.filter(HasPermission(PERM_COUPONS))


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
    text = menu_text(
        "Купоны",
        "Промокоды для клиентов.",
        quote("Дают баланс, дни подписки или скидку на покупку."),
    )
    await callback_query.message.edit_text(text=text, reply_markup=build_coupons_kb())


@router.callback_query(
    AdminPanelCallback.filter(F.action == "coupons_create"),
    IsAdminFilter(),
)
async def handle_coupons_create(callback_query: CallbackQuery, state: FSMContext):
    text = menu_text(
        "Новый купон",
        "Выберите тип.",
        quote("Баланс — деньги на счёт.\nВремя — дни подписки.\nПроцент — скидка на покупку."),
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="💰 Баланс", callback_data="coupon_type_balance")
    kb.button(text="⏳ Время", callback_data="coupon_type_days")
    kb.button(text="📉 Процент", callback_data="coupon_type_percent")
    kb.button(text=BACK, callback_data=AdminPanelCallback(action="coupons").pack())
    kb.adjust(1)

    await callback_query.message.edit_text(text=text, reply_markup=kb.as_markup())
    await state.set_state(AdminCouponsState.waiting_for_coupon_type)


async def show_coupon_audience_step(callback_query: CallbackQuery, state: FSMContext):
    text = menu_text(
        "Аудитория купона",
        "Кому он доступен?",
        quote("Всем — любому клиенту.\nТолько новым — тем, у кого ещё не было подписки."),
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="👤 Всем", callback_data="coupon_audience_all")
    kb.button(text="🆕 Только новым", callback_data="coupon_audience_new")
    kb.button(text=BACK, callback_data=AdminPanelCallback(action="coupons").pack())
    kb.adjust(1)

    await callback_query.message.edit_text(text=text, reply_markup=kb.as_markup())
    await state.set_state(AdminCouponsState.waiting_for_coupon_audience)


@router.callback_query(F.data == "coupon_type_balance", IsAdminFilter())
async def handle_balance_coupon_selection(callback_query: CallbackQuery, state: FSMContext):
    await state.update_data(coupon_type="balance")
    await show_coupon_audience_step(callback_query, state)


@router.callback_query(F.data == "coupon_type_days", IsAdminFilter())
async def handle_days_coupon_selection(callback_query: CallbackQuery, state: FSMContext):
    await state.update_data(coupon_type="days", new_users_only=False)

    kb = InlineKeyboardBuilder()
    kb.button(text=BACK, callback_data=AdminPanelCallback(action="coupons").pack())
    kb.adjust(1)

    text = menu_text(
        "Купон на дни",
        "Пришлите данные одной строкой.",
        quote("Формат: код, дни, лимит.\nНапример: <code>DAYS10 10 50</code>"),
    )
    await callback_query.message.edit_text(text=text, reply_markup=kb.as_markup())
    await state.set_state(AdminCouponsState.waiting_for_days_data)


@router.callback_query(F.data == "coupon_type_percent", IsAdminFilter())
async def handle_percent_coupon_selection(callback_query: CallbackQuery, state: FSMContext):
    await state.update_data(coupon_type="percent", new_users_only=False)

    kb = InlineKeyboardBuilder()
    kb.button(text=BACK, callback_data=AdminPanelCallback(action="coupons").pack())
    kb.adjust(1)

    text = menu_text(
        "Купон на скидку",
        "Пришлите данные одной строкой.",
        quote("Формат: код, процент, лимит.\nНапример: <code>SALE20 20 10</code> — скидка 20%."),
    )
    await callback_query.message.edit_text(text=text, reply_markup=kb.as_markup())
    await state.set_state(AdminCouponsState.waiting_for_percent_data)


@router.callback_query(
    F.data.in_(("coupon_audience_all", "coupon_audience_new")), IsAdminFilter(), flags={"popup": True}
)
async def handle_coupon_audience(callback_query: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    coupon_type = data.get("coupon_type")
    if coupon_type != "balance":
        await callback_query.answer("Режим доступен только купонам на баланс", show_alert=True)
        return

    await state.update_data(new_users_only=callback_query.data == "coupon_audience_new")

    kb = InlineKeyboardBuilder()
    kb.button(text=BACK, callback_data=AdminPanelCallback(action="coupons").pack())
    kb.adjust(1)

    text = menu_text(
        "Купон на баланс",
        "Пришлите данные одной строкой.",
        quote("Формат: код, сумма, лимит.\nНапример: <code>COUPON1 50 5</code>"),
    )
    await callback_query.message.edit_text(text=text, reply_markup=kb.as_markup())
    await state.set_state(AdminCouponsState.waiting_for_balance_data)


@router.message(AdminCouponsState.waiting_for_balance_data, IsAdminFilter())
async def handle_balance_coupon_input(message: Message, state: FSMContext, session: Any):
    text = message.text.strip()
    parts = text.split()

    kb = InlineKeyboardBuilder()
    kb.button(text=BACK, callback_data=AdminPanelCallback(action="coupons").pack())
    kb.adjust(1)

    if len(parts) != 3:
        text = menu_text(
            "Некорректный формат",
            "Нужны три значения через пробел.",
            quote("Код, сумма, лимит.\nНапример: <code>COUPON1 50 5</code>"),
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
        text = menu_text("Купоны", "❌ Сумма и лимит — целые числа больше нуля.")
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
            await message.answer(
                menu_text("Купоны", "❌ Купон с таким кодом уже существует.", markup=kb.as_markup()),
                reply_markup=kb.as_markup(),
            )
            return

        coupon_link = f"https://telegram.me/{USERNAME_BOT}?start=coupons_{coupon_code}"
        audience_txt = "только новым" if new_users_only else "всем"

        text = menu_text(
            "Купоны",
            f"✅ Купон <b>{coupon_code}</b> создан.",
            quote(f"Сумма: {coupon_amount} ₽\nЛимит: {usage_limit} раз\nДоступ: {audience_txt}"),
            quote(f"<code>{coupon_link}</code>"),
        )

        kb = InlineKeyboardBuilder()
        if MODES_CONFIG.get("INLINE_MODE_ENABLED", INLINE_MODE):
            kb.button(text="📤 Поделиться", switch_inline_query=f"coupon_{coupon_code}")
        kb.button(text=BACK, callback_data=AdminPanelCallback(action="coupons").pack())
        kb.adjust(1)

        await message.answer(text=text, reply_markup=kb.as_markup())
        await state.clear()
    except Exception as e:
        logger.error(f"Ошибка при создании купона: {e}")
        await message.answer(
            menu_text("Купоны", "❌ Не удалось создать купон.", markup=kb.as_markup()),
            reply_markup=kb.as_markup(),
        )


@router.message(AdminCouponsState.waiting_for_days_data, IsAdminFilter())
async def handle_days_coupon_input(message: Message, state: FSMContext, session: Any):
    text = message.text.strip()
    parts = text.split()

    kb = InlineKeyboardBuilder()
    kb.button(text=BACK, callback_data=AdminPanelCallback(action="coupons").pack())
    kb.adjust(1)

    if len(parts) != 3:
        text = menu_text(
            "Купоны",
            "❌ Нужны три значения через пробел.",
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
        text = menu_text("Купоны", "❌ Дни и лимит — целые числа больше нуля.")
        await message.answer(text=text, reply_markup=kb.as_markup())
        return

    try:
        ok = await create_coupon(
            session,
            coupon_code,
            0,
            usage_limit,
            days=days,
            new_users_only=False,
            percent=None,
        )
        if not ok:
            await message.answer(
                menu_text("Купоны", "❌ Купон с таким кодом уже существует.", markup=kb.as_markup()),
                reply_markup=kb.as_markup(),
            )
            return

        coupon_link = f"https://telegram.me/{USERNAME_BOT}?start=coupons_{coupon_code}"

        text = menu_text(
            "Купоны",
            f"✅ Купон <b>{coupon_code}</b> создан.",
            quote(f"Даёт: {format_days(days)}\nЛимит: {usage_limit} раз"),
            quote(f"<code>{coupon_link}</code>"),
        )

        kb = InlineKeyboardBuilder()
        if MODES_CONFIG.get("INLINE_MODE_ENABLED", INLINE_MODE):
            kb.button(text="📤 Поделиться", switch_inline_query=f"coupon_{coupon_code}")
        kb.button(text=BACK, callback_data=AdminPanelCallback(action="coupons").pack())
        kb.adjust(1)

        await message.answer(text=text, reply_markup=kb.as_markup())
        await state.clear()
    except Exception as e:
        logger.error(f"Ошибка при создании купона: {e}")
        await message.answer(
            menu_text("Купоны", "❌ Не удалось создать купон.", markup=kb.as_markup()),
            reply_markup=kb.as_markup(),
        )


@router.message(AdminCouponsState.waiting_for_percent_data, IsAdminFilter())
async def handle_percent_coupon_input(message: Message, state: FSMContext, session: Any):
    text = message.text.strip()
    parts = text.split()

    kb = InlineKeyboardBuilder()
    kb.button(text=BACK, callback_data=AdminPanelCallback(action="coupons").pack())
    kb.adjust(1)

    if len(parts) != 3:
        text = menu_text(
            "Купоны",
            "❌ Нужны три значения через пробел.",
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
        text = menu_text("Купоны", "❌ Процент от 1 до 100, лимит — число больше нуля.")
        await message.answer(text=text, reply_markup=kb.as_markup())
        return

    try:
        ok = await create_coupon(
            session,
            coupon_code,
            0,
            usage_limit,
            days=None,
            new_users_only=False,
            percent=percent,
        )
        if not ok:
            await message.answer(
                menu_text("Купоны", "❌ Купон с таким кодом уже существует.", markup=kb.as_markup()),
                reply_markup=kb.as_markup(),
            )
            return

        text = menu_text(
            "Купоны",
            f"✅ Купон <b>{coupon_code}</b> создан.",
            quote(f"Скидка: {percent}%\nЛимит: {usage_limit} раз"),
        )

        kb = InlineKeyboardBuilder()
        kb.button(text=BACK, callback_data=AdminPanelCallback(action="coupons").pack())
        kb.adjust(1)

        await message.answer(text=text, reply_markup=kb.as_markup())
        await state.clear()
    except Exception as e:
        logger.error(f"Ошибка при создании купона: {e}")
        await message.answer(
            menu_text("Купоны", "❌ Не удалось создать купон.", markup=kb.as_markup()),
            reply_markup=kb.as_markup(),
        )


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
        await callback_query.message.edit_text(menu_text("Купоны", "Не удалось получить список купонов."))


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
        menu_text("Купоны", f"Удалить купон <b>{coupon_code}</b>?", markup=kb.as_markup()),
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
                    menu_text(
                        "Купоны", f"❌ Купон с кодом {coupon_code} не найден.", markup=build_admin_back_kb("coupons")
                    ),
                    reply_markup=build_admin_back_kb("coupons"),
                )
                return
        except Exception as e:
            logger.error(f"Ошибка при удалении купона: {e}")
            await callback_query.message.edit_text(
                menu_text("Купоны", "Не удалось удалить купон.", markup=build_admin_back_kb("coupons")),
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
            text=menu_text("Купоны", "Купонов пока нет.", markup=build_admin_back_kb("coupons")),
            reply_markup=build_admin_back_kb("coupons"),
        )
        return

    kb = build_coupons_list_kb(coupons, result["current_page"], result["pages"])
    body = format_coupons_list(coupons, USERNAME_BOT)
    await message.edit_text(
        text=menu_text("Купоны", f"На странице: <b>{len(coupons)}</b>", body, markup=kb), reply_markup=kb
    )


@router.inline_query(F.query.startswith("coupon_"))
async def inline_coupon_handler(inline_query: InlineQuery, session: Any):
    if not MODES_CONFIG.get("INLINE_MODE_ENABLED", INLINE_MODE):
        return

    coupon_code = inline_query.query.split("coupon_")[1]

    coupons = await get_all_coupons(session, page=1, per_page=10)
    coupon = next((c for c in coupons["coupons"] if c["code"] == coupon_code), None)

    if not coupon:
        await safe_answer_inline_query(
            inline_query,
            results=[],
            switch_pm_text="Купон не найден",
            switch_pm_parameter="coupons",
            cache_time=1,
        )
        return

    percent_value = coupon.get("percent")
    if percent_value is not None and int(percent_value) > 0:
        await safe_answer_inline_query(
            inline_query,
            results=[],
            switch_pm_text="Процентные купоны не публикуются ссылкой",
            switch_pm_parameter="coupons",
            cache_time=1,
        )
        return

    coupon_link = f"https://telegram.me/{USERNAME_BOT}?start=coupons_{coupon_code}"
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

    await safe_answer_inline_query(inline_query, results=[result], cache_time=86400, is_personal=True)

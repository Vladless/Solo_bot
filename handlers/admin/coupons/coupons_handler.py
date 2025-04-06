from datetime import datetime
import html
import pytz
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
from database import (
    add_connection,
    check_connection_exists,
    create_coupon,
    create_coupon_usage,
    delete_coupon,
    get_all_coupons,
    get_keys,
    update_key_expiry,
)
from filters.admin import IsAdminFilter
from handlers.buttons import BACK
from handlers.keys.key_utils import renew_key_in_cluster
from handlers.profile import process_callback_view_profile
from handlers.utils import format_days
from logger import logger

from ..panel.keyboard import AdminPanelCallback, build_admin_back_kb
from .keyboard import AdminCouponDeleteCallback, build_coupons_kb, build_coupons_list_kb, format_coupons_list


router = Router()


class AdminCouponsState(StatesGroup):
    waiting_for_coupon_type = State()
    waiting_for_balance_data = State()
    waiting_for_days_data = State()
    waiting_for_key_selection = State()


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
    kb.button(text=BACK, callback_data=AdminPanelCallback(action="coupons").pack())
    kb.adjust(1)

    await callback_query.message.edit_text(text=text, reply_markup=kb.as_markup())
    await state.set_state(AdminCouponsState.waiting_for_coupon_type)


@router.callback_query(F.data == "coupon_type_balance")
async def handle_balance_coupon_selection(callback_query: CallbackQuery, state: FSMContext):
    text = (
        "🎫 <b>Введите данные для создания купона в формате:</b>\n\n"
        "📝 <i>код</i> 💰 <i>сумма</i> 🔢 <i>лимит</i>\n\n"
        "Пример: <b>'COUPON1 50 5'</b> 👈\n\n"
    )
    kb = InlineKeyboardBuilder()
    kb.button(text=BACK, callback_data=AdminPanelCallback(action="coupons").pack())

    await callback_query.message.edit_text(text=text, reply_markup=kb.as_markup())
    await state.set_state(AdminCouponsState.waiting_for_balance_data)


@router.callback_query(F.data == "coupon_type_days")
async def handle_days_coupon_selection(callback_query: CallbackQuery, state: FSMContext):
    text = (
        "🎫 <b>Введите данные для создания купона в формате:</b>\n\n"
        "📝 <i>код</i> ⏳ <i>дни</i> 🔢 <i>лимит</i>\n\n"
        "Пример: <b>'DAYS10 10 50'</b> 👈\n\n"
    )
    kb = InlineKeyboardBuilder()
    kb.button(text=BACK, callback_data=AdminPanelCallback(action="coupons").pack())

    await callback_query.message.edit_text(text=text, reply_markup=kb.as_markup())
    await state.set_state(AdminCouponsState.waiting_for_days_data)


@router.message(AdminCouponsState.waiting_for_balance_data, IsAdminFilter())
async def handle_balance_coupon_input(message: Message, state: FSMContext, session: Any):
    text = message.text.strip()
    parts = text.split()

    kb = InlineKeyboardBuilder()
    kb.button(text=BACK, callback_data=AdminPanelCallback(action="coupons").pack())

    if len(parts) != 3:
        text = (
            "❌ <b>Некорректный формат!</b> 📝 Пожалуйста, введите данные в формате:\n"
            "🏷️ <b>код</b> 💰 <b>сумма</b> 🔢 <b>лимит</b>\n"
            "Пример: <b>'COUPON1 50 5'</b> 👈"
        )
        await message.answer(text=text, reply_markup=kb.as_markup())
        return

    try:
        coupon_code = parts[0]
        coupon_amount = int(parts[1])
        usage_limit = int(parts[2])
        if coupon_amount <= 0:
            raise ValueError("Сумма должна быть больше 0")
    except ValueError:
        text = "⚠️ <b>Проверьте правильность введенных данных!</b>\n💱 Сумма должна быть числом, а лимит — целым числом."
        await message.answer(text=text, reply_markup=kb.as_markup())
        return

    try:
        await create_coupon(coupon_code, coupon_amount, usage_limit, session, days=None)

        coupon_link = f"https://t.me/{USERNAME_BOT}?start=coupons_{coupon_code}"
        text = (
            f"✅ Купон с кодом <b>{coupon_code}</b> успешно создан!\n"
            f"💰 Сумма: <b>{coupon_amount} рублей</b>\n"
            f"🔢 Лимит использования: <b>{usage_limit} раз</b>\n"
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

    if len(parts) != 3:
        text = (
            "❌ <b>Некорректный формат!</b> 📝 Пожалуйста, введите данные в формате:\n"
            "🏷️ <b>код</b> ⏳ <i>дни</i> 🔢 <b>лимит</b>\n"
            "Пример: <b>'DAYS10 10 50'</b> 👈"
        )
        await message.answer(text=text, reply_markup=kb.as_markup())
        return

    try:
        coupon_code = parts[0]
        days = int(parts[1])
        usage_limit = int(parts[2])
        if days <= 0:
            raise ValueError("Количество дней должно быть больше 0")
    except ValueError:
        text = "⚠️ <b>Проверьте правильность введенных данных!</b>\n💱 Дни должны быть числом, а лимит — целым числом."
        await message.answer(text=text, reply_markup=kb.as_markup())
        return

    try:
        await create_coupon(coupon_code, 0, usage_limit, session, days=days)

        coupon_link = f"https://t.me/{USERNAME_BOT}?start=coupons_{coupon_code}"
        text = (
            f"✅ Купон с кодом <b>{coupon_code}</b> успешно создан!\n"
            f"⏳ <b>{format_days(days)}</b>\n"
            f"🔢 Лимит использования: <b>{usage_limit} раз</b>\n"
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
async def handle_coupon_delete(callback_query: CallbackQuery, callback_data: AdminCouponDeleteCallback, session: Any):
    coupon_code = callback_data.coupon_code
    kb = InlineKeyboardBuilder()
    kb.button(
        text="✅ Да, удалить",
        callback_data=AdminCouponDeleteCallback(coupon_code=coupon_code, confirm=True).pack()
    )
    kb.button(
        text="❌ Нет, отменить",
        callback_data=AdminCouponDeleteCallback(coupon_code=coupon_code, confirm=False).pack()
    )
    kb.adjust(1)

    await callback_query.message.edit_text(
        f"Вы уверены, что хотите удалить купон <b>{coupon_code}</b>?",
        reply_markup=kb.as_markup()
    )


@router.callback_query(AdminCouponDeleteCallback.filter(F.confirm.is_not(None)), IsAdminFilter())
async def confirm_coupon_delete(callback_query: CallbackQuery, callback_data: AdminCouponDeleteCallback, session: Any):
    coupon_code = callback_data.coupon_code
    confirm = callback_data.confirm

    if confirm:
        try:
            result = await delete_coupon(coupon_code, session)
            if not result:
                await callback_query.message.edit_text(
                    f"❌ Купон с кодом {coupon_code} не найден.",
                    reply_markup=build_admin_back_kb("coupons")
                )
                return
        except Exception as e:
            logger.error(f"Ошибка при удалении купона: {e}")
            await callback_query.message.edit_text(
                "Произошла ошибка при удалении купона.",
                reply_markup=build_admin_back_kb("coupons")
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


@router.inline_query(F.query.startswith("coupon_"))
async def inline_coupon_handler(inline_query: InlineQuery, session: Any):
    if not INLINE_MODE:
        return

    coupon_code = inline_query.query.split("coupon_")[1]
    coupon_link = f"https://t.me/{USERNAME_BOT}?start=coupons_{coupon_code}"

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

    title = f"Купон {coupon['code']}"
    description = f"Получи {coupon['amount']} рублей!" if coupon["amount"] > 0 else f"Продли подписку на {format_days(coupon['days'])}!"
    message_text = (
        f"🎫 <b>Купон:</b> {coupon['code']}\n"
        f"{'💰 <b>Бонус:</b> ' + str(coupon['amount']) + ' рублей' if coupon['amount'] > 0 else '⏳ <b>Продление:</b> ' + format_days(coupon['days'])}\n"
        f"👇 Нажми, чтобы активировать!"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="Активировать купон", url=coupon_link)

    result = InlineQueryResultArticle(
        id=coupon_code,
        title=title,
        description=description,
        input_message_content=InputTextMessageContent(
            message_text=message_text,
            parse_mode=ParseMode.HTML
        ),
        reply_markup=builder.as_markup(),
    )

    await inline_query.answer(
        results=[result],
        cache_time=86400,
        is_personal=True
    )

@router.message(F.text.regexp(r"^/start coupons_(.+)$"))
async def handle_coupon_activation(
    message: Message, state: FSMContext, session: Any, admin: bool = False, text: str = None, user_id: int = None
):
    coupon_text = text if text is not None else message.text
    logger.info(f"Текст купона в handle_coupon_activation: {coupon_text}")
    coupon_code = coupon_text.split("coupons_")[1]

    coupons = await get_all_coupons(session, page=1, per_page=10)
    coupon = next((c for c in coupons["coupons"] if c["code"] == coupon_code), None)

    if not coupon:
        await message.answer("❌ Купон не найден.")
        return

    if coupon["usage_count"] >= coupon["usage_limit"] or coupon["is_used"]:
        await message.answer("❌ Лимит активаций купона исчерпан.")
        return

    effective_user_id = user_id if user_id is not None else message.from_user.id

    usage = await session.fetchrow(
        "SELECT * FROM coupon_usages WHERE coupon_id = $1 AND user_id = $2",
        coupon["id"],
        effective_user_id
    )
    if usage:
        await message.answer("❌ Вы уже активировали этот купон.")
        return

    connection_exists = await check_connection_exists(effective_user_id)
    if not connection_exists:
        await add_connection(tg_id=effective_user_id, session=session)

    if coupon["amount"] > 0:
        await session.execute(
            "UPDATE connections SET balance = balance + $1 WHERE tg_id = $2",
            coupon["amount"],
            effective_user_id
        )
        await session.execute(
            "UPDATE coupons SET usage_count = usage_count + 1, is_used = $1 WHERE id = $2",
            coupon["usage_count"] + 1 >= coupon["usage_limit"],
            coupon["id"]
        )
        await create_coupon_usage(coupon["id"], effective_user_id, session)
        await message.answer(f"✅ Купон активирован, на баланс начислено {coupon['amount']} рублей.")
        await process_callback_view_profile(message, state, admin)
        return

    if coupon["days"] is not None and coupon["days"] > 0:
        keys = await get_keys(effective_user_id, session)
        active_keys = [k for k in keys if not k["is_frozen"]]

        if not active_keys:
            await message.answer("❌ У вас нет активных подписок для продления.")
            return

        builder = InlineKeyboardBuilder()
        moscow_tz = pytz.timezone("Europe/Moscow")
        response_message = "<b>🔑 Выберите подписку для продления:</b>\n\n<blockquote>"

        for key in active_keys:
            alias = key.get("alias")
            email = key["email"]
            client_id = key["client_id"]
            expiry_time = key.get("expiry_time")

            key_display = html.escape(alias.strip() if alias else email)
            expiry_date = datetime.fromtimestamp(expiry_time / 1000, tz=moscow_tz).strftime("до %d.%m.%y, %H:%M")
            response_message += f"• <b>{key_display}</b> ({expiry_date})\n"
            builder.button(text=key_display, callback_data=f"extend_key|{client_id}|{coupon['id']}")

        response_message += "</blockquote>"
        builder.button(text="Отмена", callback_data="cancel_coupon_activation")
        builder.adjust(1)

        await message.answer(response_message, reply_markup=builder.as_markup())
        await state.set_state(AdminCouponsState.waiting_for_key_selection)
        await state.update_data(coupon_id=coupon["id"], user_id=effective_user_id)
        return

    await message.answer("❌ Купон недействителен (нет суммы или дней).")


@router.callback_query(F.data.startswith("extend_key|"))
async def handle_key_extension(callback_query: CallbackQuery, state: FSMContext, session: Any, admin: bool = False):
    parts = callback_query.data.split("|")
    client_id = parts[1]
    coupon_id = int(parts[2])

    coupon = await session.fetchrow("SELECT * FROM coupons WHERE id = $1", coupon_id)
    if not coupon or coupon["usage_count"] >= coupon["usage_limit"]:
        await callback_query.message.edit_text("❌ Купон недействителен или лимит исчерпан.")
        await state.clear()
        return

    usage = await session.fetchrow(
        "SELECT * FROM coupon_usages WHERE coupon_id = $1 AND user_id = $2",
        coupon_id,
        callback_query.from_user.id
    )
    if usage:
        await callback_query.message.edit_text("❌ Вы уже активировали этот купон.")
        await state.clear()
        return

    key = await session.fetchrow(
        "SELECT * FROM keys WHERE tg_id = $1 AND client_id = $2",
        callback_query.from_user.id,
        client_id
    )
    if not key or key["is_frozen"]:
        await callback_query.message.edit_text("❌ Выбранная подписка не найдена или заморожена.")
        await state.clear()
        return

    now_ms = int(datetime.now().timestamp() * 1000)
    current_expiry = key["expiry_time"]
    new_expiry = max(now_ms, current_expiry) + (coupon["days"] * 86400 * 1000)

    try:
        await renew_key_in_cluster(
            cluster_id=key["server_id"],
            email=key["email"],
            client_id=client_id,
            new_expiry_time=new_expiry,
            total_gb=0
        )
        await update_key_expiry(client_id, new_expiry, session)

        await session.execute(
            "UPDATE coupons SET usage_count = usage_count + 1, is_used = $1 WHERE id = $2",
            coupon["usage_count"] + 1 >= coupon["usage_limit"],
            coupon["id"]
        )
        await create_coupon_usage(coupon["id"], callback_query.from_user.id, session)

        alias = key.get("alias") or key["email"]
        expiry_date = datetime.fromtimestamp(new_expiry / 1000, tz=pytz.timezone("Europe/Moscow")).strftime("%d.%m.%y, %H:%M")
        text = f"✅ Купон активирован, подписка <b>{alias}</b> продлена на {format_days(coupon['days'])}⏳ до {expiry_date}📆."

        await callback_query.message.answer(text)
        await process_callback_view_profile(callback_query.message, state, admin)
        await state.clear()

    except Exception as e:
        logger.error(f"Ошибка при продлении ключа: {e}")
        await callback_query.message.edit_text("❌ Произошла ошибка при продлении подписки.")
        await state.clear()


@router.callback_query(F.data == "cancel_coupon_activation")
async def cancel_coupon_activation(callback_query: CallbackQuery, state: FSMContext, admin: bool = False):
    await callback_query.message.answer("⚠️ Активация купона отменена.")
    await process_callback_view_profile(callback_query.message, state, admin)
    await state.clear()

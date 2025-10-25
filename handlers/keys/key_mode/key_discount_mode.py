from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import DISCOUNT_ACTIVE_HOURS
from database import get_keys, get_tariffs
from database.models import Notification
from handlers.buttons import MAIN_MENU, RENEW_KEY_NOTIFICATION
from handlers.notifications.notify_kb import build_tariffs_keyboard
from handlers.texts import DISCOUNT_TARIFF, DISCOUNT_TARIFF_MAX
from handlers.utils import format_discount_time_left
from logger import logger

from .key_create import select_tariff_plan


router = Router()


@router.callback_query(F.data == "hot_lead_discount")
async def handle_discount_entry(callback: CallbackQuery, session: AsyncSession):
    tg_id = callback.from_user.id

    result = await session.execute(
        select(Notification.last_notification_time).where(
            Notification.tg_id == tg_id,
            Notification.notification_type == "hot_lead_step_2",
        )
    )
    last_time = result.scalar_one_or_none()

    if not last_time:
        await callback.message.edit_text("❌ Скидка недоступна.")
        return

    now = datetime.utcnow()
    if now - last_time > timedelta(hours=DISCOUNT_ACTIVE_HOURS):
        await callback.message.edit_text("⏳ Срок действия скидки истёк.")
        return

    keys = await get_keys(session, tg_id)

    if keys and len(keys) > 0:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=RENEW_KEY_NOTIFICATION, callback_data=f"renew_key|{keys[0].email}"))
        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

        expires_at = last_time + timedelta(hours=DISCOUNT_ACTIVE_HOURS)
        await callback.message.edit_text(
            f"🎯 <b>ЭКСКЛЮЗИВНОЕ ПРЕДЛОЖЕНИЕ!</b>\n\n<blockquote>"
            f"💎 <b>Специальные тарифы</b> — доступные только для вас!\n"
            f"🚀 <b>Получите максимум возможностей</b> по выгодной цене!\n"
            f"</blockquote>\n"
            f"⏰ <b>Предложение действует всего: {format_discount_time_left(expires_at, DISCOUNT_ACTIVE_HOURS)} — не упустите свой шанс!</b>",
            reply_markup=builder.as_markup(),
        )
    else:
        tariffs = await get_tariffs(session=session, group_code="discounts")
        if not tariffs:
            await callback.message.edit_text("❌ Скидочные тарифы временно недоступны.")
            return

        await callback.message.edit_text(
            DISCOUNT_TARIFF,
            reply_markup=build_tariffs_keyboard(tariffs, prefix="discount_tariff"),
        )


@router.callback_query(F.data.startswith("discount_tariff|"))
async def handle_discount_tariff_selection(callback: CallbackQuery, session, state):
    try:
        tariff_id = int(callback.data.split("|")[1])
        fake_callback = CallbackQuery.model_construct(
            id=callback.id,
            from_user=callback.from_user,
            chat_instance=callback.chat_instance,
            message=callback.message,
            data=f"select_tariff_plan|{tariff_id}",
        )
        await select_tariff_plan(fake_callback, session=session, state=state)

    except Exception as e:
        logger.error(f"Ошибка при выборе скидочного тарифа: {e}")
        await callback.message.answer("❌ Произошла ошибка при выборе тарифа.")


@router.callback_query(F.data == "hot_lead_final_discount")
async def handle_ultra_discount(callback: CallbackQuery, session: AsyncSession):
    tg_id = callback.from_user.id

    result = await session.execute(
        select(Notification.last_notification_time).where(
            Notification.tg_id == tg_id,
            Notification.notification_type == "hot_lead_step_3",
        )
    )
    last_time = result.scalar_one_or_none()

    if not last_time:
        await callback.message.edit_text("❌ Скидка недоступна.")
        return

    now = datetime.utcnow()
    if now - last_time > timedelta(hours=DISCOUNT_ACTIVE_HOURS):
        await callback.message.edit_text("⏳ Срок действия финальной скидки истёк.")
        return

    keys = await get_keys(session, tg_id)

    if keys and len(keys) > 0:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=RENEW_KEY_NOTIFICATION, callback_data=f"renew_key|{keys[0].email}"))
        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

        await callback.message.edit_text(
            f"🎯 <b>УНИКАЛЬНОЕ ФИНАЛЬНОЕ ПРЕДЛОЖЕНИЕ!</b>\n\n<blockquote>"
            f"💎 <b>Доступ к тарифам с МАКСИМАЛЬНОЙ выгодой</b> — только для вас!\n"
            f"🚀 <b>Уникальные условия</b> — получите максимум преимуществ по минимальной цене!\n"
            f"</blockquote>\n"
            f"⏰ <b>Время ограничено: {format_discount_time_left(last_time, DISCOUNT_ACTIVE_HOURS)} — не упустите шанс!</b>",
            reply_markup=builder.as_markup(),
        )
    else:
        tariffs = await get_tariffs(session, group_code="discounts_max")
        if not tariffs:
            await callback.message.edit_text("❌ Скидочные тарифы временно недоступны.")
            return

        await callback.message.edit_text(
            DISCOUNT_TARIFF_MAX,
            reply_markup=build_tariffs_keyboard(tariffs, prefix="discount_tariff"),
        )

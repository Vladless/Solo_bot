from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import DISCOUNT_ACTIVE_HOURS
from database import get_tariffs
from database.models import Notification
from handlers.notifications.notify_kb import build_tariffs_keyboard
from handlers.texts import DISCOUNT_TARIFF, DISCOUNT_TARIFF_MAX
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

    tariffs = await get_tariffs(session, group_code="discounts_max")
    if not tariffs:
        await callback.message.edit_text("❌ Скидочные тарифы временно недоступны.")
        return

    await callback.message.edit_text(
        DISCOUNT_TARIFF_MAX,
        reply_markup=build_tariffs_keyboard(tariffs, prefix="discount_tariff"),
    )

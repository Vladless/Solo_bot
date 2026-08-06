import re

from datetime import datetime

import pytz

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.redis_cache import cache_delete, cache_key, cache_set
from database import create_tracking_source, get_tracking_source_stats
from database.models import TrackingSource, User
from filters.admin import HasPermission, IsAdminFilter
from filters.permissions import PERM_ADS
from logger import logger
from settings.cache_config import START_UTM_EXISTS_TTL_SEC
from settings.config import USERNAME_BOT

from ..panel.headers import card, menu_text, quote, section, wrap_text
from ..panel.keyboard import AdminPanelCallback
from .keyboard import (
    AdminAdsCallback,
    build_ads_delete_confirm_kb,
    build_ads_kb,
    build_ads_list_kb,
    build_ads_stats_kb,
    build_cancel_input_kb,
)


router = Router()
router.callback_query.filter(HasPermission(PERM_ADS))
router.message.filter(HasPermission(PERM_ADS))


class AdminAdsState(StatesGroup):
    waiting_for_new_name = State()
    waiting_for_new_code = State()


def _ads_menu_text() -> str:
    return menu_text(
        "Аналитика рекламы",
        "UTM-ссылки и что они принесли.",
        quote("По каждой ссылке видно переходы, регистрации и оплаты."),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "ads"), IsAdminFilter())
async def handle_ads_menu(callback_query: CallbackQuery):
    await callback_query.message.edit_text(text=wrap_text(_ads_menu_text()), reply_markup=build_ads_kb())


@router.callback_query(AdminAdsCallback.filter(F.action == "create"), IsAdminFilter())
async def handle_ads_create(callback_query: CallbackQuery, state: FSMContext):
    await state.set_state(AdminAdsState.waiting_for_new_name)
    await callback_query.message.edit_text(
        menu_text("Аналитика рекламы", "📝 Введите <b>название</b> новой ссылки:", markup=build_cancel_input_kb()),
        reply_markup=build_cancel_input_kb(),
    )


@router.message(AdminAdsState.waiting_for_new_name, IsAdminFilter())
async def handle_ads_name_input(message: Message, state: FSMContext):
    name = message.text.strip()
    await state.update_data(name=name)
    await state.set_state(AdminAdsState.waiting_for_new_code)
    await message.answer(
        menu_text(
            "Аналитика рекламы",
            f"Придумайте код ссылки для «{name}».",
            quote("Только латинские буквы и цифры."),
            markup=build_cancel_input_kb(),
        ),
        reply_markup=build_cancel_input_kb(),
    )


@router.message(AdminAdsState.waiting_for_new_code, IsAdminFilter())
async def handle_ads_code_input(message: Message, state: FSMContext, session: AsyncSession):
    code = message.text.strip()
    data = await state.get_data()
    name = data["name"]

    if not re.match(r"^[a-zA-Z0-9]+$", code):
        await message.answer(
            menu_text(
                "Аналитика рекламы",
                "❌ В коде допустимы только латинские буквы и цифры.",
                quote("Введите код заново."),
                markup=build_cancel_input_kb(),
            ),
            reply_markup=build_cancel_input_kb(),
        )
        return

    code_with_prefix = f"utm_{code}"

    try:
        await create_tracking_source(
            name=name,
            code=code_with_prefix,
            type_="utm",
            created_by=message.from_user.id,
            session=session,
        )
        await cache_set(cache_key("utm_exists", code_with_prefix), True, START_UTM_EXISTS_TTL_SEC)
        stats = await get_tracking_source_stats(session, code_with_prefix)
        if not stats:
            await message.answer(menu_text("Аналитика рекламы", "❌ Источник не найден или не содержит данных."))
            return
        msg = format_ads_stats(stats, USERNAME_BOT)
        await message.answer(
            text=menu_text("Аналитика рекламы", msg, markup=build_ads_stats_kb(code_with_prefix)),
            reply_markup=build_ads_stats_kb(code_with_prefix),
        )

    except Exception as e:
        logger.error(f"Ошибка при создании ссылки: {e}", exc_info=True)
        await message.answer(menu_text("Аналитика рекламы", "❌ Не удалось создать ссылку."))
    finally:
        await state.clear()


@router.callback_query(AdminAdsCallback.filter(F.action == "list"), IsAdminFilter())
async def handle_ads_list(callback_query: CallbackQuery, session: AsyncSession, callback_data: AdminAdsCallback):
    try:
        result = await session.execute(select(TrackingSource).order_by(TrackingSource.created_at.desc()))
        ads = result.scalars().all()
        items_per_page = 6
        if callback_data.code and callback_data.code.isdigit():
            current_page = int(callback_data.code)
        else:
            current_page = 1
        total_pages = (len(ads) + items_per_page - 1) // items_per_page
        reply_markup = build_ads_list_kb(ads, current_page, total_pages)
        await callback_query.message.edit_text(
            menu_text("Аналитика рекламы", "📋 Выберите ссылку для просмотра статистики:", markup=reply_markup),
            reply_markup=reply_markup,
        )
    except Exception as e:
        logger.error(f"Ошибка при получении списка UTM: {e}", exc_info=True)
        await callback_query.message.edit_text(menu_text("Аналитика рекламы", "❌ Не удалось получить список."))


@router.callback_query(AdminAdsCallback.filter(F.action == "view"), IsAdminFilter())
async def handle_ads_view(
    callback_query: CallbackQuery,
    callback_data: AdminAdsCallback,
    session: AsyncSession,
):
    code = callback_data.code
    try:
        stats = await get_tracking_source_stats(session, code)
        if not stats:
            await callback_query.message.edit_text(
                menu_text("Аналитика рекламы", "❌ Источник не найден или не содержит данных.")
            )
            return
        msg = format_ads_stats(stats, USERNAME_BOT)
        await callback_query.message.edit_text(
            text=menu_text("Аналитика рекламы", msg, markup=build_ads_stats_kb(code)),
            reply_markup=build_ads_stats_kb(code),
        )
    except Exception as e:
        logger.error(f"Ошибка при просмотре статистики: {e}", exc_info=True)
        await callback_query.message.edit_text(menu_text("Аналитика рекламы", "❌ Ошибка при получении статистики."))


@router.callback_query(AdminAdsCallback.filter(F.action == "delete_confirm"), IsAdminFilter())
async def handle_ads_delete_confirm(callback_query: CallbackQuery, callback_data: AdminAdsCallback):
    code = callback_data.code
    await callback_query.message.edit_text(
        text=menu_text(
            "Аналитика рекламы",
            f"Удалить ссылку <code>{code}</code>?",
            markup=build_ads_delete_confirm_kb(code),
        ),
        reply_markup=build_ads_delete_confirm_kb(code),
    )


@router.callback_query(AdminAdsCallback.filter(F.action == "delete"), IsAdminFilter())
async def handle_ads_delete(
    callback_query: CallbackQuery,
    callback_data: AdminAdsCallback,
    session: AsyncSession,
):
    code = callback_data.code
    try:
        await session.execute(update(User).where(User.source_code == code).values(source_code=None))
        await session.execute(delete(TrackingSource).where(TrackingSource.code == code))
        await cache_delete(cache_key("utm_exists", code))
        await callback_query.message.edit_text(
            menu_text("Аналитика рекламы", f"🗑️ Ссылка <code>{code}</code> удалена.", markup=build_ads_kb()),
            reply_markup=build_ads_kb(),
        )
    except Exception as e:
        logger.error(f"Ошибка при удалении метки {code}: {e}", exc_info=True)
        await callback_query.message.edit_text(menu_text("Аналитика рекламы", "❌ Не удалось удалить ссылку."))


@router.callback_query(AdminAdsCallback.filter(F.action == "cancel_input"), IsAdminFilter())
async def handle_ads_cancel_input(callback_query: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback_query.message.edit_text(text=wrap_text(_ads_menu_text()), reply_markup=build_ads_kb())


def format_ads_stats(stats: dict, username_bot: str) -> str:
    """Возвращает экран статистики рекламной ссылки."""
    moscow_tz = pytz.timezone("Europe/Moscow")
    update_time = datetime.now(moscow_tz).strftime("%d.%m.%y %H:%M")

    return card(
        section(
            f"📌 {stats['name']}",
            f"Создана: {stats['created_at'].strftime('%d.%m.%y %H:%M')}",
            f"https://telegram.me/{username_bot}?start={stats['code']}",
        ),
        section(
            "💡 Активность",
            f"Регистраций: {stats.get('registrations', 0)}",
            f"Триалов: {stats.get('trials', 0)}",
        ),
        section(
            "💰 Деньги",
            f"Покупок: {stats.get('payments', 0)}",
            f"Сумма: {round(stats.get('total_amount', 0), 2)} ₽",
        ),
        section("⏱ Обновлено", update_time),
    )

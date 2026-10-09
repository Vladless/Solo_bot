from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from core.settings.yookassa_config import YOOKASSA_CONFIG, update_yookassa_config
from filters.admin import IsAdminFilter
from settings.buttons import BACK, YOOKASSA_MARKUP_ENABLED, YOOKASSA_MARKUP_PERCENT
from settings.texts import (
    SETTING_UPDATED,
    YOOKASSA_MARKUP_INVALID,
    YOOKASSA_MARKUP_PERCENT_PROMPT,
    YOOKASSA_SETTINGS_HINT,
    YOOKASSA_SETTINGS_TITLE,
)

from ..panel.headers import menu_text, quote
from ..panel.keyboard import AdminPanelCallback


router = Router(name="admin_settings_yookassa")
router.callback_query.filter(IsAdminFilter())
router.message.filter(IsAdminFilter())


class YookassaMarkupState(StatesGroup):
    waiting_percent = State()


def markup_keyboard() -> InlineKeyboardMarkup:
    """Собирает настройки наценки ЮКассы."""
    enabled = bool(YOOKASSA_CONFIG.get("MARKUP_ENABLED", False))
    percent = escape(str(YOOKASSA_CONFIG.get("MARKUP_PERCENT", "0")))
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{'✅' if enabled else '❌'} {YOOKASSA_MARKUP_ENABLED}",
                    callback_data=AdminPanelCallback(action="settings_yookassa_toggle").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{YOOKASSA_MARKUP_PERCENT}: {percent}",
                    callback_data=AdminPanelCallback(action="settings_yookassa_percent").pack(),
                )
            ],
            [InlineKeyboardButton(text=BACK, callback_data=AdminPanelCallback(action="settings_money").pack())],
        ]
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_yookassa"))
async def open_yookassa_settings(callback: CallbackQuery, state: FSMContext) -> None:
    """Открывает настройки наценки кассы."""
    await state.clear()
    await callback.message.edit_text(
        menu_text(YOOKASSA_SETTINGS_TITLE, quote(YOOKASSA_SETTINGS_HINT)),
        reply_markup=markup_keyboard(),
    )
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_yookassa_toggle"))
async def toggle_yookassa_markup(callback: CallbackQuery, session: AsyncSession) -> None:
    """Переключает наценку для новых платежей."""
    await update_yookassa_config(session, {"MARKUP_ENABLED": not bool(YOOKASSA_CONFIG.get("MARKUP_ENABLED", False))})
    await callback.message.edit_reply_markup(reply_markup=markup_keyboard())
    await callback.answer(SETTING_UPDATED)


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_yookassa_percent"))
async def edit_yookassa_percent(callback: CallbackQuery, state: FSMContext) -> None:
    """Запрашивает новый процент наценки."""
    await state.set_state(YookassaMarkupState.waiting_percent)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=BACK, callback_data=AdminPanelCallback(action="settings_yookassa").pack()),
            ]
        ]
    )
    await callback.message.edit_text(YOOKASSA_MARKUP_PERCENT_PROMPT, reply_markup=keyboard)
    await callback.answer()


@router.message(YookassaMarkupState.waiting_percent)
async def save_yookassa_percent(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Проверяет и сохраняет процент наценки."""
    try:
        await update_yookassa_config(session, {"MARKUP_PERCENT": (message.text or "").strip().replace(",", ".")})
    except ValueError:
        await message.answer(YOOKASSA_MARKUP_INVALID)
        return
    await state.clear()
    await message.answer(
        menu_text(YOOKASSA_SETTINGS_TITLE, SETTING_UPDATED, quote(YOOKASSA_SETTINGS_HINT)),
        reply_markup=markup_keyboard(),
    )

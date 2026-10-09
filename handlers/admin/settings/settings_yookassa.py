from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from core.settings.payments_config import PAYMENTS_CONFIG
from core.settings.yookassa_config import YOOKASSA_CONFIG, update_yookassa_config
from filters.admin import IsAdminFilter, get_admin_context
from settings.buttons import (
    BACK,
    PAYMENT_CASHBOX_TITLES,
    YOOKASSA_AUTOPAY_SETTINGS,
    YOOKASSA_MARKUP_ENABLED,
    YOOKASSA_MARKUP_PERCENT,
)
from settings.texts import (
    SETTING_UPDATED,
    YOOKASSA_MARKUP_INVALID,
    YOOKASSA_MARKUP_PERCENT_PROMPT,
    YOOKASSA_METHODS_HINT,
    YOOKASSA_SETTINGS_HINT,
)

from ..panel.headers import menu_text, quote
from ..panel.keyboard import AdminPanelCallback
from .keyboard import build_settings_cashbox_kb


router = Router(name="admin_settings_yookassa")
router.callback_query.filter(IsAdminFilter())
router.message.filter(IsAdminFilter())


class YookassaMarkupState(StatesGroup):
    waiting_percent = State()


def markup_keyboard(*, show_autopay_settings: bool = True) -> InlineKeyboardMarkup:
    """Собирает способы оплаты и настройки ЮКассы."""
    enabled = bool(YOOKASSA_CONFIG.get("MARKUP_ENABLED", False))
    percent = escape(str(YOOKASSA_CONFIG.get("MARKUP_PERCENT", "0")))
    rows = build_settings_cashbox_kb("YOOKASSA", PAYMENTS_CONFIG).inline_keyboard[:-1]
    rows.extend([
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
    ])
    if show_autopay_settings:
        rows.append([
            InlineKeyboardButton(
                text=YOOKASSA_AUTOPAY_SETTINGS,
                callback_data=AdminPanelCallback(action="settings_yoo_autopay").pack(),
            )
        ])
    rows.append([InlineKeyboardButton(text=BACK, callback_data=AdminPanelCallback(action="settings_cashboxes").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_yookassa_settings(callback: CallbackQuery) -> None:
    """Показывает настройки кассы с учётом прав администратора."""
    _, is_super, _ = await get_admin_context(callback.from_user.id, fresh=True)
    await callback.message.edit_text(
        menu_text(PAYMENT_CASHBOX_TITLES["YOOKASSA"], quote(YOOKASSA_METHODS_HINT), quote(YOOKASSA_SETTINGS_HINT)),
        reply_markup=markup_keyboard(show_autopay_settings=is_super),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_yookassa"))
async def open_yookassa_settings(callback: CallbackQuery, state: FSMContext) -> None:
    """Открывает все настройки ЮКассы."""
    await state.clear()
    await show_yookassa_settings(callback)
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_yookassa_toggle"))
async def toggle_yookassa_markup(callback: CallbackQuery, session: AsyncSession) -> None:
    """Переключает наценку для новых платежей."""
    await update_yookassa_config(session, {"MARKUP_ENABLED": not bool(YOOKASSA_CONFIG.get("MARKUP_ENABLED", False))})
    await show_yookassa_settings(callback)
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
    _, is_super, _ = await get_admin_context(message.from_user.id, fresh=True)
    await message.answer(
        menu_text(
            PAYMENT_CASHBOX_TITLES["YOOKASSA"],
            SETTING_UPDATED,
            quote(YOOKASSA_METHODS_HINT),
            quote(YOOKASSA_SETTINGS_HINT),
        ),
        reply_markup=markup_keyboard(show_autopay_settings=is_super),
    )

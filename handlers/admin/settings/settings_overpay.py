from html import escape
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from core.settings.overpay_autopay_config import (
    DEFAULT_OVERPAY_AUTOPAY_CONFIG,
    OVERPAY_AUTOPAY_CONFIG,
    OVERPAY_AUTOPAY_SETTINGS_INVALID,
    OVERPAY_AUTOPAY_SETTING_KEY,
    update_overpay_autopay_config,
    validate_overpay_autopay_settings,
)
from core.settings.payments_config import PAYMENTS_CONFIG
from database.settings import get_setting
from filters.admin import IsAdminFilter, get_admin_context
from settings.buttons import BACK

from ..panel.headers import menu_text, quote
from ..panel.keyboard import AdminPanelCallback
from .keyboard import build_settings_cashbox_kb
from .settings_config import OVERPAY_AUTOPAY_OPTIONS, OVERPAY_AUTOPAY_TITLES, PAYMENT_CASHBOX_TITLES
from .settings_descriptions import OVERPAY_AUTOPAY_SETTINGS_DESCRIPTION, OVERPAY_AUTOPAY_SETTING_HINTS


SETTING_INVALID_VALUE = "Некорректное значение настройки."
SETTING_UPDATED = "Настройка обновлена."
OVERPAY_METHODS_HINT = (
    "Карты, СБП и автоплатежи включаются независимо. "
    "Автоплатежи по картам требуют подключения рекуррентных платежей у OverPay и согласия клиента."
)
OVERPAY_AUTOPAY_SETTINGS_TITLE = "Автоплатежи OverPay"
_SETTING_KEYS = tuple(OVERPAY_AUTOPAY_TITLES)

router = Router(name="admin_settings_overpay")
router.callback_query.filter(IsAdminFilter())
router.message.filter(IsAdminFilter())


class OverpayAutopaySettingsState(StatesGroup):
    waiting_value = State()


async def _has_full_admin(event: CallbackQuery | Message) -> bool:
    """Проверяет актуальные полные права администратора."""
    if not event.from_user:
        return False
    _, is_super, _ = await get_admin_context(event.from_user.id, fresh=True)
    return is_super


def cashbox_keyboard(*, show_autopay_settings: bool = True) -> InlineKeyboardMarkup:
    """Собирает способы оплаты и параметры OverPay."""
    hidden = frozenset() if show_autopay_settings else frozenset({"OVERPAY_AUTOPAY"})
    rows = build_settings_cashbox_kb("OVERPAY", PAYMENTS_CONFIG, hidden_providers=hidden).inline_keyboard[:-1]
    if show_autopay_settings:
        rows.append([
            InlineKeyboardButton(
                text="⚙️ Параметры автоплатежей",
                callback_data=AdminPanelCallback(action="settings_overpay_autopay").pack(),
            )
        ])
    rows.append([InlineKeyboardButton(text=BACK, callback_data=AdminPanelCallback(action="settings_cashboxes").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_overpay_settings(callback: CallbackQuery) -> None:
    """Показывает настройки кассы с учётом прав администратора."""
    is_super = await _has_full_admin(callback)
    await callback.message.edit_text(
        menu_text(PAYMENT_CASHBOX_TITLES["OVERPAY"], quote(OVERPAY_METHODS_HINT)),
        reply_markup=cashbox_keyboard(show_autopay_settings=is_super),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_overpay"))
async def open_overpay_settings(callback: CallbackQuery, state: FSMContext) -> None:
    """Открывает способы оплаты и настройки OverPay."""
    await state.clear()
    await show_overpay_settings(callback)
    await callback.answer()


def _setting_key(index: int) -> str | None:
    """Возвращает параметр по номеру кнопки."""
    return _SETTING_KEYS[index - 1] if 1 <= index <= len(_SETTING_KEYS) else None


def _display_value(key: str, value: Any) -> str:
    """Форматирует текущее значение параметра."""
    for option in OVERPAY_AUTOPAY_OPTIONS.get(key, []):
        if option["value"] == value:
            return option["label"]
    return str(value)


def autopay_keyboard(values: dict[str, Any] | None = None) -> InlineKeyboardMarkup:
    """Собирает редактируемые параметры автоплатежей."""
    config = values if values is not None else OVERPAY_AUTOPAY_CONFIG
    rows = [
        [
            InlineKeyboardButton(
                text=f"{OVERPAY_AUTOPAY_TITLES[key]}: {_display_value(key, config.get(key, DEFAULT_OVERPAY_AUTOPAY_CONFIG[key]))}",
                callback_data=AdminPanelCallback(action="settings_overpay_auto_edit", page=index).pack(),
            )
        ]
        for index, key in enumerate(_SETTING_KEYS, 1)
    ]
    rows.append([InlineKeyboardButton(text=BACK, callback_data=AdminPanelCallback(action="settings_overpay").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _field_keyboard(index: int, current: Any) -> InlineKeyboardMarkup:
    """Собирает варианты значения и возврат к списку."""
    key = _setting_key(index)
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'✅ ' if option['value'] == current else ''}{option['label']}",
                callback_data=AdminPanelCallback(
                    action="settings_overpay_auto_option", page=index * 100 + option_index
                ).pack(),
            )
        ]
        for option_index, option in enumerate(OVERPAY_AUTOPAY_OPTIONS.get(key, []), 1)
    ]
    rows.append([
        InlineKeyboardButton(text=BACK, callback_data=AdminPanelCallback(action="settings_overpay_autopay").pack())
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _stored_config(session: AsyncSession) -> dict[str, Any]:
    """Читает сохранённые параметры вместе со значениями по умолчанию."""
    stored = await get_setting(session, OVERPAY_AUTOPAY_SETTING_KEY, {})
    return {**DEFAULT_OVERPAY_AUTOPAY_CONFIG, **(stored if isinstance(stored, dict) else {})}


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_overpay_autopay"), _has_full_admin)
async def open_autopay_settings(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Открывает параметры и отменяет незавершённый ввод."""
    await state.clear()
    await callback.message.edit_text(
        menu_text(OVERPAY_AUTOPAY_SETTINGS_TITLE, quote(OVERPAY_AUTOPAY_SETTINGS_DESCRIPTION)),
        reply_markup=autopay_keyboard(await _stored_config(session)),
    )
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_overpay_auto_edit"), _has_full_admin)
async def edit_autopay_setting(
    callback: CallbackQuery, callback_data: AdminPanelCallback, state: FSMContext, session: AsyncSession
) -> None:
    """Запрашивает новое значение параметра автоплатежей."""
    await state.clear()
    key = _setting_key(callback_data.page)
    if key is None:
        await callback.answer(SETTING_INVALID_VALUE, show_alert=True)
        return
    config = await _stored_config(session)
    hint = OVERPAY_AUTOPAY_SETTING_HINTS[key]
    if key in OVERPAY_AUTOPAY_OPTIONS:
        prompt = quote(hint)
    else:
        prompt = (
            f"Текущее значение: <b>{escape(_display_value(key, config[key]))}</b>.\n{hint}\n\nПришлите новое значение."
        )
        await state.set_state(OverpayAutopaySettingsState.waiting_value)
        await state.update_data(overpay_autopay_field=key)
    await callback.message.edit_text(
        menu_text(OVERPAY_AUTOPAY_TITLES[key], prompt),
        reply_markup=_field_keyboard(callback_data.page, config[key]),
    )
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_overpay_auto_option"), _has_full_admin)
async def select_autopay_option(
    callback: CallbackQuery, callback_data: AdminPanelCallback, state: FSMContext, session: AsyncSession
) -> None:
    """Сохраняет разрешённый вариант настройки."""
    await state.clear()
    setting_index, option_index = divmod(callback_data.page, 100)
    key = _setting_key(setting_index)
    options = OVERPAY_AUTOPAY_OPTIONS.get(key, [])
    if key is None or not 1 <= option_index <= len(options):
        await callback.answer(SETTING_INVALID_VALUE, show_alert=True)
        return
    await update_overpay_autopay_config(session, {key: options[option_index - 1]["value"]})
    await callback.message.edit_text(
        menu_text(OVERPAY_AUTOPAY_SETTINGS_TITLE, quote(OVERPAY_AUTOPAY_SETTINGS_DESCRIPTION)),
        reply_markup=autopay_keyboard(),
    )
    await callback.answer(SETTING_UPDATED)


@router.message(OverpayAutopaySettingsState.waiting_value, _has_full_admin)
async def save_autopay_setting(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Проверяет и сохраняет введённый параметр автоплатежей."""
    key = (await state.get_data()).get("overpay_autopay_field")
    if key not in _SETTING_KEYS or key in OVERPAY_AUTOPAY_OPTIONS:
        await state.clear()
        await message.answer(SETTING_INVALID_VALUE)
        return
    raw = (message.text or "").strip()
    try:
        value = int(raw) if isinstance(DEFAULT_OVERPAY_AUTOPAY_CONFIG[key], int) else raw.replace(",", ".")
        values = validate_overpay_autopay_settings({key: value})
    except (ValueError, TypeError, OverflowError):
        await message.answer(OVERPAY_AUTOPAY_SETTINGS_INVALID)
        return
    await update_overpay_autopay_config(session, values)
    await state.clear()
    await message.answer(
        menu_text(OVERPAY_AUTOPAY_SETTINGS_TITLE, SETTING_UPDATED, quote(OVERPAY_AUTOPAY_SETTINGS_DESCRIPTION)),
        reply_markup=autopay_keyboard(),
    )

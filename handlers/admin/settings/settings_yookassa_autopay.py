from html import escape
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from core.settings.yookassa_autopay_config import (
    DEFAULT_YOOKASSA_AUTOPAY_CONFIG,
    YOOKASSA_AUTOPAY_CONFIG,
    YOOKASSA_AUTOPAY_SETTING_KEY,
    update_yookassa_autopay_config,
    validate_yookassa_autopay_settings,
)
from database.settings import get_setting
from filters.admin import get_admin_context
from settings import texts
from settings.buttons import BACK
from settings.texts import (
    SETTING_INVALID_VALUE,
    SETTING_UPDATED,
    YOOKASSA_AUTOPAY_SETTINGS_DESCRIPTION,
    YOOKASSA_AUTOPAY_SETTINGS_INVALID,
    YOOKASSA_AUTOPAY_SETTINGS_TITLE,
    YOOKASSA_AUTOPAY_SETTING_HINTS,
)

from ..panel.headers import menu_text, quote
from ..panel.keyboard import AdminPanelCallback
from .settings_config import YOOKASSA_AUTOPAY_OPTIONS, YOOKASSA_AUTOPAY_TITLES


async def _has_full_admin(event: CallbackQuery | Message) -> bool:
    """Проверяет актуальные полные права администратора."""
    if not event.from_user:
        return False
    _, is_super, _ = await get_admin_context(event.from_user.id, fresh=True)
    return is_super


router = Router(name="admin_settings_yookassa_autopay")
router.callback_query.filter(_has_full_admin)
router.message.filter(_has_full_admin)
_SETTING_KEYS = tuple(YOOKASSA_AUTOPAY_TITLES)


class YookassaAutopaySettingsState(StatesGroup):
    waiting_value = State()


def _setting_key(index: int) -> str | None:
    """Возвращает поле по индексу кнопки."""
    return _SETTING_KEYS[index - 1] if 1 <= index <= len(_SETTING_KEYS) else None


def _display_value(key: str, value: Any, limit: int = 80) -> str:
    """Форматирует текущее значение настройки."""
    if isinstance(value, bool):
        return "✅" if value else "❌"
    for option in YOOKASSA_AUTOPAY_OPTIONS.get(key, []):
        if option["value"] == value:
            return option["label"]
    rendered = ", ".join(str(item) for item in value) if isinstance(value, list | tuple) else str(value)
    return rendered if len(rendered) <= limit else rendered[: limit - 1] + "…"


def autopay_keyboard(values: dict[str, Any] | None = None) -> InlineKeyboardMarkup:
    """Собирает редактируемые параметры автоплатежей."""
    config = values if values is not None else YOOKASSA_AUTOPAY_CONFIG
    rows = []
    for index, key in enumerate(_SETTING_KEYS, 1):
        value = config.get(key, DEFAULT_YOOKASSA_AUTOPAY_CONFIG[key])
        rows.append([
            InlineKeyboardButton(
                text=f"{YOOKASSA_AUTOPAY_TITLES[key]}: {_display_value(key, value, 40)}",
                callback_data=AdminPanelCallback(action="settings_yoo_auto_edit", page=index).pack(),
            )
        ])
    rows.append([InlineKeyboardButton(text=BACK, callback_data=AdminPanelCallback(action="settings_yookassa").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _field_keyboard(index: int, current: Any) -> InlineKeyboardMarkup:
    """Собирает варианты значения и возврат к списку."""
    key = _setting_key(index)
    rows = []
    for option_index, option in enumerate(YOOKASSA_AUTOPAY_OPTIONS.get(key, []), 1):
        prefix = "✅ " if option["value"] == current else ""
        rows.append([
            InlineKeyboardButton(
                text=f"{prefix}{option['label']}",
                callback_data=AdminPanelCallback(
                    action="settings_yoo_auto_option", page=index * 100 + option_index
                ).pack(),
            )
        ])
    rows.append([
        InlineKeyboardButton(text=BACK, callback_data=AdminPanelCallback(action="settings_yoo_autopay").pack())
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_yoo_autopay"))
async def open_autopay_settings(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Открывает параметры и отменяет незавершённый ввод."""
    await state.clear()
    stored = await get_setting(session, YOOKASSA_AUTOPAY_SETTING_KEY, {})
    config = {**DEFAULT_YOOKASSA_AUTOPAY_CONFIG, **(stored if isinstance(stored, dict) else YOOKASSA_AUTOPAY_CONFIG)}
    await callback.message.edit_text(
        menu_text(YOOKASSA_AUTOPAY_SETTINGS_TITLE, quote(YOOKASSA_AUTOPAY_SETTINGS_DESCRIPTION)),
        reply_markup=autopay_keyboard(config),
    )
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_yoo_auto_edit"))
async def edit_autopay_setting(
    callback: CallbackQuery, callback_data: AdminPanelCallback, state: FSMContext, session: AsyncSession
) -> None:
    """Переключает флаг или запрашивает новое значение."""
    await state.clear()
    key = _setting_key(callback_data.page)
    if key is None:
        await callback.answer(SETTING_INVALID_VALUE, show_alert=True)
        return
    stored = await get_setting(session, YOOKASSA_AUTOPAY_SETTING_KEY, {})
    config = {**DEFAULT_YOOKASSA_AUTOPAY_CONFIG, **(stored if isinstance(stored, dict) else YOOKASSA_AUTOPAY_CONFIG)}
    current = config[key]
    if isinstance(DEFAULT_YOOKASSA_AUTOPAY_CONFIG[key], bool):
        values = validate_yookassa_autopay_settings({key: not bool(current)})
        await update_yookassa_autopay_config(session, values)
        await callback.message.edit_text(
            menu_text(YOOKASSA_AUTOPAY_SETTINGS_TITLE, quote(YOOKASSA_AUTOPAY_SETTINGS_DESCRIPTION)),
            reply_markup=autopay_keyboard(),
        )
        await callback.answer(SETTING_UPDATED)
        return
    hint = YOOKASSA_AUTOPAY_SETTING_HINTS.get(key, "")
    if key in YOOKASSA_AUTOPAY_OPTIONS:
        prompt = quote(hint)
    else:
        value = escape(_display_value(key, current, 1000))
        template = getattr(texts, "YOOKASSA_AUTOPAY_SETTING_PROMPT", None)
        prompt = (
            template.format(value=value, hint=hint) if template else f"<b>{value}</b>\n{quote(hint) if hint else ''}"
        )
        await state.set_state(YookassaAutopaySettingsState.waiting_value)
        await state.update_data(yookassa_autopay_field=key)
    await callback.message.edit_text(
        menu_text(YOOKASSA_AUTOPAY_TITLES[key], prompt),
        reply_markup=_field_keyboard(callback_data.page, current),
    )
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_yoo_auto_option"))
async def select_autopay_option(
    callback: CallbackQuery, callback_data: AdminPanelCallback, state: FSMContext, session: AsyncSession
) -> None:
    """Сохраняет разрешённый вариант настройки."""
    await state.clear()
    setting_index, option_index = divmod(callback_data.page, 100)
    key = _setting_key(setting_index)
    options = YOOKASSA_AUTOPAY_OPTIONS.get(key, [])
    if key is None or not 1 <= option_index <= len(options):
        await callback.answer(SETTING_INVALID_VALUE, show_alert=True)
        return
    values = validate_yookassa_autopay_settings({key: options[option_index - 1]["value"]})
    await update_yookassa_autopay_config(session, values)
    await callback.message.edit_text(
        menu_text(YOOKASSA_AUTOPAY_SETTINGS_TITLE, quote(YOOKASSA_AUTOPAY_SETTINGS_DESCRIPTION)),
        reply_markup=autopay_keyboard(),
    )
    await callback.answer(SETTING_UPDATED)


@router.message(YookassaAutopaySettingsState.waiting_value)
async def save_autopay_setting(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Проверяет введённое значение общим валидатором."""
    data = await state.get_data()
    key = data.get("yookassa_autopay_field")
    if key not in _SETTING_KEYS or key in YOOKASSA_AUTOPAY_OPTIONS:
        await state.clear()
        await message.answer(SETTING_INVALID_VALUE)
        return
    default = DEFAULT_YOOKASSA_AUTOPAY_CONFIG[key]
    if isinstance(default, bool):
        await state.clear()
        await message.answer(SETTING_INVALID_VALUE)
        return
    raw = (message.text or "").strip()
    try:
        if isinstance(default, int):
            value = int(raw)
        elif isinstance(default, float):
            value = raw.replace(",", ".")
        else:
            value = raw
        values = validate_yookassa_autopay_settings({key: value})
    except (ValueError, TypeError, OverflowError):
        await message.answer(YOOKASSA_AUTOPAY_SETTINGS_INVALID)
        return
    await update_yookassa_autopay_config(session, values)
    await state.clear()
    await message.answer(
        menu_text(YOOKASSA_AUTOPAY_SETTINGS_TITLE, SETTING_UPDATED, quote(YOOKASSA_AUTOPAY_SETTINGS_DESCRIPTION)),
        reply_markup=autopay_keyboard(),
    )

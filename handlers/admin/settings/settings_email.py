from html import escape as html_escape
from typing import Any

from aiogram import F, Router
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.defaults import DEFAULT_WEB_CONFIG
from core.settings.web_config import WEB_CONFIG, update_web_config
from database import async_session_maker
from settings.config import PROJECT_NAME

from ..panel.keyboard import AdminPanelCallback, build_admin_back_btn


router = Router(name="admin_settings_email")


class EmailFieldCallback(CallbackData, prefix="adm_email"):
    action: str
    key: str = ""


class EmailSettingsState(StatesGroup):
    waiting_for_value = State()


SENDER_FIELDS: list[tuple[str, str]] = [
    ("EMAIL_FROM_NAME", "✉️ Имя отправителя"),
    ("EMAIL_REPLY_TO", "↩️ Адрес для ответа"),
]

CATEGORIES: dict[str, dict[str, Any]] = {
    "login": {
        "label": "🔐 Вход",
        "vars": "{project}, {code}",
        "fields": [("EMAIL_LOGIN_SUBJECT", "Тема"), ("EMAIL_LOGIN_BODY", "Текст")],
    },
    "reset": {
        "label": "🔑 Сброс пароля",
        "vars": "{project}, {code}",
        "fields": [("EMAIL_RESET_SUBJECT", "Тема"), ("EMAIL_RESET_BODY", "Текст")],
    },
    "verify": {
        "label": "✅ Подтверждение email",
        "vars": "{project}, {code}",
        "fields": [("EMAIL_VERIFY_SUBJECT", "Тема"), ("EMAIL_VERIFY_BODY", "Текст")],
    },
    "link": {
        "label": "🔗 Привязка почты",
        "vars": "{project}, {code}",
        "fields": [("EMAIL_LINK_SUBJECT", "Тема"), ("EMAIL_LINK_BODY", "Текст")],
    },
    "support": {
        "label": "💬 Ответ поддержки",
        "vars": "{project}, {ref}, {reply}",
        "fields": [("EMAIL_SUPPORT_REPLY_SUBJECT", "Тема"), ("EMAIL_SUPPORT_REPLY_BODY", "Текст")],
    },
    "broadcast": {
        "label": "📧 Письмо рассылки",
        "vars": "{project}, {content}, {image}, {cta}, {site_url}, {year}",
        "fields": [("EMAIL_BROADCAST_SUBJECT", "Тема"), ("EMAIL_BROADCAST_TEMPLATE", "HTML-шаблон")],
    },
}

FIELD_VARS_OVERRIDE = {
    "EMAIL_BROADCAST_SUBJECT": "{project}, {title}",
}

FIELD_LABELS: dict[str, str] = {}
FIELD_VARS: dict[str, str] = {}
for _key, _label in SENDER_FIELDS:
    FIELD_LABELS[_key] = _label
for _cat in CATEGORIES.values():
    for _key, _label in _cat["fields"]:
        FIELD_LABELS[_key] = f"{_cat['label']} · {_label}"
        FIELD_VARS[_key] = FIELD_VARS_OVERRIDE.get(_key, _cat["vars"])

CATEGORY_BY_FIELD: dict[str, str] = {}
for _cat_id, _cat in CATEGORIES.items():
    for _key, _ in _cat["fields"]:
        CATEGORY_BY_FIELD[_key] = _cat_id


def _stored(key: str) -> str:
    val = WEB_CONFIG.get(key)
    return str(val).strip() if val else ""


def _preview(key: str) -> str:
    val = _stored(key)
    if not val:
        if key == "EMAIL_FROM_NAME":
            return f"{PROJECT_NAME} (по умолчанию)"
        if key == "EMAIL_REPLY_TO":
            return "не задан"
        if key == "EMAIL_BROADCAST_TEMPLATE":
            return "встроенный шаблон по умолчанию"
        return str(DEFAULT_WEB_CONFIG.get(key, "")) or "не задан"
    return val


def _clip(text: str, limit: int = 40) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_email_main_kb() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    for key, label in SENDER_FIELDS:
        builder.row(
            InlineKeyboardButton(
                text=f"{label}: {_clip(_preview(key))}",
                callback_data=EmailFieldCallback(action="edit", key=key).pack(),
            )
        )
    for cat_id, cat in CATEGORIES.items():
        builder.row(
            InlineKeyboardButton(
                text=cat["label"],
                callback_data=EmailFieldCallback(action="cat", key=cat_id).pack(),
            )
        )
    builder.row(build_admin_back_btn("settings_web"))
    return builder


def _email_main_text() -> str:
    return (
        "<b>✉️ Шаблоны писем</b>\n\n"
        "Тексты писем, которые сайт отправляет пользователям на почту, "
        "и подпись отправителя.\n\n"
        f"Отправитель: <b>{html_escape(_preview('EMAIL_FROM_NAME'))}</b>\n"
        f"Reply-To: <code>{html_escape(_preview('EMAIL_REPLY_TO'))}</code>\n\n"
        "Выберите, что отредактировать."
    )


def build_category_kb(cat_id: str) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    cat = CATEGORIES[cat_id]
    for key, label in cat["fields"]:
        builder.row(
            InlineKeyboardButton(
                text=f"{label}: {_clip(_preview(key))}",
                callback_data=EmailFieldCallback(action="edit", key=key).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=EmailFieldCallback(action="open").pack(),
        )
    )
    return builder


def _category_text(cat_id: str) -> str:
    cat = CATEGORIES[cat_id]
    lines = [f"<b>{cat['label']}</b>\n"]
    for key, label in cat["fields"]:
        value = _preview(key)
        if len(value) > 300:
            value = value[:300] + "…"
        lines.append(f"<b>{label}:</b>\n<code>{html_escape(value)}</code>\n")
    lines.append(f"Переменные: <code>{html_escape(cat['vars'])}</code>")
    return "\n".join(lines)


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_email"))
async def open_email_settings(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        text=_email_main_text(),
        reply_markup=build_email_main_kb().as_markup(),
    )
    await callback.answer()


@router.callback_query(EmailFieldCallback.filter(F.action == "open"))
async def back_to_email_main(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        text=_email_main_text(),
        reply_markup=build_email_main_kb().as_markup(),
    )
    await callback.answer()


@router.callback_query(EmailFieldCallback.filter(F.action == "cat"))
async def open_category(callback: CallbackQuery, callback_data: EmailFieldCallback) -> None:
    cat_id = callback_data.key
    if cat_id not in CATEGORIES:
        await callback.answer()
        return
    await callback.message.edit_text(
        text=_category_text(cat_id),
        reply_markup=build_category_kb(cat_id).as_markup(),
    )
    await callback.answer()


@router.callback_query(EmailFieldCallback.filter(F.action == "edit"))
async def prompt_field(callback: CallbackQuery, callback_data: EmailFieldCallback, state: FSMContext) -> None:
    key = callback_data.key
    if key not in FIELD_LABELS:
        await callback.answer()
        return

    vars_hint = FIELD_VARS.get(key)
    hint_line = f"Доступные переменные: <code>{html_escape(vars_hint)}</code>\n\n" if vars_hint else ""
    reset_line = (
        "Отправьте <code>-</code>, чтобы очистить."
        if key in ("EMAIL_FROM_NAME", "EMAIL_REPLY_TO")
        else "Отправьте <code>-</code>, чтобы вернуть значение по умолчанию."
    )
    current = _preview(key)
    if len(current) > 800:
        current = current[:800] + "…"
    text = (
        f"<b>{FIELD_LABELS[key]}</b>\n\n"
        f"Текущее значение:\n<code>{html_escape(current)}</code>\n\n"
        f"{hint_line}"
        f"Отправьте новый текст сообщением.\n{reset_line}"
    )
    await callback.message.edit_text(text=text)
    await state.set_state(EmailSettingsState.waiting_for_value)
    await state.update_data(email_key=key)
    await callback.answer()


@router.message(EmailSettingsState.waiting_for_value)
async def save_field(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    key = data.get("email_key")
    if key not in FIELD_LABELS:
        await state.clear()
        return

    raw = (message.text or "").strip()
    value = "" if raw == "-" else raw

    new_config = dict(WEB_CONFIG)
    new_config[key] = value

    async with async_session_maker() as session:
        await update_web_config(session, new_config)

    await state.clear()
    cat_id = CATEGORY_BY_FIELD.get(key)
    if cat_id:
        await message.answer(
            text=_category_text(cat_id),
            reply_markup=build_category_kb(cat_id).as_markup(),
        )
    else:
        await message.answer(
            text=_email_main_text(),
            reply_markup=build_email_main_kb().as_markup(),
        )

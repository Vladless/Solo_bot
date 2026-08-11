from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.settings.legal_config import LEGAL_CONFIG, update_legal_config
from database import async_session_maker

from ..panel.headers import menu_text, quote, section
from ..panel.keyboard import AdminPanelCallback, build_admin_back_btn


router = Router(name="admin_settings_legal")

DOCS: tuple[tuple[str, str, str], ...] = (
    ("LEGAL_PRIVACY_URL", "Политика конфиденциальности", "settings_legal_privacy"),
    ("LEGAL_TERMS_URL", "Пользовательское соглашение", "settings_legal_terms"),
    ("LEGAL_OFFER_URL", "Оферта", "settings_legal_offer"),
)

ACTION_TO_KEY: dict[str, str] = {action: key for key, _, action in DOCS}


class LegalSettingsState(StatesGroup):
    waiting_for_url = State()
    waiting_for_intro = State()


def _url(key: str) -> str:
    return str(LEGAL_CONFIG.get(key) or "").strip()


def _enabled() -> bool:
    return bool(LEGAL_CONFIG.get("LEGAL_DOCS_ENABLED", False))


def build_settings_legal_kb() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=f"{'✅' if _enabled() else '❌'} Документы {'включены' if _enabled() else 'выключены'}",
            callback_data=AdminPanelCallback(action="settings_legal_toggle").pack(),
        )
    )
    for key, title, action in DOCS:
        mark = "🔗" if _url(key) else "➖"
        builder.row(
            InlineKeyboardButton(
                text=f"{mark} {title}",
                callback_data=AdminPanelCallback(action=action).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="✏️ Текст согласия",
            callback_data=AdminPanelCallback(action="settings_legal_intro").pack(),
        )
    )
    builder.row(build_admin_back_btn("settings"))
    return builder


def _legal_settings_text() -> str:
    lines = []
    for key, title, _ in DOCS:
        lines.append(f"{title}: {('<code>' + _url(key) + '</code>') if _url(key) else 'не указана'}")
    filled = sum(1 for key, _, _ in DOCS if _url(key))
    return menu_text(
        "Документы",
        "Правовые документы сервиса.",
        quote(f"Статус: {'✅ включены' if _enabled() else '❌ выключены'}\n" + "\n".join(lines)),
        quote(
            "При первом входе клиент видит документы и подтверждает согласие, дальше они висят кнопками в меню «О сервисе».",
            "Без единой ссылки раздел не показывается, даже если тумблер включён."
            if not filled
            else "Кнопки показываются только для заполненных ссылок.",
        ),
    )


async def _save(new_config: dict) -> None:
    async with async_session_maker() as session:
        await update_legal_config(session, new_config)


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_legal"))
async def open_legal_settings(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        text=_legal_settings_text(),
        reply_markup=build_settings_legal_kb().as_markup(),
    )
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_legal_toggle"), flags={"popup": True})
async def toggle_legal(callback: CallbackQuery) -> None:
    new_config = dict(LEGAL_CONFIG)
    new_config["LEGAL_DOCS_ENABLED"] = not _enabled()
    await _save(new_config)

    status = "✅ Документы включены" if new_config["LEGAL_DOCS_ENABLED"] else "❌ Документы выключены"
    await callback.answer(status, show_alert=True)
    await callback.message.edit_text(
        text=_legal_settings_text(),
        reply_markup=build_settings_legal_kb().as_markup(),
    )


@router.callback_query(AdminPanelCallback.filter(F.action.in_(set(ACTION_TO_KEY))))
async def prompt_legal_url(callback: CallbackQuery, callback_data: AdminPanelCallback, state: FSMContext) -> None:
    key = ACTION_TO_KEY[callback_data.action]
    title = next(t for k, t, _ in DOCS if k == key)
    await callback.message.edit_text(
        text=menu_text(
            title,
            "Отправьте ссылку на документ или «-», чтобы убрать кнопку.",
            section("🔗 Сейчас", _url(key) or "не указана"),
            section("💡 Пример", "https://example.com/privacy"),
        )
    )
    await state.update_data(legal_key=key)
    await state.set_state(LegalSettingsState.waiting_for_url)
    await callback.answer()


@router.message(LegalSettingsState.waiting_for_url)
async def set_legal_url(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    key = data.get("legal_key")
    if key not in ACTION_TO_KEY.values():
        await state.clear()
        return

    raw = (message.text or "").strip()
    if raw == "-":
        value = ""
    elif raw.startswith(("http://", "https://")):
        value = raw
    else:
        await message.answer(menu_text("Документы", "❌ Ссылка должна начинаться с http:// или https://"))
        return

    new_config = dict(LEGAL_CONFIG)
    new_config[key] = value
    await _save(new_config)

    await state.clear()
    await message.answer(
        text=_legal_settings_text(),
        reply_markup=build_settings_legal_kb().as_markup(),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_legal_intro"))
async def prompt_legal_intro(callback: CallbackQuery, state: FSMContext) -> None:
    current = str(LEGAL_CONFIG.get("LEGAL_INTRO_TEXT") or "")
    await callback.message.edit_text(
        text=menu_text(
            "Текст согласия",
            "Что клиент читает при первом входе. Отправьте новый текст.",
            section("📝 Сейчас", current or "не задан"),
        )
    )
    await state.set_state(LegalSettingsState.waiting_for_intro)
    await callback.answer()


@router.message(LegalSettingsState.waiting_for_intro)
async def set_legal_intro(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw:
        await message.answer(menu_text("Документы", "❌ Текст не может быть пустым."))
        return

    new_config = dict(LEGAL_CONFIG)
    new_config["LEGAL_INTRO_TEXT"] = raw
    await _save(new_config)

    await state.clear()
    await message.answer(
        text=_legal_settings_text(),
        reply_markup=build_settings_legal_kb().as_markup(),
    )

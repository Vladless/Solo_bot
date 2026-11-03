from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from core.settings.payments_config import PAYMENTS_CONFIG
from core.settings.yookassa_config import YOOKASSA_CONFIG, update_yookassa_config
from filters.admin import IsAdminFilter, get_admin_context
from settings.buttons import BACK
from settings.texts import YOOKASSA_MARKUP_INVALID

from ..panel.headers import menu_text, quote
from ..panel.keyboard import AdminPanelCallback
from .keyboard import build_settings_cashbox_kb
from .settings_config import PAYMENT_CASHBOX_TITLES, YOOKASSA_TITLES
from .settings_descriptions import YOOKASSA_PAGE_SETTINGS_HINT, YOOKASSA_PAGE_SETTING_HINTS


SETTING_UPDATED = "Настройка обновлена."
YOOKASSA_AUTOPAY_SETTINGS = "⚙️ Параметры автоплатежей"
YOOKASSA_PAGE_SETTINGS_TITLE = "Своя страница оплаты ЮКассы"
YOOKASSA_PAGE_SETTING_PROMPT = "Текущее значение: <b>{value}</b>.\n{hint}\n\nПришлите новое значение."
YOOKASSA_PAGE_SETTING_KEYS = tuple(key for key in YOOKASSA_TITLES if key.startswith("PAGE_"))
YOOKASSA_MARKUP_PERCENT_PROMPT = "Введите процент наценки от 0 до 100. Можно использовать десятичную дробь."
YOOKASSA_METHODS_HINT = (
    "Карты и СБП ЮКассы включаются независимо. "
    "Чтобы предложить оба способа оплаты, включите «ЮКасса · карты» и «ЮКасса · СБП». "
    "Наценка и параметры автоплатежей находятся в разделе «Кассы → ЮКасса»."
)
YOOKASSA_SETTINGS_HINT = (
    "Наценка добавляется к сумме оплаты, а на баланс зачисляется исходная сумма. "
    "Клиент видит полную стоимость перед оплатой. Для автоплатежей требуется согласие на полную сумму."
)


router = Router(name="admin_settings_yookassa")
router.callback_query.filter(IsAdminFilter())
router.message.filter(IsAdminFilter())


class YookassaMarkupState(StatesGroup):
    waiting_percent = State()
    waiting_page_value = State()


def markup_keyboard(*, show_autopay_settings: bool = True) -> InlineKeyboardMarkup:
    """Собирает способы оплаты и настройки ЮКассы."""
    enabled = bool(YOOKASSA_CONFIG.get("MARKUP_ENABLED", False))
    percent = escape(str(YOOKASSA_CONFIG.get("MARKUP_PERCENT", "0")))
    rows = build_settings_cashbox_kb("YOOKASSA", PAYMENTS_CONFIG).inline_keyboard[:-1]
    rows.extend([
        [
            InlineKeyboardButton(
                text=f"{'✅' if enabled else '❌'} {YOOKASSA_TITLES['MARKUP_ENABLED']}",
                callback_data=AdminPanelCallback(action="settings_yookassa_toggle").pack(),
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{YOOKASSA_TITLES['MARKUP_PERCENT']}: {percent}",
                callback_data=AdminPanelCallback(action="settings_yookassa_percent").pack(),
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{'✅' if YOOKASSA_CONFIG.get('PAGE_ENABLED', False) else '❌'} Своя страница оплаты",
                callback_data=AdminPanelCallback(action="settings_yoo_page").pack(),
            )
        ],
    ])
    if show_autopay_settings:
        rows.append([
            InlineKeyboardButton(
                text="↩️ Возвраты",
                callback_data=AdminPanelCallback(action="settings_yoo_refunds").pack(),
            )
        ])
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


def _page_setting_value(key: str, value: object, limit: int = 60) -> str:
    """Форматирует параметр страницы для администратора."""
    if isinstance(value, bool):
        return "✅" if value else "❌"
    if key == "PAGE_TEST_USERS" and not value:
        return "все пользователи"
    if key == "PAGE_PUBLIC_URL" and not value:
        return "внешний адрес бота"
    rendered = ", ".join(str(item) for item in value) if isinstance(value, list | tuple) else str(value)
    return rendered if len(rendered) <= limit else rendered[: limit - 1] + "…"


def page_settings_keyboard() -> InlineKeyboardMarkup:
    """Собирает настройки своей страницы оплаты."""
    rows = [
        [
            InlineKeyboardButton(
                text=f"{YOOKASSA_TITLES[key]}: {_page_setting_value(key, YOOKASSA_CONFIG.get(key), 35)}",
                callback_data=AdminPanelCallback(action="settings_yoo_page_edit", page=index).pack(),
            )
        ]
        for index, key in enumerate(YOOKASSA_PAGE_SETTING_KEYS, 1)
    ]
    rows.append([InlineKeyboardButton(text=BACK, callback_data=AdminPanelCallback(action="settings_yookassa").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_page_settings(callback: CallbackQuery) -> None:
    """Показывает параметры своей страницы оплаты."""
    await callback.message.edit_text(
        menu_text(YOOKASSA_PAGE_SETTINGS_TITLE, quote(YOOKASSA_PAGE_SETTINGS_HINT)),
        reply_markup=page_settings_keyboard(),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_yoo_page"))
async def open_page_settings(callback: CallbackQuery, state: FSMContext) -> None:
    """Открывает параметры и отменяет незавершённый ввод."""
    await state.clear()
    await show_page_settings(callback)
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_yoo_page_edit"))
async def edit_page_setting(
    callback: CallbackQuery, callback_data: AdminPanelCallback, state: FSMContext, session: AsyncSession
) -> None:
    """Переключает страницу или запрашивает параметр."""
    await state.clear()
    index = callback_data.page
    if not 1 <= index <= len(YOOKASSA_PAGE_SETTING_KEYS):
        await callback.answer("Некорректная настройка.", show_alert=True)
        return
    key = YOOKASSA_PAGE_SETTING_KEYS[index - 1]
    if key == "PAGE_ENABLED":
        await update_yookassa_config(session, {key: not bool(YOOKASSA_CONFIG.get(key, False))})
        await show_page_settings(callback)
        await callback.answer(SETTING_UPDATED)
        return
    await state.set_state(YookassaMarkupState.waiting_page_value)
    await state.update_data(yookassa_page_field=key)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BACK, callback_data=AdminPanelCallback(action="settings_yoo_page").pack())]
        ]
    )
    await callback.message.edit_text(
        menu_text(
            YOOKASSA_TITLES[key],
            YOOKASSA_PAGE_SETTING_PROMPT.format(
                value=escape(_page_setting_value(key, YOOKASSA_CONFIG.get(key), 500)),
                hint=YOOKASSA_PAGE_SETTING_HINTS[key],
            ),
        ),
        reply_markup=keyboard,
    )
    await callback.answer()


@router.message(YookassaMarkupState.waiting_page_value)
async def save_page_setting(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Проверяет и сохраняет параметр страницы оплаты."""
    data = await state.get_data()
    key = data.get("yookassa_page_field")
    if key not in YOOKASSA_PAGE_SETTING_KEYS or key == "PAGE_ENABLED":
        await state.clear()
        await message.answer("Некорректная настройка.")
        return
    value = (message.text or "").strip()
    if value == "-" and key in {"PAGE_TEST_USERS", "PAGE_PUBLIC_URL"}:
        value = ""
    try:
        await update_yookassa_config(session, {key: value})
    except ValueError as exc:
        await message.answer(escape(str(exc)))
        return
    await state.clear()
    await message.answer(
        menu_text(YOOKASSA_PAGE_SETTINGS_TITLE, SETTING_UPDATED, quote(YOOKASSA_PAGE_SETTINGS_HINT)),
        reply_markup=page_settings_keyboard(),
    )


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

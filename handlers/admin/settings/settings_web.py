from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.settings.web_config import WEB_CONFIG, update_web_config
from database import async_session_maker
from settings.buttons import BACK

from ..panel.keyboard import AdminPanelCallback, build_admin_back_btn


router = Router(name="admin_settings_web")


class WebSettingsState(StatesGroup):
    waiting_for_url = State()
    waiting_for_node_status_interval = State()


def _node_status_interval_min() -> int:
    try:
        return max(1, int(WEB_CONFIG.get("WEB_NODE_STATUS_INTERVAL_MIN") or 1))
    except (TypeError, ValueError):
        return 1


def build_settings_web_kb() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()

    enabled = bool(WEB_CONFIG.get("WEB_ENABLED", False))
    url = str(WEB_CONFIG.get("SITE_URL") or "не указан")
    builder.row(
        InlineKeyboardButton(
            text=f"{'✅' if enabled else '❌'} Сайт {'включён' if enabled else 'выключен'}",
            callback_data=AdminPanelCallback(action="settings_web_toggle").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=f"🌐 URL: {url}",
            callback_data=AdminPanelCallback(action="settings_web_url").pack(),
        )
    )
    open_in_browser = bool(WEB_CONFIG.get("WEB_OPEN_IN_BROWSER", False))
    builder.row(
        InlineKeyboardButton(
            text=f"🔗 Открытие: {'в браузере' if open_in_browser else 'в веб-аппе'}",
            callback_data=AdminPanelCallback(action="settings_web_open_mode_toggle").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=f"⏱ Статус серверов: раз в {_node_status_interval_min()} мин",
            callback_data=AdminPanelCallback(action="settings_web_node_interval").pack(),
        )
    )
    email_binding = bool(WEB_CONFIG.get("EMAIL_BINDING_ENABLED", False))
    builder.row(
        InlineKeyboardButton(
            text=f"{'✅' if email_binding else '❌'} Привязка почты {'включена' if email_binding else 'выключена'}",
            callback_data=AdminPanelCallback(action="settings_web_email_binding_toggle").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🔄 Сбросить сайт к исходнику",
            callback_data=AdminPanelCallback(action="settings_web_reset_ask").pack(),
        )
    )
    builder.row(build_admin_back_btn("settings"))

    return builder


def _web_settings_text() -> str:
    enabled = bool(WEB_CONFIG.get("WEB_ENABLED", False))
    url = str(WEB_CONFIG.get("SITE_URL") or "не указан")
    email_binding = bool(WEB_CONFIG.get("EMAIL_BINDING_ENABLED", False))
    open_in_browser = bool(WEB_CONFIG.get("WEB_OPEN_IN_BROWSER", False))
    return (
        "<b>🌐 Настройки веб-сайта</b>\n\n"
        f"Статус: {'✅ Включён' if enabled else '❌ Выключен'}\n"
        f"URL: <code>{url}</code>\n"
        f"Открытие: {'🔗 в браузере' if open_in_browser else '📱 в веб-аппе'}\n"
        f"Статус серверов: раз в {_node_status_interval_min()} мин\n"
        f"Привязка почты: {'✅ Включена' if email_binding else '❌ Выключена'}\n\n"
        "Сайт может работать на отдельном домене и сервере.\n"
        "При выключении кнопка «Личный кабинет» скрывается из бота.\n"
        "Открытие «в веб-аппе» — кабинет открывается внутри Telegram, "
        "«в браузере» — обычной ссылкой во внешнем браузере.\n"
        "Привязка почты — кнопка в боте, через которую пользователь указывает email "
        "для входа на сайт на случай проблем с Telegram."
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_web"))
async def open_web_settings(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        text=_web_settings_text(),
        reply_markup=build_settings_web_kb().as_markup(),
    )
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_web_toggle"), flags={"popup": True})
async def toggle_web_enabled(callback: CallbackQuery) -> None:
    current = bool(WEB_CONFIG.get("WEB_ENABLED", False))
    new_config = dict(WEB_CONFIG)
    new_config["WEB_ENABLED"] = not current

    async with async_session_maker() as session:
        await update_web_config(session, new_config)

    status = "✅ Сайт включён" if new_config["WEB_ENABLED"] else "❌ Сайт выключен"
    await callback.answer(status, show_alert=True)
    await callback.message.edit_text(
        text=_web_settings_text(),
        reply_markup=build_settings_web_kb().as_markup(),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_web_open_mode_toggle"), flags={"popup": True})
async def toggle_web_open_mode(callback: CallbackQuery) -> None:
    current = bool(WEB_CONFIG.get("WEB_OPEN_IN_BROWSER", False))
    new_config = dict(WEB_CONFIG)
    new_config["WEB_OPEN_IN_BROWSER"] = not current

    async with async_session_maker() as session:
        await update_web_config(session, new_config)

    status = "🔗 Сайт открывается в браузере" if new_config["WEB_OPEN_IN_BROWSER"] else "📱 Сайт открывается в веб-аппе"
    await callback.answer(status, show_alert=True)
    await callback.message.edit_text(
        text=_web_settings_text(),
        reply_markup=build_settings_web_kb().as_markup(),
    )


@router.callback_query(
    AdminPanelCallback.filter(F.action == "settings_web_email_binding_toggle"), flags={"popup": True}
)
async def toggle_email_binding(callback: CallbackQuery) -> None:
    current = bool(WEB_CONFIG.get("EMAIL_BINDING_ENABLED", False))
    new_config = dict(WEB_CONFIG)
    new_config["EMAIL_BINDING_ENABLED"] = not current

    async with async_session_maker() as session:
        await update_web_config(session, new_config)

    status = "✅ Привязка почты включена" if new_config["EMAIL_BINDING_ENABLED"] else "❌ Привязка почты выключена"
    await callback.answer(status, show_alert=True)
    await callback.message.edit_text(
        text=_web_settings_text(),
        reply_markup=build_settings_web_kb().as_markup(),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_web_url"))
async def prompt_web_url(callback: CallbackQuery, state: FSMContext) -> None:
    current = str(WEB_CONFIG.get("SITE_URL") or "")
    text = (
        "<b>🌐 Введите URL сайта</b>\n\n"
        f"Текущий: <code>{current or 'не указан'}</code>\n\n"
        "Отправьте полный URL (с https://).\n"
        "Пример: <code>https://my-vpn.com</code>\n\n"
        "Отправьте <code>-</code> чтобы очистить."
    )
    await callback.message.edit_text(text=text)
    await state.set_state(WebSettingsState.waiting_for_url)
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_web_node_interval"))
async def prompt_node_status_interval(callback: CallbackQuery, state: FSMContext) -> None:
    text = (
        "<b>⏱ Интервал обновления статуса серверов</b>\n\n"
        f"Текущий: раз в <b>{_node_status_interval_min()} мин</b>\n\n"
        "Как часто сайт актуализирует список серверов и их состояние из панели.\n"
        "Отправьте число минут (от 1 до 60)."
    )
    await callback.message.edit_text(text=text)
    await state.set_state(WebSettingsState.waiting_for_node_status_interval)
    await callback.answer()


@router.message(WebSettingsState.waiting_for_node_status_interval)
async def set_node_status_interval(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    try:
        minutes = int(raw)
    except ValueError:
        await message.answer("❌ Отправьте целое число минут (от 1 до 60)")
        return
    if not 1 <= minutes <= 60:
        await message.answer("❌ Интервал должен быть от 1 до 60 минут")
        return

    new_config = dict(WEB_CONFIG)
    new_config["WEB_NODE_STATUS_INTERVAL_MIN"] = minutes

    async with async_session_maker() as session:
        await update_web_config(session, new_config)

    await state.clear()
    await message.answer(
        text=_web_settings_text(),
        reply_markup=build_settings_web_kb().as_markup(),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_web_reset_ask"))
async def ask_reset_site(callback: CallbackQuery) -> None:
    text = (
        "<b>⚠️ Сброс сайта к исходнику</b>\n\n"
        "Действие удалит:\n"
        "• все страницы, блоки, темы и варианты\n"
        "• всех веб-пользователей (включая админа сайта)\n"
        "• флаг «сайт проинициализирован»\n\n"
        "Биллинг-данные (пользователи бота, ключи, платежи) не трогаются.\n\n"
        "После сброса админ сайта пересоздаётся из переменных окружения "
        "<code>WEB_ADMIN_LOGIN</code> / <code>WEB_ADMIN_PASSWORD</code>.\n\n"
        "<b>Действие необратимо.</b>"
    )
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="❌ Отмена",
            callback_data=AdminPanelCallback(action="settings_web").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="⚠️ Да, сбросить сайт",
            callback_data=AdminPanelCallback(action="settings_web_reset_do").pack(),
        )
    )
    await callback.message.edit_text(text=text, reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_web_reset_do"))
async def do_reset_site(callback: CallbackQuery, session=None) -> None:
    await callback.answer()
    await callback.message.edit_text(text="<b>⏳ Сбрасываю сайт...</b>")

    from middlewares.session import release_session_early
    from services.site_reset import reset_site

    if session is not None:
        await release_session_early(session)

    try:
        async with async_session_maker() as s:
            await reset_site(s)
            await s.commit()
    except Exception as exc:
        from html import escape as html_escape

        safe = html_escape(str(exc))[:2000]
        await callback.message.edit_text(
            text=f"<b>❌ Не удалось сбросить сайт</b>\n\n<code>{safe}</code>",
            reply_markup=build_settings_web_kb().as_markup(),
        )
        return

    text = (
        "<b>✅ Сайт сброшен к исходнику</b>\n\n"
        "Все веб-страницы, блоки и темы удалены. Админ пересоздан из env.\n"
        "Откройте сайт и пройдите путь первой установки заново."
    )
    await callback.message.edit_text(
        text=text,
        reply_markup=build_settings_web_kb().as_markup(),
    )


@router.message(WebSettingsState.waiting_for_url)
async def set_web_url(message: Message, state: FSMContext) -> None:
    url = message.text.strip() if message.text else ""

    if url == "-":
        url = ""
    elif url and not url.startswith("http"):
        await message.answer("❌ URL должен начинаться с http:// или https://")
        return

    url = url.rstrip("/")

    new_config = dict(WEB_CONFIG)
    new_config["SITE_URL"] = url

    async with async_session_maker() as session:
        await update_web_config(session, new_config)

    await state.clear()
    await message.answer(
        text=_web_settings_text(),
        reply_markup=build_settings_web_kb().as_markup(),
    )

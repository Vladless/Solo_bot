from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import MODES_CONFIG, update_modes_config
from core.defaults import DEFAULT_MODES_CONFIG
from filters.admin import IsAdminFilter
from settings.texts import SETTING_INVALID_VALUE, SETTING_UPDATED, SINGLE_SUBSCRIPTION_SCREEN_HINT

from ..panel.headers import menu_text, quote
from ..panel.keyboard import AdminPanelCallback
from .keyboard import build_settings_modes_kb, build_settings_single_subscription_kb
from .settings_config import MODES_TITLES, SINGLE_SUBSCRIPTION_TITLES


router = Router(name="admin_settings_modes")
router.callback_query.filter(IsAdminFilter())


async def load_modes_settings() -> dict[str, bool]:
    config = MODES_CONFIG or {}
    return {k: bool(config.get(k, DEFAULT_MODES_CONFIG.get(k, False))) for k in MODES_TITLES}


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_modes"))
async def open_settings_modes_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    modes_state = await load_modes_settings()
    text = menu_text(
        "Режимы",
        "Как бот себя ведёт.",
        quote("Нажмите на режим, чтобы включить или выключить его."),
    )
    await callback.message.edit_text(text=text, reply_markup=build_settings_modes_kb(modes_state))
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_modes_toggle"), flags={"popup": True})
async def toggle_mode_setting(
    callback: CallbackQuery,
    callback_data: AdminPanelCallback,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    keys = list(MODES_TITLES.keys())
    index = callback_data.page

    if not 1 <= index <= len(keys):
        await callback.answer("Неизвестная настройка", show_alert=True)
        return

    key = keys[index - 1]
    if key in SINGLE_SUBSCRIPTION_TITLES:
        await open_single_subscription_settings(callback, state)
        return

    config = {**DEFAULT_MODES_CONFIG, **MODES_CONFIG}
    config[key] = not bool(config.get(key, False))

    await update_modes_config(session, config)

    modes_state = {k: bool(config.get(k, False)) for k in MODES_TITLES.keys()}
    await callback.message.edit_reply_markup(reply_markup=build_settings_modes_kb(modes_state))
    await callback.answer(menu_text("Режимы", "Настройка обновлена"))


async def show_single_subscription_settings(callback: CallbackQuery) -> None:
    """Показывает настройки режима одной подписки."""
    await callback.message.edit_text(
        text=menu_text(MODES_TITLES["SINGLE_SUBSCRIPTION_MODE"], quote(SINGLE_SUBSCRIPTION_SCREEN_HINT)),
        reply_markup=build_settings_single_subscription_kb(await load_modes_settings()),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_single_sub"))
async def open_single_subscription_settings(callback: CallbackQuery, state: FSMContext) -> None:
    """Открывает подменю режима одной подписки."""
    await state.clear()
    await show_single_subscription_settings(callback)
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_single_toggle"), flags={"popup": True})
async def toggle_single_subscription_setting(
    callback: CallbackQuery,
    callback_data: AdminPanelCallback,
    session: AsyncSession,
) -> None:
    """Переключает выбранную настройку одной подписки."""
    keys = list(SINGLE_SUBSCRIPTION_TITLES)
    index = callback_data.page
    if not 1 <= index <= len(keys):
        await callback.answer(SETTING_INVALID_VALUE, show_alert=True)
        return
    key = keys[index - 1]
    config = {**DEFAULT_MODES_CONFIG, **MODES_CONFIG}
    config[key] = not bool(config.get(key, False))
    await update_modes_config(session, config)
    await show_single_subscription_settings(callback)
    await callback.answer(SETTING_UPDATED)

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import MODES_CONFIG, update_modes_config
from core.defaults import DEFAULT_MODES_CONFIG
from filters.admin import IsAdminFilter
from settings.texts import SINGLE_SUBSCRIPTION_SCREEN_HINT

from ..panel.headers import menu_text, quote
from ..panel.keyboard import AdminPanelCallback
from .keyboard import MODES_TITLES, build_settings_modes_kb


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
        quote(SINGLE_SUBSCRIPTION_SCREEN_HINT),
    )
    await callback.message.edit_text(text=text, reply_markup=build_settings_modes_kb(modes_state))
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_modes_toggle"), flags={"popup": True})
async def toggle_mode_setting(
    callback: CallbackQuery,
    callback_data: AdminPanelCallback,
    session: AsyncSession,
) -> None:
    keys = list(MODES_TITLES.keys())
    index = callback_data.page

    if not 1 <= index <= len(keys):
        await callback.answer("Неизвестная настройка", show_alert=True)
        return

    key = keys[index - 1]

    config = {**DEFAULT_MODES_CONFIG, **MODES_CONFIG}
    config[key] = not bool(config.get(key, False))

    await update_modes_config(session, config)

    modes_state = {k: bool(config.get(k, False)) for k in MODES_TITLES.keys()}
    await callback.message.edit_reply_markup(reply_markup=build_settings_modes_kb(modes_state))
    await callback.answer(menu_text("Режимы", "Настройка обновлена"))

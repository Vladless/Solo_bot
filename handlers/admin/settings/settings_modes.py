from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import MODES_CONFIG, update_modes_config
from filters.admin import IsAdminFilter

from ..panel.keyboard import AdminPanelCallback
from .keyboard import MODES_TITLES, build_settings_modes_kb


router = Router(name="admin_settings_modes")
router.callback_query.filter(IsAdminFilter())


async def load_modes_settings() -> dict[str, bool]:
    config = MODES_CONFIG or {}
    return {k: bool(config.get(k, False)) for k in MODES_TITLES.keys()}


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_modes"))
async def open_settings_modes_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    modes_state = await load_modes_settings()
    text = "Здесь вы можете включать и отключать режимы работы бота."
    await callback.message.edit_text(text=text, reply_markup=build_settings_modes_kb(modes_state))
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_modes_toggle"))
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

    config = {k: bool((MODES_CONFIG or {}).get(k, False)) for k in MODES_TITLES.keys()}
    config[key] = not config[key]

    await update_modes_config(session, config)

    modes_state = {k: bool(config.get(k, False)) for k in MODES_TITLES.keys()}
    await callback.message.edit_reply_markup(reply_markup=build_settings_modes_kb(modes_state))
    await callback.answer("Настройка обновлена")

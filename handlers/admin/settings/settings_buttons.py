from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import BUTTONS_CONFIG, update_buttons_config
from filters.admin import IsAdminFilter

from ..panel.keyboard import AdminPanelCallback
from .keyboard import BUTTON_TITLES, build_settings_buttons_kb


router = Router(name="admin_settings_buttons")
router.callback_query.filter(IsAdminFilter())


async def load_button_settings() -> dict[str, bool]:
    config = BUTTONS_CONFIG or {}
    return {k: bool(config.get(k, False)) for k in BUTTON_TITLES.keys()}


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_buttons"))
async def open_settings_buttons_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    buttons_state = await load_button_settings()
    text = "Здесь вы можете включать или отключать кнопки в меню бота."
    await callback.message.edit_text(text=text, reply_markup=build_settings_buttons_kb(buttons_state))
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_button_toggle"))
async def toggle_button_setting(
    callback: CallbackQuery,
    callback_data: AdminPanelCallback,
    session: AsyncSession,
) -> None:
    keys = list(BUTTON_TITLES.keys())
    idx = callback_data.page

    if not 1 <= idx <= len(keys):
        await callback.answer("Неизвестная настройка", show_alert=True)
        return

    key = keys[idx - 1]

    config = dict(BUTTONS_CONFIG or {})
    current = bool(config.get(key, False))
    config[key] = not current

    await update_buttons_config(session, config)
    await session.commit()

    buttons_state = {k: bool(config.get(k, False)) for k in BUTTON_TITLES.keys()}
    await callback.message.edit_reply_markup(reply_markup=build_settings_buttons_kb(buttons_state))
    await callback.answer("Настройка обновлена")

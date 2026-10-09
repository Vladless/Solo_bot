from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import MANAGEMENT_CONFIG, update_management_config
from filters.admin import IsAdminFilter, IsSuperAdminFilter, get_admin_context
from settings.buttons import BACK, SETTING_ADMIN_GRANULAR_PERMISSIONS
from settings.texts import ADMIN_ACCESS_SETTINGS_TITLE, ADMIN_GRANULAR_PERMISSIONS_HINT, SETTING_UPDATED

from ..panel.headers import menu_text, quote
from ..panel.keyboard import AdminPanelCallback
from .keyboard import build_settings_kb


router = Router(name="admin_settings_manage")
router.callback_query.filter(IsAdminFilter())


@router.callback_query(AdminPanelCallback.filter(F.action == "settings"))
async def open_settings_menu(callback: CallbackQuery) -> None:
    text = menu_text(
        "Настройки",
        "Меняются на лету, перезапуск боту не нужен.",
        quote(
            "⚠️ Настройки технические. Не трогайте то, чего не понимаете: "
            "случайное переключение способно сломать работу бота или базы.",
            "Сомневаетесь — спросите в чате.",
        ),
    )
    _, is_super, _ = await get_admin_context(callback.from_user.id, fresh=True)
    await callback.message.edit_text(text=text, reply_markup=build_settings_kb(show_access=is_super))
    await callback.answer()


def access_settings_keyboard() -> InlineKeyboardMarkup:
    """Собирает переключатель детальных прав."""
    enabled = bool(MANAGEMENT_CONFIG.get("ADMIN_GRANULAR_PERMISSIONS_ENABLED", False))
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{'✅' if enabled else '❌'} {SETTING_ADMIN_GRANULAR_PERMISSIONS}",
                    callback_data=AdminPanelCallback(action="settings_access_toggle").pack(),
                )
            ],
            [InlineKeyboardButton(text=BACK, callback_data=AdminPanelCallback(action="settings").pack())],
        ]
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_access"), IsSuperAdminFilter())
async def open_access_settings(callback: CallbackQuery) -> None:
    """Открывает общий переключатель детальных прав."""
    await callback.message.edit_text(
        menu_text(ADMIN_ACCESS_SETTINGS_TITLE, quote(ADMIN_GRANULAR_PERMISSIONS_HINT)),
        reply_markup=access_settings_keyboard(),
    )
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_access_toggle"), IsSuperAdminFilter())
async def toggle_access_settings(callback: CallbackQuery, session: AsyncSession) -> None:
    """Переключает применение детальных прав модераторов."""
    config = dict(MANAGEMENT_CONFIG)
    config["ADMIN_GRANULAR_PERMISSIONS_ENABLED"] = not bool(config.get("ADMIN_GRANULAR_PERMISSIONS_ENABLED", False))
    await update_management_config(session, config)
    await callback.message.edit_reply_markup(reply_markup=access_settings_keyboard())
    await callback.answer(SETTING_UPDATED)

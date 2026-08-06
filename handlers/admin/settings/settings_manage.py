from aiogram import F, Router
from aiogram.types import CallbackQuery

from filters.admin import IsAdminFilter

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
    await callback.message.edit_text(text=text, reply_markup=build_settings_kb())
    await callback.answer()

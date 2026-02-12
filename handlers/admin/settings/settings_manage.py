from aiogram import F, Router
from aiogram.types import CallbackQuery

from filters.admin import IsAdminFilter

from ..panel.keyboard import AdminPanelCallback
from .keyboard import build_settings_kb


router = Router(name="admin_settings_manage")
router.callback_query.filter(IsAdminFilter())


@router.callback_query(AdminPanelCallback.filter(F.action == "settings"))
async def open_settings_menu(callback: CallbackQuery) -> None:
    text = (
        "Здесь вы можете изменить основные настройки бота, не перезагружая его\n"
        "(Меню будет пополняться)\n\n"
        "<blockquote>"
        "⚠️⚠️⚠️ ВАЖНО! Эти настройки являются техническими и не рассчитаны на обычное использование.\n"
        "Не включайте и не меняйте настройки, если вы не понимаете, что они делают!\n"
        "Бездумные изменения могут нарушить работу бота или базы данных."
        "</blockquote>\n\n"
        "Если вы не уверены, что делает настройка — уточните вопрос в чате."
    )
    await callback.message.edit_text(text=text, reply_markup=build_settings_kb())
    await callback.answer()

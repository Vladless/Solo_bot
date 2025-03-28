from aiogram import F, Router
from aiogram.types import CallbackQuery

from backup import backup_database
from filters.admin import IsAdminFilter

from ..panel.keyboard import AdminPanelCallback, build_admin_back_kb


router = Router()


@router.callback_query(
    AdminPanelCallback.filter(F.action == "backups"),
    IsAdminFilter(),
)
async def handle_backups(callback_query: CallbackQuery):
    kb = build_admin_back_kb("management")

    await callback_query.message.edit_text(
        text="💾 Инициализация резервного копирования базы данных...", reply_markup=kb
    )

    exception = await backup_database()

    if exception:
        text = f"❌ Ошибка при создании резервной копии: {exception}"
    else:
        text = "✅ Резервная копия успешно создана и отправлена администраторам."

    await callback_query.message.edit_text(text=text, reply_markup=kb)

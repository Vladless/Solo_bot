from aiogram import F, Router
from aiogram.types import CallbackQuery

from backup import backup_database
from filters.admin import IsAdminFilter
from keyboards.admin.panel_kb import AdminPanelCallback

router = Router()


@router.callback_query(
    AdminPanelCallback.filter(F.action == "backups"),
    IsAdminFilter(),
)
async def handle_backup(callback_query: CallbackQuery):
    await callback_query.message.answer(
        text="💾 Инициализация резервного копирования базы данных..."
    )
    await backup_database()
    await callback_query.message.answer(
        text="✅ Резервная копия успешно создана и отправлена администратору."
    )

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from config import ADMIN_ID

router = Router()

@router.message(Command('backup'))
async def backup_command(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("У вас нет прав для выполнения этой команды.")
        return

    from backup import backup_database
    await message.answer("Запускаю бэкап базы данных...")
    await backup_database()
    await message.answer("Бэкап завершен и отправлен админу.")
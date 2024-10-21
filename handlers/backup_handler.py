from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router()

@router.message(Command('backup'))
async def backup_command(message: Message):
    from backup import backup_database 
    await message.answer("Запускаю бэкап базы данных...")
    await backup_database()
    await message.answer("Бэкап завершен и отправлен админу.")

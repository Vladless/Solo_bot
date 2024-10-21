from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from backup import backup_database

router = Router()

@router.message(Command('backup'))
async def backup_command(message: Message):
    await message.answer("Запускаю бэкап базы данных...")
    await backup_database()  # Запуск функции бэкапа
    await message.answer("Бэкап завершен и отправлен админу.")


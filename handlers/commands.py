from aiogram import Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from handlers.start import start_command

router = Router()


@router.message(Command("start"))
async def handle_start(message: types.Message, state: FSMContext):
    await start_command(message)


@router.message(Command("menu"))
async def handle_menu(message: types.Message, state: FSMContext):
    await start_command(message)

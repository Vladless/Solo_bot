from aiogram import Router, types
from aiogram.filters import Command
from database import add_balance_to_client, get_balance, check_connection_exists  # Импорт необходимых функций
from config import ADMIN_ID

router = Router()

@router.message(Command('add_balance'))
async def cmd_add_balance(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("У вас нет доступа к этой команде.")
        return
    
    try:
        _, client_id, amount = message.text.split()
        amount = float(amount)

        if not await check_connection_exists(int(client_id)):
            await message.reply(f"Клиент с ID {client_id} не найден.")
            return

        await add_balance_to_client(int(client_id), amount)
        await message.reply(f"Баланс клиента {client_id} увеличен на {amount} у.е.")
    except ValueError:
        await message.reply("Пожалуйста, используйте формат: /add_balance <client_id> <amount>")
    except Exception as e:
        await message.reply(f"Произошла ошибка: {e}")

@router.message(Command('check_balance'))
async def cmd_check_balance(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("У вас нет доступа к этой команде.")
        return

    try:
        _, client_id = message.text.split()

        if not await check_connection_exists(int(client_id)):
            await message.reply(f"Клиент с ID {client_id} не найден.")
            return

        balance = await get_balance(int(client_id)) 
        await message.reply(f"Баланс клиента {client_id}: {balance} у.е.")
    except ValueError:
        await message.reply("Пожалуйста, используйте формат: /check_balance <client_id>")
    except Exception as e:
        await message.reply(f"Произошла ошибка: {e}")

from aiogram import Router, types
from aiogram.filters import Command

from database import add_balance_to_client, check_connection_exists

router = Router()


@router.message(Command("add_balance"))
async def cmd_add_balance(message: types.Message, is_admin: bool):
    if is_admin:
        try:
            _, client_id, amount = message.text.split()
            amount = float(amount)

            if not await check_connection_exists(int(client_id)):
                await message.reply(f"Клиент с ID {client_id} не найден.")
                return

            await add_balance_to_client(int(client_id), amount)
            await message.reply(f"Баланс клиента {client_id} увеличен на {amount} у.е.")
        except ValueError:
            await message.reply(
                "Пожалуйста, используйте формат: /add_balance <client_id> <amount>"
            )
        except Exception as e:
            await message.reply(f"Произошла ошибка: {e}")

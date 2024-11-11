from aiogram import Router, types
from aiogram.filters import Command
from filters.admin import IsAdminFilter

from database import add_balance_to_client, check_connection_exists

router = Router()


@router.message(Command("add_balance"), IsAdminFilter())
async def cmd_add_balance(message: types.Message):
    try:
        _, client_id, amount = message.text.split()
        amount = float(amount)

        if not await check_connection_exists(int(client_id)):
            await message.reply(f"❌ Клиент с ID {client_id} не найден в базе данных.")
            return

        await add_balance_to_client(int(client_id), amount)
        await message.reply(
            f"✅ Баланс клиента {client_id} успешно пополнен на {amount}"
        )
    except ValueError:
        await message.reply(
            "❓ Неверный формат команды!\n"
            "Пожалуйста, используйте следующий шаблон:\n"
            "/add_balance <ID клиента> <сумма пополнения>"
        )
    except Exception as e:
        await message.reply(f"🚨 Произошла непредвиденная ошибка: {e}")

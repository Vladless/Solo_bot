from datetime import datetime

import asyncpg
from aiogram import Router, types
from aiogram.filters import Command
from loguru import logger

from auth import login_with_credentials
from client import extend_client_key_admin
from config import ADMIN_PASSWORD, ADMIN_USERNAME, DATABASE_URL
from database import add_balance_to_client, check_connection_exists, get_client_id_by_email, get_tg_id_by_client_id, update_key_expiry

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


@router.message(Command("update_key_expiry"))
async def cmd_update_key_expiry(message: types.Message, is_admin: bool):
    if is_admin:
        try:
            parts = message.text.split(maxsplit=2)
            if len(parts) != 3:
                await message.reply(
                    "Пожалуйста, используйте формат: /update_key_expiry <email> <expiry_time(YYYY-MM-DD HH:MM:SS)>"
                )
                return

            _, email, expiry_time_str = parts
            expiry_time = int(
                datetime.strptime(expiry_time_str, "%Y-%m-%d %H:%M:%S").timestamp()
                * 1000
            )

            client_id = await get_client_id_by_email(email)
            if client_id is None:
                await message.reply(f"Клиент с email {email} не найден.")
                return

            await update_key_expiry(client_id, expiry_time)

            conn = await asyncpg.connect(DATABASE_URL)
            try:
                record = await conn.fetchrow(
                    "SELECT server_id FROM keys WHERE client_id = $1", client_id
                )
                if not record:
                    await message.reply("Клиент не найден в базе данных.")
                    return

                server_id = record["server_id"]
                tg_id = await get_tg_id_by_client_id(client_id)

                session = await login_with_credentials(
                    server_id, ADMIN_USERNAME, ADMIN_PASSWORD
                )

                logger.info(
                    f"Попытка обновить панель для server_id: {server_id}, tg_id: {tg_id}, client_id: {client_id}, email: {email}, expiryTime: {expiry_time}"
                )

                success = await extend_client_key_admin(
                    session, server_id, tg_id, client_id, email, expiry_time
                )

                logger.info(
                    f"Статус обновления панели: {'Успешно' if success else 'Не удалось'}"
                )
                if success:
                    await message.reply(
                        f"Время истечения ключа для клиента {client_id} ({email}) обновлено и синхронизировано с панелью."
                    )
                else:
                    await message.reply(
                        f"Время истечения ключа для клиента {client_id} ({email}) обновлено, но не удалось синхронизировать с панелью."
                    )

            finally:
                await conn.close()
        except ValueError:
            await message.reply(
                "Пожалуйста, используйте формат: /update_key_expiry <email> <expiry_time(YYYY-MM-DD HH:MM:SS)>"
            )
        except Exception as e:
            await message.reply(f"Произошла ошибка: {e}")

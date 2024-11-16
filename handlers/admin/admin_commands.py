import asyncpg
from aiogram import Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot import bot
from config import DATABASE_URL
from database import add_balance_to_client, check_connection_exists
from filters.admin import IsAdminFilter
from handlers.texts import TRIAL
from logger import logger

router = Router()


class Form(StatesGroup):
    waiting_for_server_selection = State()
    waiting_for_key_name = State()
    viewing_profile = State()
    waiting_for_message = State()


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


@router.message(Command("backup"), IsAdminFilter())
async def backup_command(message: types.Message):
    from backup import backup_database

    await message.answer("🔄 Инициализация резервного копирования базы данных...")
    await backup_database()
    await message.answer(
        "✅ Бэкап базы данных успешно завершен и отправлен администратору."
    )


@router.message(Command("send_trial"), IsAdminFilter())
async def handle_send_trial_command(message: types.Message, state: FSMContext):
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            records = await conn.fetch(
                """
                SELECT tg_id FROM connections WHERE trial = 0
            """
            )

            if records:
                success_count = 0
                error_count = 0
                blocked_count = 0

                for record in records:
                    tg_id = record["tg_id"]
                    trial_message = TRIAL
                    try:
                        await bot.send_message(chat_id=tg_id, text=trial_message)
                        success_count += 1
                    except Exception as e:
                        if "Forbidden: bot was blocked by the user" in str(e):
                            blocked_count += 1
                            logger.info(
                                f"🚫 Бот заблокирован пользователем с tg_id: {tg_id}"
                            )
                        else:
                            error_count += 1
                            logger.error(
                                f"❌ Ошибка при отправке сообщения пользователю {tg_id}: {e}"
                            )

                await message.answer(
                    f"📊 Результаты рассылки пробных периодов:\n"
                    f"✅ Успешно отправлено: {success_count}\n"
                    f"🚫 Заблокировано: {blocked_count}\n"
                    f"❌ Ошибок: {error_count}"
                )
            else:
                await message.answer(
                    "📭 Нет пользователей с неиспользованными пробными ключами."
                )

        finally:
            await conn.close()

    except Exception as e:
        await message.answer(f"❗ Ошибка при отправке сообщений: {e}")


@router.message(Command("send_to_all"), IsAdminFilter())
async def send_message_to_all_clients(
    message: types.Message, state: FSMContext, from_panel=False
):
    if from_panel:
        await message.answer(
            "✍️ Введите текст сообщения, который вы хотите отправить всем клиентам:"
        )
        await state.set_state(Form.waiting_for_message)


@router.message(Form.waiting_for_message, IsAdminFilter())
async def process_message_to_all(
    message: types.Message,
    state: FSMContext,
):
    text_message = message.text

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        tg_ids = await conn.fetch("SELECT tg_id FROM connections")

        total_users = len(tg_ids)
        success_count = 0
        error_count = 0

        for record in tg_ids:
            tg_id = record["tg_id"]
            try:
                await bot.send_message(chat_id=tg_id, text=text_message)
                success_count += 1
            except Exception as e:
                error_count += 1
                logger.error(
                    f"❌ Ошибка при отправке сообщения пользователю {tg_id}: {e}"
                )

        await message.answer(
            f"📤 Рассылка завершена:\n"
            f"👥 Всего пользователей: {total_users}\n"
            f"✅ Успешно отправлено: {success_count}\n"
            f"❌ Не доставлено: {error_count}"
        )
    except Exception as e:
        logger.error(f"❗ Ошибка при подключении к базе данных: {e}")
        await message.answer("❌ Произошла ошибка при отправке сообщения.")
    finally:
        await conn.close()

    await state.clear()

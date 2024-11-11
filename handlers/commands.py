import asyncpg
from aiogram import Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from filters.admin import IsAdminFilter
from loguru import logger

from bot import bot
from config import DATABASE_URL
from handlers.admin.admin import cmd_add_balance
from handlers.keys.key_management import handle_key_name_input
from handlers.payments.yookassa_pay import ReplenishBalanceState, process_custom_amount_input
from handlers.profile import process_callback_view_profile
from handlers.start import start_command
from handlers.texts import TRIAL

router = Router()


class Form(StatesGroup):
    waiting_for_server_selection = State()
    waiting_for_key_name = State()
    viewing_profile = State()
    waiting_for_message = State()


@router.message(Command("backup"), IsAdminFilter())
async def backup_command(message: Message):
    from backup import backup_database

    await message.answer("🔄 Инициализация резервного копирования базы данных...")
    await backup_database()
    await message.answer(
        "✅ Бэкап базы данных успешно завершен и отправлен администратору."
    )


@router.message(Command("start"))
async def handle_start(message: types.Message, state: FSMContext):
    await start_command(message)


@router.message(Command("add_balance"), IsAdminFilter())
async def handle_add_balance(message: types.Message, state: FSMContext):
    await cmd_add_balance(message)


@router.message(Command("menu"))
async def handle_menu(message: types.Message, state: FSMContext):
    await start_command(message)


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


@router.message()
async def handle_text(message: types.Message, state: FSMContext):
    current_state = await state.get_state()

    if message.text in ["/send_to_all"]:
        await send_message_to_all_clients(message, state)
        return

    if message.text == "Мой профиль":
        callback_query = types.CallbackQuery(
            id="1",
            from_user=message.from_user,
            chat_instance="",
            data="view_profile",
            message=message,
        )
        await process_callback_view_profile(callback_query, state)
        return

    if current_state == ReplenishBalanceState.entering_custom_amount.state:
        await process_custom_amount_input(message, state)
        return

    if current_state == Form.waiting_for_key_name.state:
        await handle_key_name_input(message, state)
        return

    if message.text == "/backup":
        await backup_command(message)
        return

    elif current_state is None:
        await start_command(message)

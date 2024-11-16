import asyncio
import uuid
from datetime import datetime, timedelta

import asyncpg
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import bot, dp
from config import CONNECT_ANDROID, CONNECT_IOS, DATABASE_URL, DOWNLOAD_ANDROID, DOWNLOAD_IOS, PUBLIC_LINK
from database import add_connection, get_balance, store_key, update_balance
from handlers.instructions.instructions import send_instructions
from handlers.keys.key_utils import create_key_on_cluster
from handlers.profile import process_callback_view_profile
from handlers.texts import KEY, KEY_TRIAL, NULL_BALANCE, RENEWAL_PLANS, key_message_success
from handlers.utils import get_least_loaded_cluster, sanitize_key_name
from logger import logger

router = Router()


class Form(StatesGroup):
    waiting_for_server_selection = State()
    waiting_for_key_name = State()
    viewing_profile = State()
    waiting_for_message = State()


@dp.callback_query(F.data == "create_key")
async def process_callback_create_key(callback_query: CallbackQuery, state: FSMContext):
    tg_id = callback_query.from_user.id

    try:
        await bot.delete_message(
            chat_id=tg_id, message_id=callback_query.message.message_id
        )
    except Exception:
        pass

    server_id = "все сервера"
    await state.update_data(selected_server_id=server_id)
    await select_server(callback_query, state)
    await callback_query.answer()


async def select_server(callback_query: CallbackQuery, state: FSMContext):

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        existing_connection = await conn.fetchrow(
            "SELECT trial FROM connections WHERE tg_id = $1",
            callback_query.from_user.id,
        )
    finally:
        await conn.close()

    trial_status = existing_connection["trial"] if existing_connection else 0

    if trial_status == 1:
        await bot.send_message(
            chat_id=callback_query.from_user.id,
            text=KEY,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ Да, подключить новое устройство",
                            callback_data="confirm_create_new_key",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="↩️ Назад", callback_data="cancel_create_key"
                        )
                    ],
                ]
            ),
        )
        await state.update_data(creating_new_key=True)
    else:
        await bot.send_message(
            chat_id=callback_query.from_user.id, text=KEY_TRIAL, parse_mode="HTML"
        )
        await state.set_state(Form.waiting_for_key_name)

    await callback_query.answer()


@dp.callback_query(F.data == "confirm_create_new_key")
async def confirm_create_new_key(callback_query: CallbackQuery, state: FSMContext):
    tg_id = callback_query.from_user.id

    logger.info(f"User {tg_id} confirmed creation of a new key.")

    balance = await get_balance(tg_id)
    if balance < RENEWAL_PLANS["1"]["price"]:
        replenish_button = InlineKeyboardButton(
            text="Перейти в профиль", callback_data="view_profile"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[replenish_button]])
        await callback_query.message.edit_text(NULL_BALANCE, reply_markup=keyboard)
        await state.clear()
        return

    logger.info(f"Balance for user {tg_id} is sufficient. Asking for device name.")

    await callback_query.message.edit_text(
        "🔑 Пожалуйста, введите имя подключаемого устройства:"
    )
    await state.set_state(Form.waiting_for_key_name)
    logger.info(f"State set to waiting_for_key_name for user {tg_id}")
    await state.update_data(creating_new_key=True)

    await callback_query.answer()


@dp.callback_query(F.data == "cancel_create_key")
async def cancel_create_key(callback_query: CallbackQuery, state: FSMContext):
    await process_callback_view_profile(callback_query, state)
    await callback_query.answer()


@router.message(Form.waiting_for_key_name)
async def handle_key_name_input(message: Message, state: FSMContext):
    tg_id = message.from_user.id
    key_name = sanitize_key_name(message.text)

    logger.info(f"User {tg_id} is attempting to create a key with the name: {key_name}")

    if not key_name:
        await message.bot.send_message(
            tg_id, "📝 Пожалуйста, назовите устройство на английском языке."
        )
        logger.warning(f"User {tg_id} entered an invalid key name: {key_name}")
        return

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        logger.info(
            f"Checking if key name '{key_name}' already exists in the database."
        )
        existing_key = await conn.fetchrow(
            "SELECT * FROM keys WHERE email = $1", key_name.lower()
        )
        if existing_key:
            await message.bot.send_message(
                tg_id,
                "❌ Упс! Это имя уже используется. Выберите другое уникальное название для ключа.",
            )
            logger.warning(
                f"Key name '{key_name}' already exists in the database for user {tg_id}."
            )
            await state.set_state(Form.waiting_for_key_name)
            return
    finally:
        await conn.close()

    client_id = str(uuid.uuid4())
    email = key_name.lower()
    current_time = datetime.utcnow()
    expiry_time = None

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        logger.info(f"Checking trial status for user {tg_id}.")
        existing_connection = await conn.fetchrow(
            "SELECT trial FROM connections WHERE tg_id = $1", tg_id
        )
    finally:
        await conn.close()

    trial_status = existing_connection["trial"] if existing_connection else 0

    if trial_status == 0:
        expiry_time = current_time + timedelta(days=1, hours=3)
        logger.info(f"Assigned 1-day trial to user {tg_id}.")
    else:
        balance = await get_balance(tg_id)
        if balance < RENEWAL_PLANS["1"]["price"]:
            replenish_button = InlineKeyboardButton(
                text="Перейти в профиль", callback_data="view_profile"
            )
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[replenish_button]])
            await message.bot.send_message(
                tg_id,
                "💳 Недостаточно средств для создания подписки на новое устройство. Пополните баланс в личном кабинете.",
                reply_markup=keyboard,
            )
            logger.warning(f"User {tg_id} has insufficient funds for key creation.")
            await state.clear()
            return

        await update_balance(tg_id, -RENEWAL_PLANS["1"]["price"])
        expiry_time = current_time + timedelta(days=30, hours=3)
        logger.info(f"User {tg_id} balance deducted for key creation.")

    expiry_timestamp = int(expiry_time.timestamp() * 1000)
    public_link = f"{PUBLIC_LINK}{email}"

    logger.info(f"Generated public link for the key: {public_link}")

    button_profile = InlineKeyboardButton(
        text="👤 Личный кабинет", callback_data="view_profile"
    )
    button_iphone = InlineKeyboardButton(
        text="🍏 Подключить", url=f"{CONNECT_IOS}{public_link}"
    )
    button_android = InlineKeyboardButton(
        text="🤖 Подключить",
        url=f"{CONNECT_ANDROID}{public_link}",
    )

    button_download_ios = InlineKeyboardButton(text="🍏 Скачать", url=DOWNLOAD_IOS)
    button_download_android = InlineKeyboardButton(
        text="🤖 Скачать",
        url=DOWNLOAD_ANDROID,
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [button_download_ios, button_download_android],
            [button_iphone, button_android],
            [button_profile],
        ]
    )

    remaining_time = expiry_time - current_time
    days = remaining_time.days
    key_message = key_message_success(public_link, f"⏳ Осталось дней: {days} 📅")

    logger.info(f"Sending key message to user {tg_id} with the public link.")

    await message.bot.send_message(
        tg_id, key_message, parse_mode="HTML", reply_markup=keyboard
    )

    try:
        least_loaded_cluster = await get_least_loaded_cluster()

        tasks = []
        tasks.append(
            asyncio.create_task(
                create_key_on_cluster(
                    least_loaded_cluster,
                    tg_id,
                    client_id,
                    email,
                    expiry_timestamp,
                )
            )
        )

        await asyncio.gather(*tasks)

        conn = await asyncpg.connect(DATABASE_URL)
        try:
            logger.info(f"Updating trial status for user {tg_id} in the database.")
            existing_connection = await conn.fetchrow(
                "SELECT * FROM connections WHERE tg_id = $1", tg_id
            )
            if existing_connection:
                await conn.execute(
                    "UPDATE connections SET trial = 1 WHERE tg_id = $1", tg_id
                )
            else:
                await add_connection(tg_id, 0, 1)
        finally:
            await conn.close()

        logger.info(f"Storing key for user {tg_id} in the database.")
        await store_key(
            tg_id, client_id, email, expiry_timestamp, public_link, least_loaded_cluster
        )

    except Exception as e:
        logger.error(f"Error while creating the key for user {tg_id}: {e}")
        await message.bot.send_message(tg_id, f"❌ Ошибка при создании ключа: {e}")

    await state.clear()


@dp.callback_query(F.data == "instructions")
async def handle_instructions(callback_query: CallbackQuery):
    await send_instructions(callback_query)


@dp.callback_query(F.data == "back_to_main")
async def handle_back_to_main(callback_query: CallbackQuery, state: FSMContext):
    await process_callback_view_profile(callback_query, state)
    await callback_query.answer()

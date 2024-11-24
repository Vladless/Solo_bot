import asyncio
from datetime import datetime, timedelta
from typing import Any
import uuid

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import CONNECT_ANDROID, CONNECT_IOS, DOWNLOAD_ANDROID, DOWNLOAD_IOS, PUBLIC_LINK, SUPPORT_CHAT_URL
from database import add_connection, get_balance, store_key, update_balance
from handlers.keys.key_utils import create_key_on_cluster
from handlers.texts import KEY, KEY_TRIAL, NULL_BALANCE, RENEWAL_PLANS, key_message_success
from handlers.utils import get_least_loaded_cluster, sanitize_key_name
from logger import logger

router = Router()


class Form(StatesGroup):
    waiting_for_server_selection = State()
    waiting_for_key_name = State()
    viewing_profile = State()
    waiting_for_message = State()


@router.callback_query(F.data == "create_key")
async def process_callback_create_key(callback_query: CallbackQuery, state: FSMContext, session: Any):
    server_id = "все сервера"
    await state.update_data(selected_server_id=server_id)
    await select_server(callback_query, state, session)


async def select_server(callback_query: CallbackQuery, state: FSMContext, session: Any):
    existing_connection = await session.fetchrow(
        "SELECT trial FROM connections WHERE tg_id = $1",
        callback_query.from_user.id,
    )

    trial_status = existing_connection["trial"] if existing_connection else 0

    if trial_status == 1:
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="✅ Да, подключить новое устройство", callback_data="confirm_create_new_key")
        )
        builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))

        await callback_query.message.answer(
            text=KEY,
            reply_markup=builder.as_markup(),
        )
        await state.update_data(creating_new_key=True)
    else:
        await callback_query.message.answer(KEY_TRIAL)
        await state.set_state(Form.waiting_for_key_name)


@router.callback_query(F.data == "confirm_create_new_key")
async def confirm_create_new_key(callback_query: CallbackQuery, state: FSMContext):
    tg_id = callback_query.from_user.id

    logger.info(f"User {tg_id} confirmed creation of a new key.")

    balance = await get_balance(tg_id)
    if balance < RENEWAL_PLANS["1"]["price"]:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))
        await callback_query.message.answer(NULL_BALANCE, reply_markup=builder.as_markup())
        await state.clear()
        return

    logger.info(f"Balance for user {tg_id} is sufficient. Asking for device name.")

    await callback_query.message.answer("🔑 Пожалуйста, введите имя подключаемого устройства:")
    await state.set_state(Form.waiting_for_key_name)
    logger.info(f"State set to waiting_for_key_name for user {tg_id}")
    await state.update_data(creating_new_key=True)


@router.message(Form.waiting_for_key_name)
async def handle_key_name_input(message: Message, state: FSMContext, session: Any):
    tg_id = message.from_user.id
    key_name = sanitize_key_name(message.text)

    logger.info(f"User {tg_id} is attempting to create a key with the name: {key_name}")

    if not key_name:
        await message.bot.send_message(tg_id, "📝 Пожалуйста, назовите устройство на английском языке.")
        logger.warning(f"User {tg_id} entered an invalid key name: {key_name}")
        return

    logger.info(f"Checking if key name '{key_name}' already exists for user {tg_id} in the database.")
    existing_key = await session.fetchrow(
        "SELECT * FROM keys WHERE email = $1 AND tg_id = $2",
        key_name.lower(),
        tg_id,
    )
    if existing_key:
        await message.answer(
            "❌ Упс! Это имя уже используется. Выберите другое уникальное название для ключа.",
        )
        logger.warning(f"Key name '{key_name}' already exists for user {tg_id}.")
        await state.set_state(Form.waiting_for_key_name)
        return

    client_id = str(uuid.uuid4())
    email = key_name.lower()
    current_time = datetime.utcnow()
    expiry_time = None

    logger.info(f"Checking trial status for user {tg_id}.")
    existing_connection = await session.fetchrow("SELECT trial FROM connections WHERE tg_id = $1", tg_id)

    trial_status = existing_connection["trial"] if existing_connection else 0

    if trial_status == 0:
        expiry_time = current_time + timedelta(days=1, hours=3)
        logger.info(f"Assigned 1-day trial to user {tg_id}.")
    else:
        balance = await get_balance(tg_id)
        if balance < RENEWAL_PLANS["1"]["price"]:
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))
            await message.answer(
                "💳 Недостаточно средств для создания подписки на новое устройство. Пополните баланс в личном кабинете.",
                reply_markup=builder.as_markup(),
            )
            logger.warning(f"User {tg_id} has insufficient funds for key creation.")
            await state.clear()
            return

        await update_balance(tg_id, -RENEWAL_PLANS["1"]["price"])
        expiry_time = current_time + timedelta(days=30, hours=3)
        logger.info(f"User {tg_id} balance deducted for key creation.")

    expiry_timestamp = int(expiry_time.timestamp() * 1000)
    public_link = f"{PUBLIC_LINK}{email}/{tg_id}"

    logger.info(f"Generated public link for the key: {public_link}")

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💬 Поддержка", url=SUPPORT_CHAT_URL))
    builder.row(
        InlineKeyboardButton(text="🍏 Скачать для iOS", url=DOWNLOAD_IOS),
        InlineKeyboardButton(text="🤖 Скачать для Android", url=DOWNLOAD_ANDROID),
    )
    builder.row(
        InlineKeyboardButton(text="🍏 Подключить на iOS", url=f"{CONNECT_IOS}{public_link}"),
        InlineKeyboardButton(text="🤖 Подключить на Android", url=f"{CONNECT_ANDROID}{public_link}"),
    )
    builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))

    remaining_time = expiry_time - current_time
    days = remaining_time.days
    key_message = key_message_success(public_link, f"⏳ Осталось дней: {days} 📅")

    logger.info(f"Sending key message to user {tg_id} with the public link.")

    await message.answer(key_message, reply_markup=builder.as_markup())

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

        logger.info(f"Updating trial status for user {tg_id} in the database.")
        existing_connection = await session.fetchrow("SELECT * FROM connections WHERE tg_id = $1", tg_id)
        if existing_connection:
            await session.execute("UPDATE connections SET trial = 1 WHERE tg_id = $1", tg_id)
        else:
            await add_connection(tg_id, 0, 1)

        logger.info(f"Storing key for user {tg_id} in the database.")
        await store_key(
            tg_id,
            client_id,
            email,
            expiry_timestamp,
            public_link,
            least_loaded_cluster,
        )

    except Exception as e:
        logger.error(f"Error while creating the key for user {tg_id}: {e}")
    await state.clear()

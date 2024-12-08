import asyncio
from datetime import datetime, timedelta
from typing import Any, Union
import uuid

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import (
    CONNECT_ANDROID,
    CONNECT_IOS,
    DOWNLOAD_ANDROID,
    DOWNLOAD_IOS,
    PUBLIC_LINK,
    RENEWAL_PRICES,
    SUPPORT_CHAT_URL,
    TRIAL_TIME,
)
from utils.database import get_balance, get_trial, store_key, update_balance
from handlers.keys.key_utils import create_key_on_cluster
from handlers.texts import DISCOUNTS, KEY, key_message_success
from handlers.utils import generate_random_email, get_least_loaded_cluster
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
    trial_status = await get_trial(callback_query.message.chat.id, session)
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
        await handle_key_creation(callback_query.message.chat.id, state, session, callback_query)


@router.callback_query(F.data == "confirm_create_new_key")
async def confirm_create_new_key(callback_query: CallbackQuery, state: FSMContext, session: Any):
    tg_id = callback_query.message.chat.id

    logger.info(f"User {tg_id} confirmed creation of a new key.")

    logger.info(f"Balance for user {tg_id} is sufficient. Proceeding with key creation.")

    await handle_key_creation(tg_id, state, session, callback_query)


async def handle_key_creation(
    tg_id: int, state: FSMContext, session: Any, message_or_query: Union[Message, CallbackQuery]
):
    """Создание ключа с учётом выбора тарифного плана."""
    current_time = datetime.utcnow()
    trial_status = await get_trial(tg_id, session)

    if trial_status == 0:
        expiry_time = current_time + timedelta(days=TRIAL_TIME)
        logger.info(f"Assigned 1-day trial to user {tg_id}.")

        await session.execute("UPDATE connections SET trial = 1 WHERE tg_id = $1", tg_id)
        await create_key(tg_id, expiry_time, state, session, message_or_query)
    else:
        builder = InlineKeyboardBuilder()

        for index, (plan_id, price) in enumerate(RENEWAL_PRICES.items()):
            discount_text = ""

            if plan_id in DISCOUNTS:
                discount_percentage = DISCOUNTS[plan_id]
                discount_text = f" ({discount_percentage}% скидка)"

                if index == len(RENEWAL_PRICES) - 1:
                    discount_text = f" ({discount_percentage}% 🔥)"

            builder.row(
                InlineKeyboardButton(
                    text=f"📅 {plan_id} мес. - {price}₽{discount_text}", callback_data=f"select_plan_{plan_id}"
                )
            )

        builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))

        await message_or_query.message.answer(
            "💳 Выберите тарифный план для создания нового ключа:", reply_markup=builder.as_markup()
        )
        await state.update_data(tg_id=tg_id)
        await state.set_state(Form.waiting_for_server_selection)


@router.callback_query(F.data.startswith("select_plan_"))
async def select_tariff_plan(callback_query: CallbackQuery, state: FSMContext, session: Any):
    tg_id = callback_query.message.chat.id
    plan_id = callback_query.data.split("_")[-1]
    plan_price = RENEWAL_PRICES.get(plan_id)

    if plan_price is None:
        await callback_query.message.answer("🚫 Неверный тарифный план.")
        return

    duration_days = int(plan_id) * 30

    balance = await get_balance(tg_id)
    if balance < plan_price:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="pay"))
        builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))
        await callback_query.message.answer(
            "💳 Недостаточно средств для создания подписки. Пополните баланс в личном кабинете.",
            reply_markup=builder.as_markup(),
        )
        await state.clear()
        return

    await update_balance(tg_id, -plan_price)

    expiry_time = datetime.utcnow() + timedelta(days=duration_days)

    await create_key(tg_id, expiry_time, state, session, callback_query)


async def create_key(
    tg_id: int, expiry_time: datetime, state: FSMContext, session: Any, message_or_query: Union[Message, CallbackQuery]
):
    """Создаёт ключ с заданным сроком действия."""
    while True:
        key_name = generate_random_email()
        logger.info(f"Generated random key name for user {tg_id}: {key_name}")

        existing_key = await session.fetchrow(
            "SELECT * FROM keys WHERE email = $1 AND tg_id = $2",
            key_name,
            tg_id,
        )
        if not existing_key:
            break
        logger.warning(f"Key name '{key_name}' already exists for user {tg_id}. Generating a new one.")

    client_id = str(uuid.uuid4())
    email = key_name.lower()
    expiry_timestamp = int(expiry_time.timestamp() * 1000)
    public_link = f"{PUBLIC_LINK}{email}/{tg_id}"

    try:
        least_loaded_cluster = await get_least_loaded_cluster()

        tasks = [
            asyncio.create_task(
                create_key_on_cluster(
                    least_loaded_cluster,
                    tg_id,
                    client_id,
                    email,
                    expiry_timestamp,
                )
            )
        ]

        await asyncio.gather(*tasks)
        logger.info(f"Key created on cluster {least_loaded_cluster} for user {tg_id}.")

        await store_key(tg_id, client_id, email, expiry_timestamp, public_link, least_loaded_cluster, session)

    except Exception as e:
        logger.error(f"Error while creating the key for user {tg_id} on cluster: {e}")
        await message_or_query.message.answer("❌ Произошла ошибка при создании ключа. Пожалуйста, попробуйте снова.")
        return

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
    builder.row(InlineKeyboardButton(text="💻 Windows/Linux", callback_data=f"connect_pc|{email}"))
    builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))

    remaining_time = expiry_time - datetime.utcnow()
    days = remaining_time.days
    key_message = key_message_success(public_link, f"⏳ Осталось дней: {days} 📅")

    await message_or_query.message.answer(key_message, reply_markup=builder.as_markup())
    await state.clear()

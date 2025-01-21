import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Any

import pytz
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import bot
from config import (
    CONNECT_ANDROID,
    CONNECT_IOS,
    DOWNLOAD_ANDROID,
    DOWNLOAD_IOS,
    PUBLIC_LINK,
    RENEWAL_PRICES,
    SUPPORT_CHAT_URL,
    TRIAL_TIME,
    USE_COUNTRY_SELECTION,
    USE_NEW_PAYMENT_FLOW,
)
from database import (
    get_balance,
    get_trial,
    save_temporary_data,
    store_key,
    update_balance,
)
from handlers.buttons.add_subscribe import (
    DOWNLOAD_ANDROID_BUTTON,
    DOWNLOAD_IOS_BUTTON,
    IMPORT_ANDROID,
    IMPORT_IOS,
    PC_BUTTON,
    TV_BUTTON,
)
from handlers.keys.key_utils import create_client_on_server, create_key_on_cluster
from handlers.payments.robokassa_pay import handle_custom_amount_input
from handlers.payments.yookassa_pay import process_custom_amount_input
from handlers.texts import DISCOUNTS, key_message_success
from handlers.utils import generate_random_email, get_least_loaded_cluster
from logger import logger

router = Router()


class Form(StatesGroup):
    waiting_for_server_selection = State()
    waiting_for_key_name = State()
    viewing_profile = State()
    waiting_for_message = State()


@router.callback_query(F.data == "create_key")
async def confirm_create_new_key(
    callback_query: CallbackQuery, state: FSMContext, session: Any
):
    tg_id = callback_query.message.chat.id

    logger.info(f"User {tg_id} confirmed creation of a new key.")

    logger.info(
        f"Balance for user {tg_id} is sufficient. Proceeding with key creation."
    )

    await handle_key_creation(tg_id, state, session, callback_query)


async def handle_key_creation(
    tg_id: int,
    state: FSMContext,
    session: Any,
    message_or_query: Message | CallbackQuery,
):
    """Создание ключа с учётом выбора тарифного плана."""
    current_time = datetime.utcnow()
    trial_status = await get_trial(tg_id, session)

    if trial_status == 0:
        expiry_time = current_time + timedelta(days=TRIAL_TIME)
        logger.info(f"Assigned 1-day trial to user {tg_id}.")

        await session.execute(
            "UPDATE connections SET trial = 1 WHERE tg_id = $1", tg_id
        )
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
                    text=f"📅 {plan_id} мес. - {price}₽{discount_text}",
                    callback_data=f"select_plan_{plan_id}",
                )
            )

        builder.row(
            InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile")
        )

        await message_or_query.message.answer(
            "💳 Выберите тарифный план для создания нового ключа:",
            reply_markup=builder.as_markup(),
        )
        await state.update_data(tg_id=tg_id)
        await state.set_state(Form.waiting_for_server_selection)


@router.callback_query(F.data.startswith("select_plan_"))
async def select_tariff_plan(callback_query: CallbackQuery, session: Any):
    tg_id = callback_query.message.chat.id
    plan_id = callback_query.data.split("_")[-1]
    plan_price = RENEWAL_PRICES.get(plan_id)

    if plan_price is None:
        await callback_query.message.answer("🚫 Неверный тарифный план.")
        return

    duration_days = int(plan_id) * 30
    balance = await get_balance(tg_id)

    await save_temporary_data(
        session,
        tg_id,
        "waiting_for_payment",
        {
            "plan_id": plan_id,
            "plan_price": plan_price,
            "duration_days": duration_days,
            "required_amount": max(0, plan_price - balance),
        },
    )

    if balance < plan_price:
        required_amount = plan_price - balance

        if USE_NEW_PAYMENT_FLOW == "YOOKASSA":
            await process_custom_amount_input(callback_query, session)
        elif USE_NEW_PAYMENT_FLOW == "ROBOKASSA":
            await handle_custom_amount_input(callback_query, session)
        else:
            builder = InlineKeyboardBuilder()
            builder.row(
                InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="pay")
            )
            builder.row(
                InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile")
            )

            await callback_query.message.answer(
                f"💳 Недостаточно средств. Для продолжения необходимо пополнить баланс на {required_amount}₽.",
                reply_markup=builder.as_markup(),
            )
        return

    expiry_time = datetime.utcnow() + timedelta(days=duration_days)
    await create_key(tg_id, expiry_time, None, session, callback_query)
    await update_balance(tg_id, -plan_price)


async def create_key(
    tg_id: int,
    expiry_time: datetime,
    state: FSMContext | None,
    session: Any,
    message_or_query: Message | CallbackQuery | None = None,
):
    """Создаёт ключ с заданным сроком действия."""
    moscow_tz = pytz.timezone("Europe/Moscow")
    expiry_time = expiry_time.astimezone(moscow_tz)

    if USE_COUNTRY_SELECTION:
        logger.info("[Country Selection] USE_COUNTRY_SELECTION включен.")

        logger.info("[Country Selection] Получение наименее загруженного кластера.")
        least_loaded_cluster = await get_least_loaded_cluster()
        logger.info(f"[Country Selection] Наименее загруженный кластер: {least_loaded_cluster}")

        logger.info(f"[Country Selection] Получение списка серверов для кластера {least_loaded_cluster}.")
        servers = await session.fetch(
            "SELECT server_name FROM servers WHERE cluster_name = $1",
            least_loaded_cluster,
        )
        countries = [server["server_name"] for server in servers]
        logger.info(f"[Country Selection] Список серверов: {countries}")

        builder = InlineKeyboardBuilder()
        for country in countries:
            callback_data = f"select_country|{country}|{expiry_time.isoformat()}"
            builder.row(
                InlineKeyboardButton(
                    text=country, callback_data=callback_data
                )
            )
            logger.info(f"[Country Selection] Добавлена кнопка для страны: {country} с callback_data: {callback_data}")

        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="profile"))
        logger.info("[Country Selection] Добавлена кнопка '🔙 Назад'.")

        if isinstance(message_or_query, Message):
            logger.info("[Country Selection] Сообщение пользователя - тип Message.")
            await message_or_query.answer(
                "🌍 Пожалуйста, выберите страну для вашего ключа:",
                reply_markup=builder.as_markup(),
            )
            logger.info("[Country Selection] Сообщение отправлено с выбором страны.")
        elif isinstance(message_or_query, CallbackQuery):
            logger.info("[Country Selection] Сообщение пользователя - тип CallbackQuery.")
            await message_or_query.message.answer(
                "🌍 Пожалуйста, выберите страну для вашего ключа:",
                reply_markup=builder.as_markup(),
            )
            logger.info("[Country Selection] Сообщение отправлено с выбором страны.")
        elif tg_id is not None:
            logger.info("[Country Selection] Использование tg_id для отправки сообщения.")
            await bot.send_message(
                chat_id=tg_id,
                text="🌍 Пожалуйста, выберите страну для вашего ключа:",
                reply_markup=builder.as_markup(),
            )
            logger.info(f"[Country Selection] Сообщение отправлено напрямую в чат {tg_id}.")
        else:
            logger.error("[Country Selection] Невозможно определить идентификатор чата. Сообщение не отправлено.")

        logger.info("[Country Selection] Возврат из функции.")
        return

    while True:
        key_name = generate_random_email()
        logger.info(f"[Key Generation] Сгенерировано имя ключа: {key_name} для пользователя {tg_id}")

        existing_key = await session.fetchrow(
            "SELECT * FROM keys WHERE email = $1 AND tg_id = $2",
            key_name,
            tg_id,
        )
        if not existing_key:
            break
        logger.warning(f"[Key Generation] Имя ключа {key_name} уже существует. Генерация нового.")

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
        logger.info(f"[Key Creation] Ключ создан на кластере {least_loaded_cluster} для пользователя {tg_id}")

        await store_key(
            tg_id,
            client_id,
            email,
            expiry_timestamp,
            public_link,
            least_loaded_cluster,
            session,
        )
        logger.info(f"[Database] Ключ сохранён в базе данных для пользователя {tg_id}")

    except Exception as e:
        logger.error(f"[Error] Ошибка при создании ключа для пользователя {tg_id}: {e}")

        error_message = "❌ Произошла ошибка при создании подписки. Пожалуйста, попробуйте снова."
        if isinstance(message_or_query, Message):
            await message_or_query.answer(error_message)
        elif isinstance(message_or_query, CallbackQuery):
            await message_or_query.message.answer(error_message)
        else:
            await bot.send_message(chat_id=tg_id, text=error_message)
        return

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💬 Поддержка", url=SUPPORT_CHAT_URL))
    builder.row(
        InlineKeyboardButton(text=DOWNLOAD_IOS_BUTTON, url=DOWNLOAD_IOS),
        InlineKeyboardButton(text=DOWNLOAD_ANDROID_BUTTON, url=DOWNLOAD_ANDROID),
    )
    builder.row(
        InlineKeyboardButton(text=IMPORT_IOS, url=f"{CONNECT_IOS}{public_link}"),
        InlineKeyboardButton(text=IMPORT_ANDROID, url=f"{CONNECT_ANDROID}{public_link}"),
    )
    builder.row(
        InlineKeyboardButton(text=PC_BUTTON, callback_data=f"connect_pc|{email}"),
        InlineKeyboardButton(text=TV_BUTTON, callback_data=f"connect_tv|{email}"),
    )
    builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))

    remaining_time = expiry_time - datetime.now(moscow_tz)
    days = remaining_time.days
    key_message = key_message_success(public_link, f"⏳ Осталось дней: {days} 📅")

    if isinstance(message_or_query, Message):
        await message_or_query.answer(key_message, reply_markup=builder.as_markup())
    elif isinstance(message_or_query, CallbackQuery):
        await message_or_query.message.answer(key_message, reply_markup=builder.as_markup())
    else:
        await bot.send_message(chat_id=tg_id, text=key_message, reply_markup=builder.as_markup())

    if state:
        await state.clear()
        logger.info(f"[FSM] Состояние пользователя {tg_id} очищено")


@router.callback_query(F.data.startswith("select_country|"))
async def handle_country_selection(callback_query: CallbackQuery, session: Any):
    """Обработчик выбора страны."""
    data = callback_query.data.split("|")
    selected_country = data[1]
    expiry_time_str = data[2]

    tg_id = callback_query.from_user.id

    logger.info(f"Пользователь {tg_id} выбрал страну: {selected_country}")
    logger.info(f"Получено время истечения: {expiry_time_str}")

    try:
        expiry_time = datetime.fromisoformat(expiry_time_str)
    except ValueError:
        logger.error(f"Ошибка преобразования времени истечения: {expiry_time_str}")
        await callback_query.message.answer("❌ Некорректное время истечения. Попробуйте снова.")
        return

    await finalize_key_creation(tg_id, expiry_time, selected_country, None, session, callback_query)


async def finalize_key_creation(
    tg_id: int,
    expiry_time: datetime,
    selected_country: str,
    state: FSMContext | None,
    session: Any,
    callback_query: CallbackQuery,
):
    """Финализирует создание ключа с выбранной страной."""
    moscow_tz = pytz.timezone("Europe/Moscow")
    expiry_time = expiry_time.astimezone(moscow_tz)

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
        logger.warning(
            f"Key name '{key_name}' already exists for user {tg_id}. Generating a new one."
        )

    client_id = str(uuid.uuid4())
    email = key_name.lower()
    expiry_timestamp = int(expiry_time.timestamp() * 1000)
    public_link = f"{PUBLIC_LINK}{email}/{tg_id}"

    try:
        server_info = await session.fetchrow(
            "SELECT api_url, inbound_id, server_name FROM servers WHERE server_name = $1",
            selected_country,
        )

        if not server_info:
            raise ValueError(f"Сервер {selected_country} не найден.")

        semaphore = asyncio.Semaphore(2)

        await create_client_on_server(
            server_info=server_info,
            tg_id=tg_id,
            client_id=client_id,
            email=email,
            expiry_timestamp=expiry_timestamp,
            semaphore=semaphore,
        )

        logger.info(f"Key created on server {selected_country} for user {tg_id}.")

        await store_key(
            tg_id,
            client_id,
            email,
            expiry_timestamp,
            public_link,
            selected_country,
            session,
        )

    except Exception as e:
        logger.error(f"Error while creating the key for user {tg_id}: {e}")
        await callback_query.message.answer("❌ Произошла ошибка при создании подписки. Пожалуйста, попробуйте снова.")
        return

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💬 Поддержка", url=SUPPORT_CHAT_URL))
    builder.row(
        InlineKeyboardButton(text=DOWNLOAD_IOS_BUTTON, url=DOWNLOAD_IOS),
        InlineKeyboardButton(text=DOWNLOAD_ANDROID_BUTTON, url=DOWNLOAD_ANDROID),
    )
    builder.row(
        InlineKeyboardButton(text=IMPORT_IOS, url=f"{CONNECT_IOS}{public_link}"),
        InlineKeyboardButton(text=IMPORT_ANDROID, url=f"{CONNECT_ANDROID}{public_link}"),
    )
    builder.row(
        InlineKeyboardButton(text=PC_BUTTON, callback_data=f"connect_pc|{email}"),
        InlineKeyboardButton(text=TV_BUTTON, callback_data=f"connect_tv|{email}"),
    )
    builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))

    remaining_time = expiry_time - datetime.now(moscow_tz)
    days = remaining_time.days
    key_message = key_message_success(public_link, f"⏳ Осталось дней: {days} 📅")

    await callback_query.message.answer(key_message, reply_markup=builder.as_markup())

    if state:
        await state.clear()

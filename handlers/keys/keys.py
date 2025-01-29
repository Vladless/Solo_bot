import asyncio
import locale
import os
from datetime import datetime, timedelta
from typing import Any

import aiofiles
import asyncpg
import pytz
from aiogram import F, Router, types
from aiogram.types import BufferedInputFile, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from handlers.payments.yookassa_pay import process_custom_amount_input

from bot import bot
from config import (
    CONNECT_ANDROID,
    CONNECT_IOS,
    DATABASE_URL,
    DOWNLOAD_ANDROID,
    DOWNLOAD_IOS,
    ENABLE_DELETE_KEY_BUTTON,
    ENABLE_UPDATE_SUBSCRIPTION_BUTTON,
    PUBLIC_LINK,
    RENEWAL_PLANS,
    TOTAL_GB,
    USE_COUNTRY_SELECTION,
    USE_NEW_PAYMENT_FLOW,
)
from database import (
    check_server_name_by_cluster,
    delete_key,
    get_balance,
    get_key_details,
    get_keys,
    get_keys_by_server,
    get_servers,
    create_temporary_data,
    store_key,
    update_balance,
    update_key_expiry,
)
from handlers.buttons.add_subscribe import (
    DOWNLOAD_ANDROID_BUTTON,
    DOWNLOAD_IOS_BUTTON,
    IMPORT_ANDROID,
    IMPORT_IOS,
    PC_BUTTON,
    TV_BUTTON,
)
from handlers.keys.key_utils import (
    delete_key_from_cluster,
    delete_key_from_db,
    renew_key_in_cluster,
    update_subscription,
)
from handlers.payments.robokassa_pay import handle_custom_amount_input
from handlers.texts import (
    DISCOUNTS,
    KEY_NOT_FOUND_MSG,
    PLAN_SELECTION_MSG,
    SUCCESS_RENEWAL_MSG,
    key_message,
)
from handlers.utils import handle_error
from logger import logger

locale.setlocale(locale.LC_TIME, "ru_RU.UTF-8")

router = Router()


@router.callback_query(F.data == "view_keys")
@router.message(F.text == "/subs")
async def process_callback_or_message_view_keys(
    callback_query_or_message: types.Message | types.CallbackQuery, session: Any
):
    if isinstance(callback_query_or_message, types.CallbackQuery):
        chat_id = callback_query_or_message.message.chat.id
        send_message = callback_query_or_message.message.answer
        send_photo = callback_query_or_message.message.answer_photo
    else:
        chat_id = callback_query_or_message.chat.id
        send_message = callback_query_or_message.answer
        send_photo = callback_query_or_message.answer_photo

    try:
        records = await get_keys(chat_id, session)

        inline_keyboard, response_message = build_keys_response(records)

        image_path = os.path.join("img", "pic_keys.jpg")
        await send_with_optional_image(send_message, send_photo, image_path, response_message, inline_keyboard)
    except Exception as e:
        error_message = f"Ошибка при получении ключей: {e}"
        await send_message(text=error_message)


def build_keys_response(records):
    """
    Формирует сообщение и клавиатуру для устройств с указанием срока действия подписки.
    """
    builder = InlineKeyboardBuilder()

    moscow_tz = pytz.timezone("Europe/Moscow")

    if records:
        response_message = "<b>🔑 Список ваших подписок:</b>\n\n"
        for record in records:
            key_name = record["email"]
            expiry_time = record.get("expiry_time")

            if expiry_time:
                expiry_date_full = datetime.fromtimestamp(expiry_time / 1000, tz=moscow_tz)
                formatted_date_full = expiry_date_full.strftime("до %d %B %Y года, %H:%M").lower()

                formatted_date_short = expiry_date_full.strftime("до %d %B").lower()
            else:
                formatted_date_full = "без срока действия"
                formatted_date_short = "без срока действия"

            button_text = f"{key_name} ({formatted_date_short})"
            builder.row(InlineKeyboardButton(text=button_text, callback_data=f"view_key|{key_name}"))

            response_message += f"• <b>{key_name}</b> ({formatted_date_full})\n"

    else:
        response_message = (
            "<b>🔑 У вас пока нет подписок.</b>\n\nВы можете создать новую подписку для подключения устройств."
        )

    builder.row(InlineKeyboardButton(text="➕ Добавить подписку", callback_data="create_key"))
    builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))

    inline_keyboard = builder.as_markup()
    return inline_keyboard, response_message


async def send_with_optional_image(send_message, send_photo, image_path, text, keyboard):
    """
    Отправляет сообщение с изображением, если файл существует. В противном случае отправляет только текст.
    """
    if os.path.isfile(image_path):
        async with aiofiles.open(image_path, "rb") as image_file:
            image_data = await image_file.read()
            await send_photo(
                photo=BufferedInputFile(image_data, filename=os.path.basename(image_path)),
                caption=text,
                reply_markup=keyboard,
            )
    else:
        await send_message(
            text=text,
            reply_markup=keyboard,
        )


@router.callback_query(F.data.startswith("view_key|"))
async def process_callback_view_key(callback_query: types.CallbackQuery, session: Any):
    tg_id = callback_query.message.chat.id
    key_name = callback_query.data.split("|")[1]
    try:
        record = await get_key_details(key_name, session)

        if record:
            key = record["key"]
            expiry_time = record["expiry_time"]
            server_name = record["server_id"]
            expiry_date = datetime.utcfromtimestamp(expiry_time / 1000)
            current_date = datetime.utcnow()
            time_left = expiry_date - current_date

            if time_left.total_seconds() <= 0:
                days_left_message = "<b>🕒 Статус подписки:</b>\n🔴 Истекла\nОсталось часов: 0\nОсталось минут: 0"
            else:
                total_seconds = int(time_left.total_seconds())
                days = total_seconds // 86400
                hours = (total_seconds % 86400) // 3600
                minutes = (total_seconds % 3600) // 60

                days_left_message = (
                    f"<b>🕒 Статус подписки:</b>\n"
                    f"Осталось: <b>{days}</b> дней, <b>{hours}</b> часов, <b>{minutes}</b> минут"
                )

            formatted_expiry_date = expiry_date.strftime("%d %B %Y года")
            response_message = key_message(key, formatted_expiry_date, days_left_message, server_name)

            builder = InlineKeyboardBuilder()

            if not key.startswith(PUBLIC_LINK) or ENABLE_UPDATE_SUBSCRIPTION_BUTTON:
                builder.row(
                    InlineKeyboardButton(
                        text="🔄 Обновить подписку",
                        callback_data=f"update_subscription|{key_name}",
                    )
                )

            builder.row(
                InlineKeyboardButton(text=DOWNLOAD_IOS_BUTTON, url=DOWNLOAD_IOS),
                InlineKeyboardButton(text=DOWNLOAD_ANDROID_BUTTON, url=DOWNLOAD_ANDROID),
            )

            builder.row(
                InlineKeyboardButton(text=IMPORT_IOS, url=f"{CONNECT_IOS}{key}"),
                InlineKeyboardButton(text=IMPORT_ANDROID, url=f"{CONNECT_ANDROID}{key}"),
            )

            builder.row(
                InlineKeyboardButton(text=PC_BUTTON, callback_data=f"connect_pc|{key_name}"),
                InlineKeyboardButton(text=TV_BUTTON, callback_data=f"connect_tv|{key_name}"),
            )

            if ENABLE_DELETE_KEY_BUTTON:
                builder.row(
                    InlineKeyboardButton(text="⏳ Продлить", callback_data=f"renew_key|{key_name}"),
                    InlineKeyboardButton(text="❌ Удалить", callback_data=f"delete_key|{key_name}"),
                )
            else:
                builder.row(InlineKeyboardButton(text="⏳ Продлить", callback_data=f"renew_key|{key_name}"))
            builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="view_keys"))
            builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))

            keyboard = builder.as_markup()

            image_path = os.path.join("img", "pic_view.jpg")

            if not os.path.isfile(image_path):
                await callback_query.message.answer("Файл изображения не найден.")
                return

            async with aiofiles.open(image_path, "rb") as image_file:
                image_data = await image_file.read()
                await callback_query.message.answer_photo(
                    photo=BufferedInputFile(image_data, filename="pic_view.jpg"),
                    caption=response_message,
                    reply_markup=keyboard,
                )
        else:
            await callback_query.message.answer(
                text="<b>Информация о подписке не найдена.</b>",
            )
    except Exception as e:
        await handle_error(
            tg_id,
            callback_query,
            f"Ошибка при получении информации о ключе: {e}",
        )


@router.callback_query(F.data.startswith("update_subscription|"))
async def process_callback_update_subscription(callback_query: types.CallbackQuery, session: Any):
    tg_id = callback_query.message.chat.id
    email = callback_query.data.split("|")[1]

    try:
        await update_subscription(tg_id, email, session)
        await process_callback_view_key(callback_query, session)
    except Exception as e:
        logger.error(f"Ошибка при обновлении ключа {email} пользователем: {e}")
        await handle_error(tg_id, callback_query, f"Ошибка при обновлении подписки: {e}")


@router.callback_query(F.data.startswith("delete_key|"))
async def process_callback_delete_key(callback_query: types.CallbackQuery):
    client_id = callback_query.data.split("|")[1]
    try:
        confirmation_keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="✅ Да, удалить",
                        callback_data=f"confirm_delete|{client_id}",
                    )
                ],
                [types.InlineKeyboardButton(text="❌ Нет, отменить", callback_data="view_keys")],
            ]
        )

        await callback_query.message.answer(
            text="<b>Вы уверены, что хотите удалить ключ?</b>",
            reply_markup=confirmation_keyboard,
        )

    except Exception as e:
        logger.error(e)


@router.callback_query(F.data.startswith("renew_key|"))
async def process_callback_renew_key(callback_query: types.CallbackQuery, session: Any):
    tg_id = callback_query.message.chat.id
    key_name = callback_query.data.split("|")[1]
    try:
        record = await get_key_details(key_name, session)

        if record:
            client_id = record["client_id"]
            expiry_time = record["expiry_time"]

            builder = InlineKeyboardBuilder()

            for plan_id, plan_details in RENEWAL_PLANS.items():
                months = plan_details["months"]
                price = plan_details["price"]
                discount = DISCOUNTS.get(plan_id, 0)
                button_text = f"📅 {months} месяц{'а' if months > 1 else ''} ({price} руб.)" + (
                    f" {discount}% скидка" if discount > 0 else ""
                )
                builder.row(
                    InlineKeyboardButton(
                        text=button_text,
                        callback_data=f"renew_plan|{months}|{client_id}",
                    )
                )

            builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="view_keys"))

            balance = await get_balance(tg_id)

            response_message = PLAN_SELECTION_MSG.format(
                balance=balance,
                expiry_date=datetime.utcfromtimestamp(expiry_time / 1000).strftime("%Y-%m-%d %H:%M:%S"),
            )

            await callback_query.message.answer(
                text=response_message,
                reply_markup=builder.as_markup(),
            )
        else:
            await callback_query.message.answer("<b>Ключ не найден.</b>")
    except Exception as e:
        logger.error(e)


@router.callback_query(F.data.startswith("confirm_delete|"))
async def process_callback_confirm_delete(callback_query: types.CallbackQuery, session: Any):
    email = callback_query.data.split("|")[1]
    try:
        record = await get_key_details(email, session)

        if record:
            client_id = record["client_id"]
            response_message = "Ключ успешно удален."
            back_button = types.InlineKeyboardButton(text="Назад", callback_data="view_keys")
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[back_button]])

            await delete_key(client_id, session)
            await callback_query.message.answer(
                response_message,
                reply_markup=keyboard,
            )

            servers = await get_servers(session)

            async def delete_key_from_servers():
                try:
                    tasks = []
                    for cluster_id, cluster in servers.items():
                        tasks.append(delete_key_from_cluster(cluster_id, email, client_id))

                    await asyncio.gather(*tasks)

                except Exception as e:
                    logger.error(f"Ошибка при удалении ключа {client_id}: {e}")

            asyncio.create_task(delete_key_from_servers())

            await delete_key_from_db(client_id, session)

        else:
            response_message = "Ключ не найден или уже удален."
            back_button = types.InlineKeyboardButton(text="Назад", callback_data="view_keys")
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[back_button]])

            await callback_query.message.answer(
                response_message,
                reply_markup=keyboard,
            )
    except Exception as e:
        logger.error(e)


@router.callback_query(F.data.startswith("renew_plan|"))
async def process_callback_renew_plan(callback_query: types.CallbackQuery, session: Any):
    tg_id = callback_query.message.chat.id
    plan, client_id = callback_query.data.split("|")[1], callback_query.data.split("|")[2]
    days_to_extend = 30 * int(plan)

    gb_multiplier = {"1": 1, "3": 3, "6": 6, "12": 12}
    total_gb = TOTAL_GB * gb_multiplier.get(plan, 1) if TOTAL_GB > 0 else 0

    try:
        record = await get_keys_by_server(tg_id, client_id, session)

        if record:
            email = record["email"]
            expiry_time = record["expiry_time"]
            current_time = datetime.utcnow().timestamp() * 1000

            if expiry_time <= current_time:
                new_expiry_time = int(current_time + timedelta(days=days_to_extend).total_seconds() * 1000)
            else:
                new_expiry_time = int(expiry_time + timedelta(days=days_to_extend).total_seconds() * 1000)

            cost = RENEWAL_PLANS[plan]["price"]
            balance = await get_balance(tg_id)

            if balance < cost:
                required_amount = cost - balance

                logger.info(
                    f"[RENEW] Пользователю {tg_id} не хватает {required_amount}₽. Запуск доплаты через {USE_NEW_PAYMENT_FLOW}"
                )

                await create_temporary_data(
                    session,
                    tg_id,
                    "waiting_for_renewal_payment",
                    {
                        "plan": plan,
                        "client_id": client_id,
                        "cost": cost,
                        "required_amount": required_amount,
                        "new_expiry_time": new_expiry_time,
                        "total_gb": total_gb,
                        "email": email,
                    },
                )

                if USE_NEW_PAYMENT_FLOW == "YOOKASSA":
                    logger.info(f"[RENEW] Запуск оплаты через Юкассу для пользователя {tg_id}")
                    await process_custom_amount_input(callback_query, session)
                elif USE_NEW_PAYMENT_FLOW == "ROBOKASSA":
                    logger.info(f"[RENEW] Запуск оплаты через Робокассу для пользователя {tg_id}")
                    await handle_custom_amount_input(callback_query, session)
                else:
                    logger.info(f"[RENEW] Отправка сообщения о доплате пользователю {tg_id}")
                    builder = InlineKeyboardBuilder()
                    builder.row(InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="pay"))
                    builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))

                    await callback_query.message.answer(
                        f"💳 Недостаточно средств. Пополните баланс на {required_amount}₽.",
                        reply_markup=builder.as_markup(),
                    )
                return

            logger.info(f"[RENEW] Средств достаточно. Продление ключа для пользователя {tg_id}")
            await complete_key_renewal(tg_id, client_id, email, new_expiry_time, total_gb, cost, callback_query, plan)

        else:
            await callback_query.message.answer(KEY_NOT_FOUND_MSG)
            logger.error(f"[RENEW] Ключ с client_id={client_id} не найден.")
    except Exception as e:
        logger.error(f"[RENEW] Ошибка при продлении ключа для пользователя {tg_id}: {e}")


async def complete_key_renewal(tg_id, client_id, email, new_expiry_time, total_gb, cost, callback_query, plan):
    response_message = SUCCESS_RENEWAL_MSG.format(months=plan)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))

    if callback_query:
        await callback_query.message.answer(response_message, reply_markup=builder.as_markup())
    else:
        await bot.send_message(tg_id, response_message, reply_markup=builder.as_markup())

    conn = await asyncpg.connect(DATABASE_URL)
    key_info = await get_key_details(email, conn)

    if not key_info:
        logger.error(f"[RENEW] Ключ с client_id {client_id} для пользователя {tg_id} не найден.")
        await conn.close()
        return

    server_id = key_info["server_id"]

    if USE_COUNTRY_SELECTION:
        cluster_info = await check_server_name_by_cluster(server_id, conn)

        if not cluster_info:
            logger.error(f"[RENEW] Сервер {server_id} не найден в таблице servers.")
            await conn.close()
            return

        cluster_id = cluster_info["cluster_name"]
    else:
        cluster_id = server_id

    await conn.close()

    logger.info(f"[RENEW] Запуск продления ключа для пользователя {tg_id} на {plan} мес. в кластере {cluster_id}.")

    async def renew_key_on_cluster():
        await renew_key_in_cluster(
            cluster_id,
            email,
            client_id,
            new_expiry_time,
            total_gb,
        )

        await update_key_expiry(client_id, new_expiry_time, conn)
        await update_balance(tg_id, -cost, conn)
        logger.info(f"[RENEW] Ключ {client_id} успешно продлён на {plan} мес. для пользователя {tg_id}.")

    await renew_key_on_cluster()

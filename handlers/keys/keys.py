import asyncio
import locale
import os
from datetime import datetime, timedelta
from typing import Any

from aiogram import F, Router, types
from aiogram.types import BufferedInputFile, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import bot
from config import (
    CONNECT_ANDROID,
    CONNECT_IOS,
    DOWNLOAD_ANDROID,
    DOWNLOAD_IOS,
    ENABLE_DELETE_KEY_BUTTON,
    ENABLE_UPDATE_SUBSCRIPTION_BUTTON,
    PUBLIC_LINK,
    RENEWAL_PLANS,
    TOTAL_GB,
    USE_NEW_PAYMENT_FLOW,
)
from database import (
    delete_key,
    get_balance,
    get_servers_from_db,
    save_temporary_data,
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
    update_key_on_cluster,
)
from handlers.payments.robokassa_pay import handle_custom_amount_input
from handlers.payments.yookassa_pay import process_custom_amount_input
from handlers.texts import (
    DISCOUNTS,
    KEY_NOT_FOUND_MSG,
    PLAN_SELECTION_MSG,
    SUCCESS_RENEWAL_MSG,
    key_message,
)
from handlers.utils import get_least_loaded_cluster, handle_error
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
        records = await session.fetch(
            """
            SELECT email, client_id, expiry_time FROM keys WHERE tg_id = $1
            """,
            chat_id,
        )

        inline_keyboard, response_message = build_keys_response(records)

        image_path = os.path.join("img", "pic_keys.jpg")
        await send_with_optional_image(
            send_message, send_photo, image_path, response_message, inline_keyboard
        )
    except Exception as e:
        error_message = f"Ошибка при получении ключей: {e}"
        await send_message(text=error_message)


def build_keys_response(records):
    """
    Формирует сообщение и клавиатуру для устройств.
    """
    builder = InlineKeyboardBuilder()

    if records:
        for record in records:
            key_name = record["email"]
            expiry_date = datetime.utcfromtimestamp(record["expiry_time"] / 1000).strftime("%d.%m.%Y")
            builder.row(
                InlineKeyboardButton(
                    text=f"🔑 {key_name} (до {expiry_date})",
                    callback_data=f"view_key|{key_name}"
                )
            )

    builder.row(
        InlineKeyboardButton(text="➕ Добавить подписку", callback_data="create_key")
    )

    builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))

    inline_keyboard = builder.as_markup()
    response_message = (
        "<b>🔑 Список ваших подписок</b>\n\n"
        "<i>👇 Выберите подписку для управления или добавьте новую для подключения дополнительного устройства:</i>"
    )
    return inline_keyboard, response_message


async def send_with_optional_image(
    send_message, send_photo, image_path, text, keyboard
):
    """
    Отправляет сообщение с изображением, если файл существует. В противном случае отправляет только текст.
    """
    if os.path.isfile(image_path):
        with open(image_path, "rb") as image_file:
            await send_photo(
                photo=BufferedInputFile(
                    image_file.read(), filename=os.path.basename(image_path)
                ),
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
        record = await session.fetchrow(
            """
            SELECT k.expiry_time, k.server_id, k.key
            FROM keys k
            WHERE k.tg_id = $1 AND k.email = $2
            """,
            tg_id,
            key_name,
        )

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
            response_message = key_message(
                key, formatted_expiry_date, days_left_message, server_name
            )

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
                InlineKeyboardButton(
                    text=DOWNLOAD_ANDROID_BUTTON, url=DOWNLOAD_ANDROID
                ),
            )

            builder.row(
                InlineKeyboardButton(text=IMPORT_IOS, url=f"{CONNECT_IOS}{key}"),
                InlineKeyboardButton(
                    text=IMPORT_ANDROID, url=f"{CONNECT_ANDROID}{key}"
                ),
            )

            builder.row(
                InlineKeyboardButton(
                    text=PC_BUTTON, callback_data=f"connect_pc|{key_name}"
                ),
                InlineKeyboardButton(
                    text=TV_BUTTON, callback_data=f"connect_tv|{key_name}"
                ),
            )

            # ✅ Добавлена проверка флага ENABLE_DELETE_KEY_BUTTON
            if ENABLE_DELETE_KEY_BUTTON:
                builder.row(
                    InlineKeyboardButton(
                        text="⏳ Продлить", callback_data=f"renew_key|{key_name}"
                    ),
                    InlineKeyboardButton(
                        text="❌ Удалить", callback_data=f"delete_key|{key_name}"
                    ),
                )
            else:
                builder.row(
                    InlineKeyboardButton(
                        text="⏳ Продлить", callback_data=f"renew_key|{key_name}"
                    )
                )
            builder.row(
                InlineKeyboardButton(
                    text="🔙 Назад", callback_data="view_keys"
                )
            )
            builder.row(
                InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile")
            )

            keyboard = builder.as_markup()

            image_path = os.path.join("img", "pic_view.jpg")

            if not os.path.isfile(image_path):
                await callback_query.message.answer("Файл изображения не найден.")
                return

            with open(image_path, "rb") as image_file:
                await callback_query.message.answer_photo(
                    photo=BufferedInputFile(image_file.read(), filename="pic_view.jpg"),
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
async def process_callback_update_subscription(
    callback_query: types.CallbackQuery, session: Any
):
    tg_id = callback_query.message.chat.id
    email = callback_query.data.split("|")[1]
    try:
        record = await session.fetchrow(
            """
            SELECT k.key, k.expiry_time, k.email, k.server_id, k.client_id
            FROM keys k
            WHERE k.tg_id = $1 AND k.email = $2
            """,
            tg_id,
            email,
        )

        if record:
            expiry_time = record["expiry_time"]
            client_id = record["client_id"]
            public_link = f"{PUBLIC_LINK}{email}/{tg_id}"

            try:
                await session.execute(
                    """
                    DELETE FROM keys
                    WHERE tg_id = $1 AND email = $2
                    """,
                    tg_id,
                    email,
                )
            except Exception as delete_error:
                await callback_query.message.answer(
                    f"Ошибка при удалении старой подписки: {delete_error}",
                )
                return

            least_loaded_cluster_id = await get_least_loaded_cluster()

            await asyncio.gather(
                update_key_on_cluster(
                    tg_id,
                    client_id,
                    email,
                    expiry_time,
                    least_loaded_cluster_id,
                )
            )

            await store_key(
                tg_id,
                client_id,
                email,
                expiry_time,
                public_link,
                server_id=least_loaded_cluster_id,
                session=session,
            )

            await process_callback_view_key(callback_query, session)
        else:
            await callback_query.message.answer("<b>Ключ не найден в базе данных.</b>")
    except Exception as e:
        await handle_error(
            tg_id, callback_query, f"Ошибка при обновлении подписки: {e}"
        )


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
                [
                    types.InlineKeyboardButton(
                        text="❌ Нет, отменить", callback_data="view_keys"
                    )
                ],
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
        record = await session.fetchrow(
            """
            SELECT client_id, expiry_time 
            FROM keys 
            WHERE email = $1
            """,
            key_name,
        )

        if record:
            client_id = record["client_id"]
            expiry_time = record["expiry_time"]

            builder = InlineKeyboardBuilder()

            builder.row(
                InlineKeyboardButton(
                    text=f'📅 1 месяц ({RENEWAL_PLANS["1"]["price"]} руб.)',
                    callback_data=f"renew_plan|1|{client_id}",
                )
            )

            builder.row(
                InlineKeyboardButton(
                    text=f'📅 3 месяца ({RENEWAL_PLANS["3"]["price"]} руб.) {DISCOUNTS["3"]}% скидка',
                    callback_data=f"renew_plan|3|{client_id}",
                )
            )

            builder.row(
                InlineKeyboardButton(
                    text=f'📅 6 месяцев ({RENEWAL_PLANS["6"]["price"]} руб.) {DISCOUNTS["6"]}% скидка',
                    callback_data=f"renew_plan|6|{client_id}",
                )
            )

            builder.row(
                InlineKeyboardButton(
                    text=f'📅 12 месяцев ({RENEWAL_PLANS["12"]["price"]} руб.) ({DISCOUNTS["12"]}% 🔥)',
                    callback_data=f"renew_plan|12|{client_id}",
                )
            )
            back_button = InlineKeyboardButton(
                text="🔙 Назад", callback_data="view_keys"
            )
            builder.row(back_button)

            balance = await get_balance(tg_id)

            response_message = PLAN_SELECTION_MSG.format(
                balance=balance,
                expiry_date=datetime.utcfromtimestamp(expiry_time / 1000).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
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
async def process_callback_confirm_delete(
    callback_query: types.CallbackQuery, session: Any
):
    email = callback_query.data.split("|")[1]
    try:
        record = await session.fetchrow(
            "SELECT client_id FROM keys WHERE email = $1", email
        )

        if record:
            client_id = record["client_id"]
            response_message = "Ключ успешно удален."
            back_button = types.InlineKeyboardButton(
                text="Назад", callback_data="view_keys"
            )
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[back_button]])

            await delete_key(client_id)
            await callback_query.message.answer(
                response_message,
                reply_markup=keyboard,
            )

            servers = await get_servers_from_db()

            async def delete_key_from_servers():
                try:
                    tasks = []
                    for cluster_id, cluster in servers.items():
                        tasks.append(
                            delete_key_from_cluster(cluster_id, email, client_id)
                        )

                    await asyncio.gather(*tasks)

                except Exception as e:
                    logger.error(f"Ошибка при удалении ключа {client_id}: {e}")

            asyncio.create_task(delete_key_from_servers())

            await delete_key_from_db(client_id, session)

        else:
            response_message = "Ключ не найден или уже удален."
            back_button = types.InlineKeyboardButton(
                text="Назад", callback_data="view_keys"
            )
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
        record = await session.fetchrow(
            "SELECT email, expiry_time FROM keys WHERE client_id = $1",
            client_id,
        )

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

                logger.info(f"[RENEW] Пользователю {tg_id} не хватает {required_amount}₽. Запуск доплаты через {USE_NEW_PAYMENT_FLOW}")

                await save_temporary_data(
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

    servers = await get_servers_from_db()

    logger.info(f"[RENEW] Запуск продления ключа для пользователя {tg_id} на {plan} мес. на всех серверах.")

    async def renew_key_on_servers():
        tasks = []
        for cluster_id in servers:
            task = asyncio.create_task(
                renew_key_in_cluster(
                    cluster_id,
                    email,
                    client_id,
                    new_expiry_time,
                    total_gb,
                )
            )
            tasks.append(task)

        await asyncio.gather(*tasks)

        await update_balance(tg_id, -cost)
        await update_key_expiry(client_id, new_expiry_time)
        logger.info(f"[RENEW] Ключ {client_id} успешно продлён на {plan} мес. для пользователя {tg_id}.")

    await renew_key_on_servers()


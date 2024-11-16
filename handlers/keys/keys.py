import asyncio
import locale
import os
from datetime import datetime, timedelta

import asyncpg
from aiogram import F, Router, types
from aiogram.types import BufferedInputFile

from bot import bot
from config import CLUSTERS, CONNECT_ANDROID, CONNECT_IOS, DATABASE_URL, DOWNLOAD_ANDROID, DOWNLOAD_IOS, PUBLIC_LINK, TOTAL_GB
from database import delete_key, get_balance, store_key, update_balance, update_key_expiry
from handlers.keys.key_utils import delete_key_from_cluster, delete_key_from_db, renew_key_in_cluster, update_key_on_cluster
from handlers.texts import INSUFFICIENT_FUNDS_MSG, KEY_NOT_FOUND_MSG, NO_KEYS, PLAN_SELECTION_MSG, RENEWAL_PLANS, SUCCESS_RENEWAL_MSG, key_message
from handlers.utils import get_least_loaded_cluster, handle_error
from logger import logger

locale.setlocale(locale.LC_TIME, "ru_RU.UTF-8")

router = Router()


@router.callback_query(F.data == "view_keys")
async def process_callback_view_keys(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            records = await conn.fetch(
                """
                SELECT email, client_id FROM keys WHERE tg_id = $1
            """,
                tg_id,
            )

            if records:
                buttons = []
                for record in records:
                    key_name = record["email"]
                    client_id = record["client_id"]
                    button = types.InlineKeyboardButton(
                        text=f"🔑 {key_name}",
                        callback_data=f"view_key|{key_name}|{client_id}",
                    )
                    buttons.append([button])

                back_button = types.InlineKeyboardButton(
                    text="🔙 Назад", callback_data="view_profile"
                )
                buttons.append([back_button])

                inline_keyboard = types.InlineKeyboardMarkup(inline_keyboard=buttons)
                response_message = (
                    "<b>🔑 Список ваших устройств</b>\n\n"
                    "<i>👇 Выберите устройство для управления подпиской:</i>"
                )

                image_path = os.path.join(os.path.dirname(__file__), "pic_keys.jpg")

                try:
                    await bot.delete_message(
                        chat_id=tg_id, message_id=callback_query.message.message_id
                    )
                except Exception as e:
                    logger.error(f"Ошибка при удалении сообщения: {e}")

                if os.path.isfile(image_path):
                    with open(image_path, "rb") as image_file:
                        await bot.send_photo(
                            chat_id=tg_id,
                            photo=BufferedInputFile(
                                image_file.read(), filename="pic_keys.jpg"
                            ),
                            caption=response_message,
                            parse_mode="HTML",
                            reply_markup=inline_keyboard,
                        )
                else:
                    await bot.send_message(
                        chat_id=tg_id,
                        text=response_message,
                        reply_markup=inline_keyboard,
                        parse_mode="HTML",
                    )

            else:
                response_message = NO_KEYS
                create_key_button = types.InlineKeyboardButton(
                    text="➕ Создать подписку", callback_data="create_key"
                )
                back_button = types.InlineKeyboardButton(
                    text="🔙 Назад", callback_data="view_profile"
                )

                keyboard = types.InlineKeyboardMarkup(
                    inline_keyboard=[[create_key_button], [back_button]]
                )

                try:
                    await bot.delete_message(
                        chat_id=tg_id, message_id=callback_query.message.message_id
                    )
                except Exception as e:
                    logger.error(f"Ошибка при удалении сообщения: {e}")

                image_path = os.path.join(os.path.dirname(__file__), "pic_keys.jpg")

                if os.path.isfile(image_path):
                    with open(image_path, "rb") as image_file:
                        await bot.send_photo(
                            chat_id=tg_id,
                            photo=BufferedInputFile(
                                image_file.read(), filename="pic_keys.jpg"
                            ),
                            caption=response_message,
                            parse_mode="HTML",
                            reply_markup=keyboard,
                        )
                else:
                    await bot.send_message(
                        chat_id=tg_id,
                        text=response_message,
                        reply_markup=keyboard,
                        parse_mode="HTML",
                    )

        finally:
            await conn.close()

    except Exception as e:
        await handle_error(tg_id, callback_query, f"Ошибка при получении ключей: {e}")

    await callback_query.answer()


@router.callback_query(F.data.startswith("view_key|"))
async def process_callback_view_key(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    key_name, client_id = (
        callback_query.data.split("|")[1],
        callback_query.data.split("|")[2],
    )

    try:
        try:
            await bot.delete_message(
                chat_id=tg_id, message_id=callback_query.message.message_id
            )
        except Exception:
            pass

        conn = await asyncpg.connect(DATABASE_URL)
        try:
            record = await conn.fetchrow(
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
                    days_left_message = (
                        "<b>🕒 Статус подписки:</b>\n🔴 Истекла\nОсталось часов: 0"
                    )
                elif time_left.days > 0:
                    days_left_message = f"Осталось дней: <b>{time_left.days}</b>"
                else:
                    hours_left = time_left.seconds // 3600
                    days_left_message = f"Осталось часов: <b>{hours_left}</b>"

                formatted_expiry_date = expiry_date.strftime("%d %B %Y года")
                response_message = key_message(
                    key, formatted_expiry_date, days_left_message, server_name
                )

                download_android_button = types.InlineKeyboardButton(
                    text="🤖 Скачать", url=DOWNLOAD_ANDROID
                )
                download_iphone_button = types.InlineKeyboardButton(
                    text="🍏 Скачать", url=DOWNLOAD_IOS
                )

                connect_iphone_button = types.InlineKeyboardButton(
                    text="🍏 Подключить", url=f"{CONNECT_IOS}{key}"
                )
                connect_android_button = types.InlineKeyboardButton(
                    text="🤖 Подключить", url=f"{CONNECT_ANDROID}{key}"
                )

                connect_pc_button = types.InlineKeyboardButton(
                    text="💻 Windows/Linux", callback_data=f"connect_pc|{key}"
                )

                renew_button = types.InlineKeyboardButton(
                    text="⏳ Продлить", callback_data=f"renew_key|{client_id}"
                )
                delete_button = types.InlineKeyboardButton(
                    text="❌ Удалить", callback_data=f"delete_key|{client_id}"
                )
                back_button = types.InlineKeyboardButton(
                    text="🔙 Назад в профиль", callback_data="view_profile"
                )

                inline_keyboard = [
                    [download_iphone_button, download_android_button],
                    [connect_iphone_button, connect_android_button],
                    [connect_pc_button],
                    [renew_button, delete_button],
                ]

                if not key.startswith(PUBLIC_LINK):
                    update_subscription_button = types.InlineKeyboardButton(
                        text="🔄 Обновить подписку",
                        callback_data=f"update_subscription|{client_id}",
                    )
                    inline_keyboard.append([update_subscription_button])

                inline_keyboard.append([back_button])

                keyboard = types.InlineKeyboardMarkup(inline_keyboard=inline_keyboard)

                image_path = os.path.join(os.path.dirname(__file__), "pic_view.jpg")

                if not os.path.isfile(image_path):
                    await bot.send_message(tg_id, "Файл изображения не найден.")
                    return

                with open(image_path, "rb") as image_file:
                    await bot.send_photo(
                        chat_id=tg_id,
                        photo=BufferedInputFile(
                            image_file.read(), filename="pic_view.jpg"
                        ),
                        caption=response_message,
                        reply_markup=keyboard,
                        parse_mode="HTML",
                    )
            else:
                await bot.send_message(
                    chat_id=tg_id,
                    text="<b>Информация о подписке не найдена.</b>",
                    parse_mode="HTML",
                )

        finally:
            await conn.close()

    except Exception as e:
        await handle_error(
            tg_id, callback_query, f"Ошибка при получении информации о ключе: {e}"
        )

    await callback_query.answer()


@router.callback_query(F.data.startswith("update_subscription|"))
async def process_callback_update_subscription(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    client_id = callback_query.data.split("|")[1]

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            record = await conn.fetchrow(
                """
                SELECT k.key, k.expiry_time, k.email, k.server_id
                FROM keys k
                WHERE k.tg_id = $1 AND k.client_id = $2
            """,
                tg_id,
                client_id,
            )

            if record:
                expiry_time = record["expiry_time"]
                email = record["email"]
                public_link = f"{PUBLIC_LINK}{email}"

                try:
                    await conn.execute(
                        """
                        DELETE FROM keys
                        WHERE tg_id = $1 AND client_id = $2
                    """,
                        tg_id,
                        client_id,
                    )
                except Exception as delete_error:
                    await bot.send_message(
                        tg_id, f"Ошибка при удалении старой подписки: {delete_error}"
                    )
                    return

                least_loaded_cluster_id = await get_least_loaded_cluster()

                tasks = []
                tasks.append(
                    update_key_on_cluster(
                        tg_id, client_id, email, expiry_time, least_loaded_cluster_id
                    )
                )

                await asyncio.gather(*tasks)

                await store_key(
                    tg_id,
                    client_id,
                    email,
                    expiry_time,
                    public_link,
                    server_id=least_loaded_cluster_id,
                )

                try:
                    await bot.delete_message(
                        chat_id=tg_id, message_id=callback_query.message.message_id
                    )
                except Exception as e:
                    logger.error(f"Ошибка при удалении сообщения: {e}")

                response_message = f"Ваша подписка {email} обновлена!"
                back_button = types.InlineKeyboardButton(
                    text="🔙 Назад в профиль", callback_data="view_profile"
                )
                keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[back_button]])

                await bot.send_message(
                    tg_id, response_message, reply_markup=keyboard, parse_mode="HTML"
                )
            else:
                try:
                    await bot.delete_message(
                        chat_id=tg_id, message_id=callback_query.message.message_id
                    )
                except Exception as e:
                    logger.error(f"Ошибка при удалении сообщения: {e}")

                await bot.send_message(
                    tg_id, "<b>Ключ не найден в базе данных.</b>", parse_mode="HTML"
                )

        finally:
            await conn.close()

    except Exception as e:
        await handle_error(
            tg_id, callback_query, f"Ошибка при обновлении подписки: {e}"
        )

    await callback_query.answer()


@router.callback_query(F.data.startswith("delete_key|"))
async def process_callback_delete_key(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    client_id = callback_query.data.split("|")[1]

    try:
        try:
            await bot.delete_message(
                chat_id=tg_id, message_id=callback_query.message.message_id
            )
        except Exception:
            pass

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

        await bot.send_message(
            chat_id=tg_id,
            text="<b>Вы уверены, что хотите удалить ключ?</b>",
            reply_markup=confirmation_keyboard,
            parse_mode="HTML",
        )

    except Exception as e:
        await bot.send_message(
            chat_id=tg_id,
            text=f"<b>Ошибка при удалении ключа:</b> {e}",
            parse_mode="HTML",
        )

    await callback_query.answer()


@router.callback_query(F.data.startswith("renew_key|"))
async def process_callback_renew_key(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    client_id = callback_query.data.split("|")[1]

    try:
        try:
            await bot.delete_message(
                chat_id=tg_id, message_id=callback_query.message.message_id
            )
        except Exception:
            pass

        conn = await asyncpg.connect(DATABASE_URL)
        try:
            record = await conn.fetchrow(
                "SELECT email, expiry_time FROM keys WHERE client_id = $1", client_id
            )

            if record:
                expiry_time = record["expiry_time"]
                keyboard = types.InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            types.InlineKeyboardButton(
                                text=f'📅 1 месяц ({RENEWAL_PLANS["1"]["price"]} руб.)',
                                callback_data=f"renew_plan|1|{client_id}",
                            )
                        ],
                        [
                            types.InlineKeyboardButton(
                                text=f'📅 3 месяца ({RENEWAL_PLANS["3"]["price"]} руб.)',
                                callback_data=f"renew_plan|3|{client_id}",
                            )
                        ],
                        [
                            types.InlineKeyboardButton(
                                text=f'📅 6 месяцев ({RENEWAL_PLANS["6"]["price"]} руб.)',
                                callback_data=f"renew_plan|6|{client_id}",
                            )
                        ],
                        [
                            types.InlineKeyboardButton(
                                text=f'📅 12 месяцев ({RENEWAL_PLANS["12"]["price"]} руб.)',
                                callback_data=f"renew_plan|12|{client_id}",
                            )
                        ],
                        [
                            types.InlineKeyboardButton(
                                text="🔙 Назад", callback_data="view_profile"
                            )
                        ],
                    ]
                )

                balance = await get_balance(tg_id)
                response_message = PLAN_SELECTION_MSG.format(
                    balance=balance,
                    expiry_date=datetime.utcfromtimestamp(expiry_time / 1000).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                )

                await bot.send_message(
                    chat_id=tg_id,
                    text=response_message,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )

            else:
                response_message = "<b>Ключ не найден.</b>"
                await bot.send_message(
                    chat_id=tg_id, text=response_message, parse_mode="HTML"
                )

        finally:
            await conn.close()

    except Exception as e:
        await bot.send_message(
            chat_id=tg_id,
            text=f"<b>Ошибка при выборе плана:</b> {e}",
            parse_mode="HTML",
        )

    await callback_query.answer()


@router.callback_query(F.data.startswith("confirm_delete|"))
async def process_callback_confirm_delete(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    client_id = callback_query.data.split("|")[1]

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            record = await conn.fetchrow(
                "SELECT email FROM keys WHERE client_id = $1", client_id
            )

            if record:
                email = record["email"]
                response_message = "Ключ успешно удален."
                back_button = types.InlineKeyboardButton(
                    text="Назад", callback_data="view_keys"
                )
                keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[back_button]])

                await delete_key(client_id)
                await bot.edit_message_text(
                    response_message,
                    chat_id=tg_id,
                    message_id=callback_query.message.message_id,
                    reply_markup=keyboard,
                )

                async def delete_key_from_servers():
                    try:
                        tasks = []
                        for cluster_id, cluster in CLUSTERS.items():
                            tasks.append(
                                delete_key_from_cluster(cluster_id, email, client_id)
                            )

                        await asyncio.gather(*tasks)

                    except Exception as e:
                        logger.error(f"Ошибка при удалении ключа {client_id}: {e}")

                asyncio.create_task(delete_key_from_servers())

                await delete_key_from_db(client_id)

            else:
                response_message = "Ключ не найден или уже удален."
                back_button = types.InlineKeyboardButton(
                    text="Назад", callback_data="view_keys"
                )
                keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[back_button]])

                await bot.edit_message_text(
                    response_message,
                    chat_id=tg_id,
                    message_id=callback_query.message.message_id,
                    reply_markup=keyboard,
                )

        finally:
            await conn.close()

    except Exception as e:
        await bot.edit_message_text(
            f"Ошибка при удалении ключа: {e}",
            chat_id=tg_id,
            message_id=callback_query.message.message_id,
        )

    await callback_query.answer()


@router.callback_query(F.data.startswith("renew_plan|"))
async def process_callback_renew_plan(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    plan, client_id = (
        callback_query.data.split("|")[1],
        callback_query.data.split("|")[2],
    )
    days_to_extend = 30 * int(plan)

    gb_multiplier = {"1": 1, "3": 3, "6": 6, "12": 12}
    total_gb = TOTAL_GB * gb_multiplier.get(plan, 1) if TOTAL_GB > 0 else 0

    try:
        try:
            await bot.delete_message(
                chat_id=tg_id, message_id=callback_query.message.message_id
            )
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения: {e}")

        conn = await asyncpg.connect(DATABASE_URL)
        try:
            record = await conn.fetchrow(
                "SELECT email, expiry_time FROM keys WHERE client_id = $1", client_id
            )

            if record:
                email = record["email"]
                expiry_time = record["expiry_time"]
                current_time = datetime.utcnow().timestamp() * 1000

                if expiry_time <= current_time:
                    new_expiry_time = int(
                        current_time
                        + timedelta(days=days_to_extend).total_seconds() * 1000
                    )
                else:
                    new_expiry_time = int(
                        expiry_time
                        + timedelta(days=days_to_extend).total_seconds() * 1000
                    )

                cost = RENEWAL_PLANS[plan]["price"]

                balance = await get_balance(tg_id)
                if balance < cost:
                    replenish_button = types.InlineKeyboardButton(
                        text="Пополнить баланс", callback_data="pay"
                    )
                    view_profile = types.InlineKeyboardButton(
                        text="👤 Личный кабинет", callback_data="view_profile"
                    )
                    keyboard = types.InlineKeyboardMarkup(
                        inline_keyboard=[[replenish_button], [view_profile]]
                    )

                    await bot.send_message(
                        tg_id,
                        INSUFFICIENT_FUNDS_MSG,
                        reply_markup=keyboard,
                        parse_mode="HTML",
                    )
                    return

                response_message = SUCCESS_RENEWAL_MSG.format(
                    months=RENEWAL_PLANS[plan]["months"]
                )
                back_button = types.InlineKeyboardButton(
                    text="Назад", callback_data="view_profile"
                )
                keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[back_button]])

                await bot.send_message(
                    tg_id, response_message, reply_markup=keyboard, parse_mode="HTML"
                )

                async def renew_key_on_servers():
                    tasks = []
                    for cluster_id in CLUSTERS:
                        task = asyncio.create_task(
                            renew_key_in_cluster(
                                cluster_id, email, client_id, new_expiry_time, total_gb
                            )
                        )
                        tasks.append(task)

                    await asyncio.gather(*tasks)

                    await update_balance(tg_id, -cost)
                    await update_key_expiry(client_id, new_expiry_time)

                await renew_key_on_servers()

            else:
                await bot.send_message(tg_id, KEY_NOT_FOUND_MSG, parse_mode="HTML")

        finally:
            await conn.close()

    except Exception as e:
        await bot.send_message(
            tg_id, f"Ошибка при продлении ключа: {e}", parse_mode="HTML"
        )

    await callback_query.answer()

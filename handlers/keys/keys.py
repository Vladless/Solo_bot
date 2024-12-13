import asyncio
import locale
import os
from datetime import datetime, timedelta
from typing import Any

from aiogram import F, Router, types
from aiogram.types import BufferedInputFile

from config import PUBLIC_LINK, RENEWAL_PLANS, TOTAL_GB
from database import delete_key, get_balance, get_servers_from_db, store_key, update_balance, update_key_expiry
from handlers.keys.key_utils import (
    delete_key_from_cluster,
    delete_key_from_db,
    renew_key_in_cluster,
    update_key_on_cluster,
)
from handlers.texts import (
    INSUFFICIENT_FUNDS_MSG,
    KEY_NOT_FOUND_MSG,
    NO_KEYS,
    PLAN_SELECTION_MSG,
    SUCCESS_RENEWAL_MSG,
    key_message,
)
from handlers.utils import get_least_loaded_cluster, handle_error
from keyboards.common_kb import build_back_kb
from keyboards.keys.keys_kb import build_view_keys_kb, build_view_no_keys_kb, build_view_key_kb, build_key_delete_kb, \
    build_renewal_plans_kb, build_top_up_kb
from logger import logger

locale.setlocale(locale.LC_TIME, "ru_RU.UTF-8")

router = Router()


@router.callback_query(F.data == "view_keys")
async def process_callback_view_keys(callback_query: types.CallbackQuery, session: Any):
    tg_id = callback_query.message.chat.id
    try:
        records = await session.fetch(
            """
            SELECT email, client_id FROM keys WHERE tg_id = $1
        """,
            tg_id,
        )

        if records:
            # Build keyboard
            kb = build_view_keys_kb(records)

            # Prepare text
            text = (
                "<b>🔑 Список ваших устройств</b>\n\n"
                "<i>👇 Выберите устройство для управления подпиской:</i>"
            )

            image_path = os.path.join("img", "pic_keys.jpg")
            if os.path.isfile(image_path):
                with open(image_path, "rb") as image_file:
                    await callback_query.message.answer_photo(
                        photo=BufferedInputFile(image_file.read(), filename="pic_keys.jpg"),
                        caption=text,
                        reply_markup=kb,
                    )
            else:
                await callback_query.message.answer(
                    text=text,
                    reply_markup=kb,
                )

        else:
            # Build keyboard
            kb = build_view_no_keys_kb()

            image_path = os.path.join("img", "pic_keys.jpg")
            if os.path.isfile(image_path):
                with open(image_path, "rb") as image_file:
                    await callback_query.message.answer_photo(
                        photo=BufferedInputFile(image_file.read(), filename="pic_keys.jpg"),
                        caption=NO_KEYS,
                        reply_markup=kb,
                    )
            else:
                await callback_query.message.answer(
                    text=NO_KEYS,
                    reply_markup=kb,
                )
    except Exception as e:
        await handle_error(tg_id, callback_query, f"Ошибка при получении ключей: {e}")


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
                days_left_message = "<b>🕒 Статус подписки:</b>\n🔴 Истекла\nОсталось часов: 0"
            elif time_left.days > 0:
                days_left_message = f"Осталось дней: <b>{time_left.days}</b>"
            else:
                hours_left = time_left.seconds // 3600
                days_left_message = f"Осталось часов: <b>{hours_left}</b>"

            formatted_expiry_date = expiry_date.strftime("%d %B %Y года")
            response_message = key_message(key, formatted_expiry_date, days_left_message, server_name)

            # Build keyboard
            kb = build_view_key_kb(key, key_name)

            image_path = os.path.join("img", "pic_view.jpg")

            if not os.path.isfile(image_path):
                await callback_query.message.answer("Файл изображения не найден.")
                return

            with open(image_path, "rb") as image_file:
                await callback_query.message.answer_photo(
                    photo=BufferedInputFile(image_file.read(), filename="pic_view.jpg"),
                    caption=response_message,
                    reply_markup=kb,
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
        await handle_error(tg_id, callback_query, f"Ошибка при обновлении подписки: {e}")


@router.callback_query(F.data.startswith("delete_key|"))
async def process_callback_delete_key(callback_query: types.CallbackQuery):
    client_id = callback_query.data.split("|")[1]
    try:
        # Build keyboard
        kb = build_key_delete_kb(client_id)

        # Answer message
        await callback_query.message.answer(
            text="<b>Вы уверены, что хотите удалить ключ?</b>",
            reply_markup=kb,
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

            # Build keyboard
            kb = build_renewal_plans_kb(client_id)

            balance = await get_balance(tg_id)
            response_message = PLAN_SELECTION_MSG.format(
                balance=balance,
                expiry_date=datetime.utcfromtimestamp(expiry_time / 1000).strftime("%Y-%m-%d %H:%M:%S"),
            )

            await callback_query.message.answer(
                text=response_message,
                reply_markup=kb,
            )
        else:
            await callback_query.message.answer("<b>Ключ не найден.</b>")
    except Exception as e:
        logger.error(e)


@router.callback_query(F.data.startswith("confirm_delete|"))
async def process_callback_confirm_delete(callback_query: types.CallbackQuery, session: Any):
    email = callback_query.data.split("|")[1]
    try:
        record = await session.fetchrow("SELECT client_id FROM keys WHERE email = $1", email)

        if record:
            client_id = record["client_id"]
            response_message = "Ключ успешно удален."
            back_button = types.InlineKeyboardButton(text="Назад", callback_data="view_keys")
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
                        tasks.append(delete_key_from_cluster(cluster_id, email, client_id))

                    await asyncio.gather(*tasks)

                except Exception as e:
                    logger.error(f"Ошибка при удалении ключа {client_id}: {e}")

            asyncio.create_task(delete_key_from_servers())

            await delete_key_from_db(client_id, session)

        else:
            # Build keyboard
            kb = build_back_kb("view_keys")

            # Answer message
            await callback_query.message.answer(
                text="Ключ не найден или уже удален.",
                reply_markup=kb,
            )
    except Exception as e:
        logger.error(e)


@router.callback_query(F.data.startswith("renew_plan|"))
async def process_callback_renew_plan(callback_query: types.CallbackQuery, session: Any):
    tg_id = callback_query.message.chat.id
    plan, client_id = (
        callback_query.data.split("|")[1],
        callback_query.data.split("|")[2],
    )
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
                # Build keyboard
                kb = build_top_up_kb()

                # Answer message
                await callback_query.message.answer(
                    text=INSUFFICIENT_FUNDS_MSG,
                    reply_markup=kb,
                )
                return

            response_message = SUCCESS_RENEWAL_MSG.format(months=RENEWAL_PLANS[plan]["months"])

            # Build keyboard
            kb = build_back_kb("profile", "👤 Личный кабинет")

            # Answer message
            await callback_query.message.answer(
                text=response_message,
                reply_markup=kb
            )

            servers = await get_servers_from_db()

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

            await renew_key_on_servers()

        else:
            await callback_query.message.answer(KEY_NOT_FOUND_MSG)
    except Exception as e:
        logger.error(e)

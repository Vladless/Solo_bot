from datetime import datetime, timedelta
from typing import Any

import asyncpg

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import bot
from config import (
    DATABASE_URL,
    USE_COUNTRY_SELECTION,
    USE_NEW_PAYMENT_FLOW,
)
from database import (
    check_server_name_by_cluster,
    create_temporary_data,
    get_balance,
    get_key_by_server,
    get_key_details,
    update_balance,
    update_key_expiry,
)
from handlers.buttons import (
    BACK,
    MAIN_MENU,
    PAYMENT,
)
from handlers.keys.key_utils import (
    renew_key_in_cluster,
)
from handlers.payments.robokassa_pay import handle_custom_amount_input
from handlers.payments.yookassa_pay import process_custom_amount_input
from handlers.texts import (
    INSUFFICIENT_FUNDS_RENEWAL_MSG,
    KEY_NOT_FOUND_MSG,
    PLAN_SELECTION_MSG,
    SUCCESS_RENEWAL_MSG,
)
from handlers.utils import edit_or_send_message, format_months, format_days
from logger import logger


router = Router()


@router.callback_query(F.data.startswith("renew_key|"))
async def process_callback_renew_key(callback_query: CallbackQuery, session: Any):
    tg_id = callback_query.message.chat.id
    key_name = callback_query.data.split("|")[1]
    try:
        record = await get_key_details(key_name, session)
        if not record:
            await callback_query.message.answer("<b>Ключ не найден.</b>")
            return

        client_id = record["client_id"]
        expiry_time = record["expiry_time"]
        server_id = record["server_id"]

        logger.info(f"[RENEW] Получение тарифной группы для server_id={server_id}")

        row = await session.fetchrow(
            """
            SELECT tariff_group FROM servers 
            WHERE id::text = $1 OR server_name = $1 OR cluster_name = $1 
            LIMIT 1
        """,
            server_id,
        )

        if not row or not row["tariff_group"]:
            logger.warning(f"[RENEW] Тарифная группа не найдена для server_id={server_id}")
            await callback_query.message.answer("❌ Не удалось определить тарифную группу.")
            return

        tariff_group = row["tariff_group"]
        logger.info(f"[RENEW] Найдена тарифная группа '{tariff_group}' для server_id={server_id}")

        tariffs = await session.fetch(
            """
            SELECT * FROM tariffs 
            WHERE group_code = $1 AND is_active = TRUE
            ORDER BY duration_days
        """,
            tariff_group,
        )

        if not tariffs:
            logger.warning(f"[RENEW] Нет активных тарифов для группы '{tariff_group}'")
            await callback_query.message.answer("❌ Нет доступных тарифов для этой группы.")
            return

        builder = InlineKeyboardBuilder()
        for t in tariffs:
            button_text = f"📅 {t['name']} — {t['price_rub']}₽"
            builder.row(
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=f"renew_plan|{t['id']}|{client_id}",
                )
            )

        builder.row(InlineKeyboardButton(text=BACK, callback_data=f"view_key|{record['email']}"))

        balance = await get_balance(tg_id)
        response_message = PLAN_SELECTION_MSG.format(
            balance=balance,
            expiry_date=datetime.utcfromtimestamp(expiry_time / 1000).strftime("%Y-%m-%d %H:%M:%S"),
        )

        await edit_or_send_message(
            target_message=callback_query.message,
            text=response_message,
            reply_markup=builder.as_markup(),
        )

    except Exception as e:
        logger.error(f"[RENEW] Ошибка в process_callback_renew_key для tg_id={tg_id}: {e}")
        await callback_query.message.answer("❌ Произошла ошибка при обработке. Попробуйте позже.")


@router.callback_query(F.data.startswith("renew_plan|"))
async def process_callback_renew_plan(callback_query: CallbackQuery, session: Any):
    tg_id = callback_query.message.chat.id
    tariff_id, client_id = callback_query.data.split("|")[1:]
    tariff_id = int(tariff_id)

    try:
        tariff = await session.fetchrow("SELECT * FROM tariffs WHERE id = $1 AND is_active = TRUE", tariff_id)
        if not tariff:
            await callback_query.message.answer("❌ Тариф не найден или отключён.")
            return

        duration_days = tariff["duration_days"]
        cost = tariff["price_rub"]
        total_gb = tariff["traffic_limit"] or 0

        record = await get_key_by_server(tg_id, client_id, session)
        if not record:
            await callback_query.message.answer(KEY_NOT_FOUND_MSG)
            logger.error(f"[RENEW] Ключ с client_id={client_id} не найден.")
            return

        email = record["email"]
        expiry_time = record["expiry_time"]
        current_time = datetime.utcnow().timestamp() * 1000

        if expiry_time <= current_time:
            new_expiry_time = int(current_time + timedelta(days=duration_days).total_seconds() * 1000)
        else:
            new_expiry_time = int(expiry_time + timedelta(days=duration_days).total_seconds() * 1000)

        balance = await get_balance(tg_id)
        if balance < cost:
            required_amount = cost - balance
            logger.info(f"[RENEW] Недостаточно средств: {required_amount}₽")

            await create_temporary_data(
                session,
                tg_id,
                "waiting_for_renewal_payment",
                {
                    "tariff_id": tariff_id,
                    "client_id": client_id,
                    "cost": cost,
                    "required_amount": required_amount,
                    "new_expiry_time": new_expiry_time,
                    "total_gb": total_gb,
                    "email": email,
                },
            )

            if USE_NEW_PAYMENT_FLOW == "YOOKASSA":
                await process_custom_amount_input(callback_query, session)
            elif USE_NEW_PAYMENT_FLOW == "ROBOKASSA":
                await handle_custom_amount_input(callback_query, session)
            else:
                builder = InlineKeyboardBuilder()
                builder.row(InlineKeyboardButton(text=PAYMENT, callback_data="pay"))
                builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
                await edit_or_send_message(
                    target_message=callback_query.message,
                    text=INSUFFICIENT_FUNDS_RENEWAL_MSG.format(required_amount=required_amount),
                    reply_markup=builder.as_markup(),
                )
            return

        logger.info(f"[RENEW] Продление ключа для пользователя {tg_id} на {duration_days} дней")
        await complete_key_renewal(tg_id, client_id, email, new_expiry_time, total_gb, cost, callback_query, tariff_id)

    except Exception as e:
        logger.error(f"[RENEW] Ошибка при продлении ключа для пользователя {tg_id}: {e}")


async def complete_key_renewal(tg_id, client_id, email, new_expiry_time, total_gb, cost, callback_query, tariff_id):
    conn = await asyncpg.connect(DATABASE_URL)

    try:
        logger.info(f"[Info] Продление ключа {client_id} по тарифу ID={tariff_id} (Start)")

        tariff = await conn.fetchrow("SELECT * FROM tariffs WHERE id = $1", tariff_id)
        if not tariff:
            logger.error(f"[Error] Тариф с id={tariff_id} не найден.")
            return

        if tariff["duration_days"] % 30 == 0:
            months_formatted = format_months(tariff["duration_days"] // 30)
        else:
            months_formatted = format_days(tariff["duration_days"])

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
        response_message = SUCCESS_RENEWAL_MSG.format(months_formatted=months_formatted)

        if callback_query:
            try:
                await edit_or_send_message(
                    target_message=callback_query.message,
                    text=response_message,
                    reply_markup=builder.as_markup(),
                )
            except Exception as e:
                logger.error(f"[Error] Ошибка при редактировании сообщения: {e}")
                await callback_query.message.answer(response_message, reply_markup=builder.as_markup())
        else:
            await bot.send_message(tg_id, response_message, reply_markup=builder.as_markup())

        key_info = await get_key_details(email, conn)
        if not key_info:
            logger.error(f"[Error] Ключ с client_id={client_id} не найден в БД.")
            return

        server_id = key_info["server_id"]
        if USE_COUNTRY_SELECTION:
            cluster_info = await check_server_name_by_cluster(server_id, conn)
            if not cluster_info:
                logger.error(f"[Error] Сервер {server_id} не найден в таблице servers.")
                return
            cluster_id = cluster_info["cluster_name"]
        else:
            cluster_id = server_id

        await renew_key_in_cluster(cluster_id, email, client_id, new_expiry_time, total_gb, hwid_device_limit=tariff["device_limit"])
        await update_key_expiry(client_id, new_expiry_time, conn)
        await update_balance(tg_id, -cost, conn)

        logger.info(f"[Info] Продление ключа {client_id} завершено успешно (User: {tg_id})")

    except Exception as e:
        logger.error(f"[Error] Ошибка в complete_key_renewal: {e}")
    finally:
        await conn.close()

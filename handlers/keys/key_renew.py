from datetime import datetime, timedelta
from typing import Any

import asyncpg

from aiogram import F, Router

from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import bot
from config import (
    DATABASE_URL,
    RENEWAL_PLANS,
    TOTAL_GB,
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
    DISCOUNTS,
    INSUFFICIENT_FUNDS_RENEWAL_MSG,
    KEY_NOT_FOUND_MSG,
    PLAN_SELECTION_MSG,
    SUCCESS_RENEWAL_MSG,
)
from handlers.utils import edit_or_send_message
from logger import logger


router = Router()


@router.callback_query(F.data.startswith("renew_key|"))
async def process_callback_renew_key(callback_query: CallbackQuery, session: Any):
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

                discount = DISCOUNTS.get(plan_id, 0) if isinstance(DISCOUNTS, dict) else 0

                button_text = f"📅 {months} месяц{'а' if months > 1 else ''} ({price} руб.)"
                if discount > 0:
                    button_text += f" {discount}% скидка"

                builder.row(
                    InlineKeyboardButton(
                        text=button_text,
                        callback_data=f"renew_plan|{months}|{client_id}",
                    )
                )

            builder.row(InlineKeyboardButton(text=BACK, callback_data="view_keys"))

            balance = await get_balance(tg_id)

            response_message = PLAN_SELECTION_MSG.format(
                balance=balance,
                expiry_date=datetime.utcfromtimestamp(expiry_time / 1000).strftime("%Y-%m-%d %H:%M:%S"),
            )

            await edit_or_send_message(
                target_message=callback_query.message,
                text=response_message,
                reply_markup=builder.as_markup(),
                media_path=None,
            )
        else:
            await callback_query.message.answer("<b>Ключ не найден.</b>")
    except Exception as e:
        logger.error(f"Ошибка в process_callback_renew_key: {e}")
        await callback_query.message.answer("❌ Произошла ошибка при обработке. Попробуйте позже.")


@router.callback_query(F.data.startswith("renew_plan|"))
async def process_callback_renew_plan(callback_query: CallbackQuery, session: Any):
    tg_id = callback_query.message.chat.id
    plan, client_id = callback_query.data.split("|")[1], callback_query.data.split("|")[2]
    days_to_extend = 30 * int(plan)

    total_gb = int((int(plan) or 1) * TOTAL_GB * 1024**3)

    try:
        record = await get_key_by_server(tg_id, client_id, session)

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
                    builder.row(InlineKeyboardButton(text=PAYMENT, callback_data="pay"))
                    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

                    await edit_or_send_message(
                        target_message=callback_query.message,
                        text=INSUFFICIENT_FUNDS_RENEWAL_MSG.format(required_amount=required_amount),
                        reply_markup=builder.as_markup(),
                        media_path=None,
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
    logger.info(f"[Info] Продление ключа {client_id} на {plan} мес. (Start)")

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
    response_message = SUCCESS_RENEWAL_MSG.format(months=plan)

    if callback_query:
        try:
            await edit_or_send_message(
                target_message=callback_query.message,
                text=response_message,
                reply_markup=builder.as_markup(),
                media_path=None,
            )
        except Exception as e:
            logger.error(f"[Error] Ошибка при редактировании сообщения: {e}")
            await callback_query.message.answer(response_message, reply_markup=builder.as_markup())
    else:
        await bot.send_message(tg_id, response_message, reply_markup=builder.as_markup())

    conn = await asyncpg.connect(DATABASE_URL)
    key_info = await get_key_details(email, conn)
    if not key_info:
        logger.error(f"[Error] Ключ с client_id={client_id} не найден в БД.")
        await conn.close()
        return

    server_id = key_info["server_id"]

    if USE_COUNTRY_SELECTION:
        cluster_info = await check_server_name_by_cluster(server_id, conn)
        if not cluster_info:
            logger.error(f"[Error] Сервер {server_id} не найден в таблице servers.")
            await conn.close()
            return
        cluster_id = cluster_info["cluster_name"]
    else:
        cluster_id = server_id

    await renew_key_in_cluster(cluster_id, email, client_id, new_expiry_time, total_gb)
    await update_key_expiry(client_id, new_expiry_time, conn)
    await update_balance(tg_id, -cost, conn)
    await conn.close()

    logger.info(f"[Info] Продление ключа {client_id} завершено успешно (User: {tg_id})")

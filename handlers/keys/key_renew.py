from datetime import datetime, timedelta
from typing import Any
from aiogram.fsm.context import FSMContext
from collections import defaultdict

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from math import ceil
import pytz

from bot import bot
from config import USE_NEW_PAYMENT_FLOW
from database import (
    create_temporary_data,
    get_balance,
    get_key_by_server,
    get_key_details,
    get_tariff_by_id,
    get_tariffs,
    update_balance,
    update_key_expiry,
    check_tariff_exists,
)
from database.models import Server, Key
from database.tariffs import create_subgroup_hash, find_subgroup_by_hash
from handlers.localization import get_user_texts, get_user_buttons, get_localized_month_for_user
from handlers.keys.key_utils import renew_key_in_cluster
from handlers.payments.robokassa_pay import handle_custom_amount_input
from handlers.payments.stars_pay import process_custom_amount_input_stars
from handlers.payments.yookassa_pay import process_custom_amount_input
from handlers.payments.yoomoney_pay import process_custom_amount_input_yoomoney
from handlers.utils import edit_or_send_message, format_days, format_months
from logger import logger

router = Router()
moscow_tz = pytz.timezone("Europe/Moscow")


@router.callback_query(F.data.startswith("renew_key|"))
async def process_callback_renew_key(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    tg_id = callback_query.message.chat.id
    key_name = callback_query.data.split("|")[1]

    texts = await get_user_texts(session, tg_id)
    buttons = await get_user_buttons(session, tg_id)

    try:
        record = await get_key_details(session, key_name)
        if not record:
            await callback_query.message.answer("<b>Ключ не найден.</b>")
            return

        client_id = record["client_id"]
        expiry_time = record["expiry_time"]
        server_id = record["server_id"]
        tariff_id = record.get("tariff_id")

        await state.update_data(
            renew_key_name=key_name,
            renew_client_id=client_id
        )

        logger.info(f"[RENEW] Получение тарифов для server_id={server_id}")

        try:
            server_id_int = int(server_id)
            filter_condition = or_(
                Server.id == server_id_int,
                Server.server_name == server_id,
                Server.cluster_name == server_id,
            )
        except ValueError:
            filter_condition = or_(
                Server.server_name == server_id,
                Server.cluster_name == server_id,
            )

        row = await session.execute(
            select(Server.tariff_group).where(filter_condition).limit(1)
        )
        row = row.first()
        if not row or not row[0]:
            logger.warning(f"[RENEW] Тарифная группа не найдена для server_id={server_id}")
            await callback_query.message.answer("❌ Не удалось определить тарифную группу.")
            return

        group_code = row[0]

        if tariff_id:
            if await check_tariff_exists(session, tariff_id):
                current_tariff = await get_tariff_by_id(session, tariff_id)
                if current_tariff["group_code"] not in ["discounts", "discounts_max", "gifts"]:
                    group_code = current_tariff["group_code"]

        tariffs = await get_tariffs(session, group_code=group_code)
        tariffs = [t for t in tariffs if t["is_active"]]
        if not tariffs:
            await callback_query.message.answer("❌ Нет доступных тарифов для продления.")
            return

        grouped_tariffs = defaultdict(list)
        for t in tariffs:
            subgroup = t.get("subgroup_title")
            grouped_tariffs[subgroup].append(t)

        builder = InlineKeyboardBuilder()

        for t in grouped_tariffs.get(None, []):
            builder.row(
                InlineKeyboardButton(
                    text=f"{t['name']} — {t['price_rub']}₽",
                    callback_data=f"renew_plan|{t['id']}",
                )
            )

        for subgroup in sorted(k for k in grouped_tariffs if k):
            subgroup_hash = create_subgroup_hash(subgroup, group_code)
            builder.row(
                InlineKeyboardButton(
                    text=subgroup,
                    callback_data=f"renew_subgroup|{subgroup_hash}",
                )
            )

        builder.row(InlineKeyboardButton(text=buttons.BACK, callback_data="renew_menu"))

        balance = await get_balance(session, tg_id)
        response_message = texts.PLAN_SELECTION_MSG.format(
            balance=balance,
            expiry_date=datetime.utcfromtimestamp(expiry_time / 1000).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        )

        await edit_or_send_message(
            target_message=callback_query.message,
            text=response_message,
            reply_markup=builder.as_markup(),
        )

    except Exception as e:
        logger.error(f"[RENEW] Ошибка в process_callback_renew_key для tg_id={tg_id}: {e}")
        await callback_query.message.answer("❌ Произошла ошибка при обработке. Попробуйте позже.")


@router.callback_query(F.data.startswith("renew_subgroup|"))
async def show_tariffs_in_renew_subgroup(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    try:
        subgroup_hash = callback.data.split("|")[1]
        tg_id = callback.from_user.id
        buttons = await get_user_buttons(session, tg_id)

        data = await state.get_data()
        client_id = data.get("renew_client_id")
        key_name = data.get("renew_key_name")

        if not client_id or not key_name:
            await callback.message.answer("❌ Данные для подгруппы не найдены.")
            return

        record = await get_key_details(session, key_name)
        if not record:
            await callback.message.answer("❌ Ключ не найден.")
            return

        server_id = record["server_id"]
        try:
            server_id_int = int(server_id)
            filter_condition = or_(
                Server.id == server_id_int,
                Server.server_name == server_id,
                Server.cluster_name == server_id,
            )
        except ValueError:
            filter_condition = or_(
                Server.server_name == server_id,
                Server.cluster_name == server_id,
            )

        row = await session.execute(
            select(Server.tariff_group).where(filter_condition).limit(1)
        )
        row = row.first()
        if not row or not row[0]:
            logger.warning(f"[RENEW_SUBGROUP] Тарифная группа не найдена для server_id={server_id}")
            await callback.message.answer("❌ Не удалось определить тарифную группу.")
            return

        group_code = row[0]

        subgroup = await find_subgroup_by_hash(session, subgroup_hash, group_code)
        if not subgroup:
            await callback.message.answer("❌ Подгруппа не найдена.")
            return
            
        tariffs = await get_tariffs(session, group_code=group_code)
        filtered = [t for t in tariffs if t["subgroup_title"] == subgroup and t["is_active"]]

        tariffs = await get_tariffs(session, group_code=group_code, subgroup_title=subgroup)
        tariffs = [t for t in tariffs if t["is_active"]]

        builder = InlineKeyboardBuilder()
        for t in filtered:
            builder.row(
                InlineKeyboardButton(
                    text=f"{t['name']} — {t['price_rub']}₽",
                    callback_data=f"renew_plan|{t['id']}",
                )
            )

        builder.row(
            InlineKeyboardButton(text=buttons.BACK, callback_data="renew_menu")
        )
        builder.row(InlineKeyboardButton(text=buttons.MAIN_MENU, callback_data="profile"))

        await edit_or_send_message(
            target_message=callback.message,
            text=f"<b>{subgroup}</b>\n\nВыберите тариф:",
            reply_markup=builder.as_markup(),
        )

    except Exception as e:
        logger.error(f"[RENEW_SUBGROUP] Ошибка при отображении подгруппы: {e}")
        await callback.message.answer("❌ Произошла ошибка при отображении тарифов.")


@router.callback_query(F.data.startswith("renew_plan|"))
async def process_callback_renew_plan(callback_query: CallbackQuery, state: FSMContext, session: Any):
    tg_id = callback_query.from_user.id
    tariff_id = callback_query.data.split("|")[1]
    tariff_id = int(tariff_id)

    texts = await get_user_texts(session, tg_id)
    buttons = await get_user_buttons(session, tg_id)

    data = await state.get_data()
    client_id = data.get("renew_client_id")
    key_name = data.get("renew_key_name")

    if not client_id or not key_name:
        await callback_query.message.answer("❌ Данные для продления не найдены.")
        return

    try:
        tariff = await get_tariff_by_id(session, tariff_id)
        if not tariff or not tariff["is_active"]:
            await callback_query.message.answer("❌ Тариф не найден или отключён.")
            return

        duration_days = tariff["duration_days"]
        cost = tariff["price_rub"]
        total_gb = tariff["traffic_limit"] or 0

        record = await get_key_by_server(session, tg_id, client_id)
        if not record:
            await callback_query.message.answer(texts.KEY_NOT_FOUND_MSG)
            logger.error(f"[RENEW] Ключ с client_id={client_id} не найден.")
            return

        email = record["email"]
        expiry_time = record["expiry_time"]
        current_time = datetime.utcnow().timestamp() * 1000

        if expiry_time <= current_time:
            new_expiry_time = int(
                current_time + timedelta(days=duration_days).total_seconds() * 1000
            )
        else:
            new_expiry_time = int(
                expiry_time + timedelta(days=duration_days).total_seconds() * 1000
            )

        balance = round(await get_balance(session, tg_id), 2)
        cost = round(cost, 2)
        if balance < cost:
            required_amount = ceil(cost - balance)
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
                await handle_custom_amount_input(message=callback_query, session=session)
            elif USE_NEW_PAYMENT_FLOW == "STARS":
                await process_custom_amount_input_stars(callback_query, session)
            elif USE_NEW_PAYMENT_FLOW == "YOOMONEY":
                await process_custom_amount_input_yoomoney(callback_query, session)
            else:
                builder = InlineKeyboardBuilder()
                builder.row(InlineKeyboardButton(text=buttons.PAYMENT, callback_data="pay"))
                builder.row(
                    InlineKeyboardButton(text=buttons.MAIN_MENU, callback_data="profile")
                )
                await edit_or_send_message(
                    target_message=callback_query.message,
                    text=texts.INSUFFICIENT_FUNDS_RENEWAL_MSG.format(
                        required_amount=required_amount
                    ),
                    reply_markup=builder.as_markup(),
                )
            return

        logger.info(
            f"[RENEW] Продление ключа для пользователя {tg_id} на {duration_days} дней"
        )
        await complete_key_renewal(
            session,
            tg_id,
            client_id,
            email,
            new_expiry_time,
            total_gb,
            cost,
            callback_query,
            tariff_id,
        )

    except Exception as e:
        logger.error(
            f"[RENEW] Ошибка при продлении ключа для пользователя {tg_id}: {e}"
        )


async def resolve_cluster_name(
    session: AsyncSession, server_or_cluster: str
) -> str | None:
    result = await session.execute(
        select(Server).where(Server.cluster_name == server_or_cluster).limit(1)
    )
    server = result.scalars().first()
    if server:
        return server_or_cluster

    result = await session.execute(
        select(Server.cluster_name)
        .where(Server.server_name == server_or_cluster)
        .limit(1)
    )
    row = result.scalar()
    return row


async def complete_key_renewal(
    session: AsyncSession,
    tg_id: int,
    client_id: str,
    email: str,
    new_expiry_time: int,
    total_gb: int,
    cost: float,
    callback_query: CallbackQuery | None,
    tariff_id: int,
):
    try:
        logger.info(
            f"[Info] Продление ключа {client_id} по тарифу ID={tariff_id} (Start)"
        )

        texts = await get_user_texts(session, tg_id)
        buttons = await get_user_buttons(session, tg_id)

        tariff = await get_tariff_by_id(session, tariff_id)
        if not tariff:
            logger.error(f"[Error] Тариф с id={tariff_id} не найден.")
            return

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=buttons.MY_SUB, callback_data=f"view_key|{email}"))
        
        formatted_expiry_date = datetime.fromtimestamp(
            new_expiry_time / 1000, tz=moscow_tz
        ).strftime("%d %B %Y, %H:%M")
        
        formatted_expiry_date = formatted_expiry_date.replace(
            datetime.fromtimestamp(new_expiry_time / 1000, tz=moscow_tz).strftime("%B"),
            await get_localized_month_for_user(session, tg_id, datetime.fromtimestamp(new_expiry_time / 1000, tz=moscow_tz))
        )

        response_message = texts.get_renewal_message(
            tariff_name=tariff["name"],
            traffic_limit=tariff.get("traffic_limit") if tariff.get("traffic_limit") is not None else 0,
            device_limit=tariff.get("device_limit") if tariff.get("device_limit") is not None else 0,
            expiry_date=formatted_expiry_date,
            subgroup_title=tariff.get("subgroup_title", "")
        )

        if callback_query:
            try:
                await edit_or_send_message(
                    target_message=callback_query.message,
                    text=response_message,
                    reply_markup=builder.as_markup(),
                )
            except Exception as e:
                logger.error(f"[Error] Ошибка при редактировании сообщения: {e}")
                await callback_query.message.answer(
                    response_message, reply_markup=builder.as_markup()
                )
        else:
            await bot.send_message(
                tg_id, response_message, reply_markup=builder.as_markup()
            )

        key_info = await get_key_details(session, email)
        if not key_info:
            logger.error(f"[Error] Ключ с client_id={client_id} не найден в БД.")
            return

        server_or_cluster = key_info["server_id"]
        cluster_id = await resolve_cluster_name(session, server_or_cluster)

        if not cluster_id:
            logger.error(f"[Error] Кластер для {server_or_cluster} не найден.")
            return

        await renew_key_in_cluster(
            cluster_id,
            email,
            client_id,
            new_expiry_time,
            total_gb,
            session,
            hwid_device_limit=tariff["device_limit"],
        )

        await update_key_expiry(session, client_id, new_expiry_time)
        await session.execute(
            update(Key)
            .where(Key.client_id == client_id)
            .values(tariff_id=tariff_id)
        )
        await update_balance(session, tg_id, -cost)

        logger.info(
            f"[Info] Продление ключа {client_id} завершено успешно (User: {tg_id})"
        )

    except Exception as e:
        logger.error(f"[Error] Ошибка в complete_key_renewal: {e}")
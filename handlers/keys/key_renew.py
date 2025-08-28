from collections import defaultdict
from datetime import datetime, timedelta
from math import ceil
from typing import Any

import pytz

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot import bot
from config import USE_NEW_PAYMENT_FLOW, DISCOUNT_ACTIVE_HOURS
from database import (
    check_tariff_exists,
    create_temporary_data,
    get_balance,
    get_key_by_server,
    get_key_details,
    get_tariff_by_id,
    get_tariffs,
    update_balance,
    update_key_expiry,
)
from database.models import Key, Server
from database.notifications import check_hot_lead_discount
from database.tariffs import create_subgroup_hash, find_subgroup_by_hash, get_tariffs
from handlers.buttons import BACK, MAIN_MENU, MY_SUB, PAYMENT
from handlers.keys.operations import renew_key_in_cluster
from handlers.payments.robokassa_pay import handle_custom_amount_input
from handlers.payments.stars_pay import process_custom_amount_input_stars
from handlers.payments.wata import handle_custom_amount_input as handle_custom_amount_input_wata
from handlers.payments.yookassa_pay import process_custom_amount_input
from handlers.payments.yoomoney_pay import process_custom_amount_input_yoomoney
from handlers.texts import (
    INSUFFICIENT_FUNDS_RENEWAL_MSG,
    KEY_NOT_FOUND_MSG,
    PLAN_SELECTION_MSG,
    get_renewal_message,
)
from handlers.utils import edit_or_send_message, get_russian_month, format_discount_time_left
from hooks.hooks import run_hooks
from hooks.hook_buttons import insert_hook_buttons
from logger import logger
from utils.modules_loader import load_module_fast_flow_handlers


router = Router()
moscow_tz = pytz.timezone("Europe/Moscow")


@router.callback_query(F.data.startswith("renew_key|"))
async def process_callback_renew_key(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    tg_id = callback_query.message.chat.id
    key_name = callback_query.data.split("|")[1]

    try:
        record = await get_key_details(session, key_name)
        if not record:
            await callback_query.message.answer("<b>Ключ не найден.</b>")
            return

        client_id = record["client_id"]
        expiry_time = record["expiry_time"]
        server_id = record["server_id"]
        tariff_id = record.get("tariff_id")

        await state.update_data(renew_key_name=key_name, renew_client_id=client_id)

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

        row = await session.execute(select(Server.tariff_group).where(filter_condition).limit(1))
        row = row.first()
        if not row or not row[0]:
            logger.warning(f"[RENEW] Тарифная группа не найдена для server_id={server_id}")
            await callback_query.message.answer("❌ Не удалось определить тарифную группу.")
            return

        group_code = row[0]

        if tariff_id:
            if await check_tariff_exists(session, tariff_id):
                current_tariff = await get_tariff_by_id(session, tariff_id)
                if current_tariff["group_code"] not in ["discounts", "discounts_max", "gifts", "trial"]:
                    group_code = current_tariff["group_code"]

        discount_info = await check_hot_lead_discount(session, tg_id)
        
        if discount_info.get("available"):
            group_code = discount_info["tariff_group"]

        tariffs_data = await get_tariffs(session, group_code=group_code, with_subgroup_weights=True)
        tariffs = [t for t in tariffs_data['tariffs'] if t.get('is_active')]
        subgroup_weights = tariffs_data['subgroup_weights']
        
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

        sorted_subgroups = sorted(
            [k for k in grouped_tariffs if k],
            key=lambda x: (subgroup_weights.get(x, 999999), x)
        )
        
        for subgroup in sorted_subgroups:
            subgroup_hash = create_subgroup_hash(subgroup, group_code)
            builder.row(
                InlineKeyboardButton(
                    text=subgroup,
                    callback_data=f"renew_subgroup|{subgroup_hash}",
                )
            )

        builder.row(InlineKeyboardButton(text=BACK, callback_data="renew_menu"))

        try:
            hook_builder = InlineKeyboardBuilder()
            hook_builder.attach(builder)

            hook_commands = await run_hooks("renew_tariffs", chat_id=tg_id, admin=False, session=session)
            if hook_commands:
                hook_builder = insert_hook_buttons(hook_builder, hook_commands)
            
            final_markup = hook_builder.as_markup()
        except Exception as e:
            logger.warning(f"[RENEW] Ошибка при применении хуков: {e}")
            final_markup = builder.as_markup()

        balance = await get_balance(session, tg_id)

        discount_message = ""
        if discount_info.get("available"):
            discount_message = f"\n\n🎯 <b>ЭКСКЛЮЗИВНОЕ ПРЕДЛОЖЕНИЕ!</b>\n<blockquote>"
            if discount_info["type"] == "hot_lead_step_2":
                discount_message += "💎 <b>Вам открыт доступ к специальным тарифам</b> для продления\n"
                discount_message += "🚀 <b>Эксклюзивные предложения</b> - доступны только для вас!\n"
            else:
                discount_message += "💎 <b>Вам открыт доступ к МАКСИМАЛЬНО выгодным тарифам</b> для продления\n"
                discount_message += "🚀 <b>VIP предложения</b> - максимальная выгода!\n"
            
            expires_at = discount_info["expires_at"]
            discount_message += f"</blockquote>\n⏰ <b>Предложение действует только: {format_discount_time_left(expires_at - timedelta(hours=DISCOUNT_ACTIVE_HOURS), DISCOUNT_ACTIVE_HOURS)}, не упустите свой шанс!</b>"

        response_message = PLAN_SELECTION_MSG.format(
            balance=balance,
            expiry_date=datetime.utcfromtimestamp(expiry_time / 1000).strftime("%Y-%m-%d %H:%M:%S"),
        ) + discount_message

        await edit_or_send_message(
            target_message=callback_query.message,
            text=response_message,
            reply_markup=final_markup,
        )

    except Exception as e:
        logger.error(f"[RENEW] Ошибка в process_callback_renew_key для tg_id={tg_id}: {e}")
        await callback_query.message.answer("❌ Произошла ошибка при обработке. Попробуйте позже.")


@router.callback_query(F.data.startswith("renew_subgroup|"))
async def show_tariffs_in_renew_subgroup(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    try:
        subgroup_hash = callback.data.split("|")[1]

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

        row = await session.execute(select(Server.tariff_group).where(filter_condition).limit(1))
        row = row.first()
        if not row or not row[0]:
            logger.warning(f"[RENEW_SUBGROUP] Тарифная группа не найдена для server_id={server_id}")
            await callback.message.answer("❌ Не удалось определить тарифную группу.")
            return

        group_code = row[0]

        tg_id = callback.from_user.id
        discount_info = await check_hot_lead_discount(session, tg_id)
        
        if discount_info.get("available"):
            group_code = discount_info["tariff_group"]

        subgroup = await find_subgroup_by_hash(session, subgroup_hash, group_code)
        if not subgroup:
            await callback.message.answer("❌ Подгруппа не найдена.")
            return

        tariffs = await get_tariffs(session, group_code=group_code)
        filtered = [t for t in tariffs if t["subgroup_title"] == subgroup and t["is_active"]]

        if not filtered:
            await edit_or_send_message(
                target_message=callback.message,
                text="❌ В этой подгруппе пока нет тарифов.",
                reply_markup=None,
            )
            return

        builder = InlineKeyboardBuilder()
        for t in filtered:
            builder.row(
                InlineKeyboardButton(
                    text=f"{t['name']} — {t['price_rub']}₽",
                    callback_data=f"renew_plan|{t['id']}",
                )
            )

        builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"renew_key|{key_name}"))
        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

        try:
            hook_builder = InlineKeyboardBuilder()
            hook_builder.attach(builder)

            hook_commands = await run_hooks("renew_tariffs", chat_id=callback.from_user.id, admin=False, session=session)
            if hook_commands:
                hook_builder = insert_hook_buttons(hook_builder, hook_commands)
            
            final_markup = hook_builder.as_markup()
        except Exception as e:
            logger.warning(f"[RENEW_SUBGROUP] Ошибка при применении хуков: {e}")
            final_markup = builder.as_markup()

        discount_message = ""
        if discount_info.get("available"):
            discount_message = f"\n\n🎯 <b>ЭКСКЛЮЗИВНОЕ ПРЕДЛОЖЕНИЕ!</b>\n<blockquote>"
            if discount_info["type"] == "hot_lead_step_2":
                discount_message += "💎 <b>Вам открыт доступ к специальным тарифам</b> для продления\n"
                discount_message += "🚀 <b>Эксклюзивные предложения</b> - доступны только для вас!\n"
            else:
                discount_message += "💎 <b>Вам открыт доступ к МАКСИМАЛЬНО выгодным тарифам</b> для продления\n"
                discount_message += "🚀 <b>VIP предложения</b> - максимальная выгода!\n"
            
            expires_at = discount_info["expires_at"]
            discount_message += f"</blockquote>\n⏰ <b>Предложение действует только: {format_discount_time_left(expires_at - timedelta(hours=DISCOUNT_ACTIVE_HOURS), DISCOUNT_ACTIVE_HOURS)}, не упустите свой шанс!</b>"

        await edit_or_send_message(
            target_message=callback.message,
            text=f"<b>{subgroup}</b>\n\nВыберите тариф:{discount_message}",
            reply_markup=final_markup,
        )

    except Exception as e:
        logger.error(f"[RENEW_SUBGROUP] Ошибка при отображении подгруппы: {e}")
        await callback.message.answer("❌ Произошла ошибка при отображении тарифов.")


@router.callback_query(F.data.startswith("renew_plan|"))
async def process_callback_renew_plan(callback_query: CallbackQuery, state: FSMContext, session: Any):
    tg_id = callback_query.from_user.id
    tariff_id = callback_query.data.split("|")[1]
    tariff_id = int(tariff_id)

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

        discount_info = await check_hot_lead_discount(session, tg_id)
        if tariff.get("group_code") in ["discounts", "discounts_max"]:
            if not discount_info.get("available") or datetime.utcnow() >= discount_info["expires_at"]:
                builder = InlineKeyboardBuilder()
                builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
                
                await callback_query.message.answer(
                    "❌ Скидка недоступна или истекла. Пожалуйста, выберите тариф заново.",
                    reply_markup=builder.as_markup()
                )
                return

        duration_days = tariff["duration_days"]
        cost = tariff["price_rub"]
        total_gb = tariff["traffic_limit"] or 0

        record = await get_key_by_server(session, tg_id, client_id)
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

            module_fast_flow_handlers = load_module_fast_flow_handlers()
            flow_handled = False
            
            if USE_NEW_PAYMENT_FLOW in module_fast_flow_handlers:
                try:
                    handler = module_fast_flow_handlers[USE_NEW_PAYMENT_FLOW]
                    await handler(callback_query, session, state)
                    flow_handled = True
                except Exception as e:
                    logger.error(f"[RENEW] Ошибка в модульном обработчике быстрого флоу {USE_NEW_PAYMENT_FLOW}: {e}")

            if not flow_handled:
                if USE_NEW_PAYMENT_FLOW == "YOOKASSA":
                    await process_custom_amount_input(callback_query, session)
                elif USE_NEW_PAYMENT_FLOW == "ROBOKASSA":
                    await handle_custom_amount_input(message=callback_query, session=session)
                elif USE_NEW_PAYMENT_FLOW == "STARS":
                    await process_custom_amount_input_stars(callback_query, session)
                elif USE_NEW_PAYMENT_FLOW == "YOOMONEY":
                    await process_custom_amount_input_yoomoney(callback_query, session)
                elif USE_NEW_PAYMENT_FLOW == "WATA":
                    await state.update_data(wata_cassa="sbp", required_amount=required_amount)
                    await handle_custom_amount_input_wata(callback_query, state)
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
        logger.error(f"[RENEW] Ошибка при продлении ключа для пользователя {tg_id}: {e}")


async def resolve_cluster_name(session: AsyncSession, server_or_cluster: str) -> str | None:
    result = await session.execute(select(Server).where(Server.cluster_name == server_or_cluster).limit(1))
    server = result.scalars().first()
    if server:
        return server_or_cluster

    result = await session.execute(select(Server.cluster_name).where(Server.server_name == server_or_cluster).limit(1))
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
        logger.info(f"[Info] Продление ключа {client_id} по тарифу ID={tariff_id} (Start)")

        tariff = await get_tariff_by_id(session, tariff_id)
        if not tariff:
            logger.error(f"[Error] Тариф с id={tariff_id} не найден.")
            return

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=MY_SUB, callback_data=f"view_key|{email}"))

        try:
            hook_commands = await run_hooks("renewal_complete", chat_id=tg_id, admin=False, session=session, email=email, client_id=client_id)
            if hook_commands:
                builder = insert_hook_buttons(builder, hook_commands)
        except Exception as e:
            logger.warning(f"[RENEWAL_COMPLETE] Ошибка при применении хуков: {e}")

        formatted_expiry_date = datetime.fromtimestamp(new_expiry_time / 1000, tz=moscow_tz).strftime("%d %B %Y, %H:%M")

        formatted_expiry_date = formatted_expiry_date.replace(
            datetime.fromtimestamp(new_expiry_time / 1000, tz=moscow_tz).strftime("%B"),
            get_russian_month(datetime.fromtimestamp(new_expiry_time / 1000, tz=moscow_tz)),
        )

        response_message = get_renewal_message(
            tariff_name=tariff["name"],
            traffic_limit=tariff.get("traffic_limit") if tariff.get("traffic_limit") is not None else 0,
            device_limit=tariff.get("device_limit") if tariff.get("device_limit") is not None else 0,
            expiry_date=formatted_expiry_date,
            subgroup_title=tariff.get("subgroup_title", ""),
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
                await callback_query.message.answer(response_message, reply_markup=builder.as_markup())
        else:
            await bot.send_message(tg_id, response_message, reply_markup=builder.as_markup())

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
        await session.execute(update(Key).where(Key.client_id == client_id).values(tariff_id=tariff_id))
        await update_balance(session, tg_id, -cost)

        logger.info(f"[Info] Продление ключа {client_id} завершено успешно (User: {tg_id})")

    except Exception as e:
        logger.error(f"[Error] Ошибка в complete_key_renewal: {e}")

        logger.error(f"[Error] Ошибка в complete_key_renewal: {e}")

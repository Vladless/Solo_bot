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
from config import DISCOUNT_ACTIVE_HOURS, RENEW_BUTTON_BEFORE_DAYS, USE_NEW_PAYMENT_FLOW
from database import (
    check_tariff_exists,
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
from handlers.payments.currency_rates import format_for_user
from handlers.payments.fast_payment_flow import try_fast_payment_flow
from handlers.texts import (
    DISCOUNT_OFFER_MESSAGE,
    DISCOUNT_OFFER_STEP2,
    DISCOUNT_OFFER_STEP3,
    INSUFFICIENT_FUNDS_RENEWAL_MSG,
    KEY_NOT_FOUND_MSG,
    PLAN_SELECTION_MSG,
    get_renewal_message,
)
from handlers.utils import edit_or_send_message, format_discount_time_left, get_russian_month
from hooks.hook_buttons import insert_hook_buttons
from hooks.hooks import run_hooks
from logger import logger


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

        expiry_utc = datetime.utcfromtimestamp(expiry_time / 1000).replace(tzinfo=pytz.UTC)
        available_from_utc = expiry_utc - timedelta(days=RENEW_BUTTON_BEFORE_DAYS)
        now_utc = datetime.utcnow().replace(tzinfo=pytz.UTC)

        if now_utc < available_from_utc:
            dt_msk = available_from_utc.astimezone(moscow_tz).strftime("%d.%m.%Y %H:%M")
            kb = InlineKeyboardBuilder()
            kb.row(InlineKeyboardButton(text=BACK, callback_data=f"view_key|{key_name}"))

            try:
                hook_commands = await run_hooks(
                    "process_callback_renew_key", callback_query=callback_query, state=state, session=session
                )
                if hook_commands:
                    kb = insert_hook_buttons(kb, hook_commands)
            except Exception as e:
                logger.warning(f"[RENEW] Ошибка при применении хуков: {e}")

            await edit_or_send_message(
                target_message=callback_query.message,
                text=f"Продление доступно с {dt_msk}",
                reply_markup=kb.as_markup(),
            )
            return

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

                forbidden_groups = ["discounts", "discounts_max", "gifts", "trial"]

                try:
                    hook_results = await run_hooks(
                        "renewal_forbidden_groups", chat_id=tg_id, admin=False, session=session
                    )
                    for hook_result in hook_results:
                        additional_groups = hook_result.get("additional_groups", [])
                        forbidden_groups.extend(additional_groups)
                except Exception as e:
                    logger.warning(f"[RENEW] Ошибка при получении дополнительных групп: {e}")

                if current_tariff["group_code"] not in forbidden_groups:
                    group_code = current_tariff["group_code"]

        discount_info = await check_hot_lead_discount(session, tg_id)

        if discount_info.get("available"):
            group_code = discount_info["tariff_group"]

        try:
            hook_results = await run_hooks(
                "purchase_tariff_group_override", chat_id=tg_id, admin=False, session=session, original_group=group_code
            )
            for hook_result in hook_results:
                if hook_result.get("override_group"):
                    group_code = hook_result["override_group"]
                    logger.info(f"[RENEW] Тарифная группа переопределена хуком для продления: {group_code}")
                    break
        except Exception as e:
            logger.warning(f"[RENEW] Ошибка при применении хуков переопределения группы: {e}")

        tariffs_data = await get_tariffs(session, group_code=group_code, with_subgroup_weights=True)
        tariffs = [t for t in tariffs_data["tariffs"] if t.get("is_active")]
        subgroup_weights = tariffs_data["subgroup_weights"]

        if not tariffs:
            await callback_query.message.answer("❌ Нет доступных тарифов для продления.")
            return

        grouped_tariffs = defaultdict(list)
        for t in tariffs:
            subgroup = t.get("subgroup_title")
            grouped_tariffs[subgroup].append(t)

        builder = InlineKeyboardBuilder()

        language_code = getattr(callback_query.from_user, "language_code", None)

        for t in grouped_tariffs.get(None, []):
            price_text = await format_for_user(session, tg_id, t["price_rub"], language_code)
            builder.row(
                InlineKeyboardButton(
                    text=f"{t['name']} — {price_text}",
                    callback_data=f"renew_plan|{t['id']}",
                )
            )

        sorted_subgroups = sorted([k for k in grouped_tariffs if k], key=lambda x: (subgroup_weights.get(x, 999999), x))

        for subgroup in sorted_subgroups:
            subgroup_hash = create_subgroup_hash(subgroup, group_code)
            builder.row(
                InlineKeyboardButton(
                    text=subgroup,
                    callback_data=f"renew_subgroup|{subgroup_hash}",
                )
            )

        builder.row(InlineKeyboardButton(text=BACK, callback_data=f"view_key|{key_name}"))

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

        balance_rub = await get_balance(session, tg_id) or 0
        balance = await format_for_user(session, tg_id, balance_rub, language_code)

        discount_message = ""
        if discount_info.get("available"):
            offer_text = DISCOUNT_OFFER_STEP2 if discount_info["type"] == "hot_lead_step_2" else DISCOUNT_OFFER_STEP3
            expires_at = discount_info["expires_at"]
            time_left = format_discount_time_left(
                expires_at - timedelta(hours=DISCOUNT_ACTIVE_HOURS), DISCOUNT_ACTIVE_HOURS
            )
            discount_message = DISCOUNT_OFFER_MESSAGE.format(offer_text=offer_text, time_left=time_left)

        response_message = (
            PLAN_SELECTION_MSG.format(
                balance=balance,
                expiry_date=datetime.utcfromtimestamp(expiry_time / 1000).strftime("%Y-%m-%d %H:%M:%S"),
            )
            + discount_message
        )

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
        language_code = callback.from_user.language_code
        discount_info = await check_hot_lead_discount(session, tg_id)

        if discount_info.get("available"):
            group_code = discount_info["tariff_group"]

        try:
            hook_results = await run_hooks(
                "purchase_tariff_group_override", chat_id=tg_id, admin=False, session=session, original_group=group_code
            )
            for hook_result in hook_results:
                if hook_result.get("override_group"):
                    group_code = hook_result["override_group"]
                    logger.info(f"[RENEW_SUBGROUP] Тарифная группа переопределена хуком: {group_code}")
                    break
        except Exception as e:
            logger.warning(f"[RENEW_SUBGROUP] Ошибка при применении хуков переопределения группы: {e}")

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
            price_txt = await format_for_user(session, tg_id, t.get("price_rub", 0), language_code)
            builder.row(
                InlineKeyboardButton(
                    text=f"{t['name']} — {price_txt}",
                    callback_data=f"renew_plan|{t['id']}",
                )
            )

        builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"renew_key|{key_name}"))
        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

        try:
            hook_builder = InlineKeyboardBuilder()
            hook_builder.attach(builder)

            hook_commands = await run_hooks(
                "renew_tariffs", chat_id=callback.from_user.id, admin=False, session=session
            )
            if hook_commands:
                hook_builder = insert_hook_buttons(hook_builder, hook_commands)

            final_markup = hook_builder.as_markup()
        except Exception as e:
            logger.warning(f"[RENEW_SUBGROUP] Ошибка при применении хуков: {e}")
            final_markup = builder.as_markup()

        discount_message = ""
        if discount_info.get("available"):
            offer_text = DISCOUNT_OFFER_STEP2 if discount_info["type"] == "hot_lead_step_2" else DISCOUNT_OFFER_STEP3
            expires_at = discount_info["expires_at"]
            time_left = format_discount_time_left(
                expires_at - timedelta(hours=DISCOUNT_ACTIVE_HOURS), DISCOUNT_ACTIVE_HOURS
            )
            discount_message = DISCOUNT_OFFER_MESSAGE.format(offer_text=offer_text, time_left=time_left)

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
                    reply_markup=builder.as_markup(),
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

            if USE_NEW_PAYMENT_FLOW:
                handled = await try_fast_payment_flow(
                    callback_query,
                    session,
                    state,
                    tg_id=tg_id,
                    temp_key="waiting_for_renewal_payment",
                    temp_payload={
                        "tariff_id": tariff_id,
                        "client_id": client_id,
                        "cost": cost,
                        "required_amount": required_amount,
                        "new_expiry_time": new_expiry_time,
                        "total_gb": total_gb,
                        "email": email,
                    },
                    required_amount=required_amount,
                )
                if handled:
                    return

            language_code = getattr(callback_query.from_user, "language_code", None)
            required_amount_text = await format_for_user(session, tg_id, float(required_amount), language_code)

            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text=PAYMENT, callback_data="pay"))
            builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
            await edit_or_send_message(
                target_message=callback_query.message,
                text=INSUFFICIENT_FUNDS_RENEWAL_MSG.format(required_amount=required_amount_text),
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

        waiting_message = None
        wait_text = "⏳ Подождите. Идет продление подписки…"

        try:
            if callback_query:
                await edit_or_send_message(
                    target_message=callback_query.message,
                    text=wait_text,
                    reply_markup=None,
                )
            else:
                waiting_message = await bot.send_message(tg_id, wait_text)
        except Exception as e:
            logger.warning(f"[Renew] Не удалось показать экран ожидания: {e}")

        tariff = await get_tariff_by_id(session, tariff_id)
        if not tariff:
            logger.error(f"[Error] Тариф с id={tariff_id} не найден.")
            return

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

        key_info = await get_key_details(session, email)
        if not key_info:
            logger.error(f"[Error] Ключ с client_id={client_id} не найден в БД.")
            return

        current_subgroup = None
        try:
            current_tariff_id = key_info.get("tariff_id")
            if current_tariff_id:
                current_tariff = await get_tariff_by_id(session, int(current_tariff_id))
                if current_tariff:
                    current_subgroup = current_tariff.get("subgroup_title")
        except Exception as e:
            logger.warning(f"[Renew] Не удалось определить текущую подгруппу: {e}")

        target_subgroup = tariff.get("subgroup_title")
        old_subgroup = current_subgroup

        server_or_cluster = key_info["server_id"]
        cluster_id = await resolve_cluster_name(session, server_or_cluster)
        if not cluster_id:
            logger.error(f"[Error] Кластер для {server_or_cluster} не найден.")
            return

        await renew_key_in_cluster(
            cluster_id=cluster_id,
            email=email,
            client_id=client_id,
            new_expiry_time=new_expiry_time,
            total_gb=total_gb,
            session=session,
            hwid_device_limit=tariff.get("device_limit") if tariff.get("device_limit") is not None else 0,
            reset_traffic=True,
            target_subgroup=target_subgroup,
            old_subgroup=old_subgroup,
            plan=tariff_id,
        )

        key_row = await get_key_details(session, email)
        effective_client_id = key_row["client_id"] if key_row else client_id

        await update_key_expiry(session, effective_client_id, new_expiry_time)
        await session.execute(update(Key).where(Key.email == email).values(tariff_id=tariff_id))
        await update_balance(session, tg_id, -cost)

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=MY_SUB, callback_data=f"view_key|{email}"))
        try:
            hook_commands = await run_hooks(
                "renewal_complete", chat_id=tg_id, admin=False, session=session, email=email, client_id=client_id
            )
            if hook_commands:
                builder = insert_hook_buttons(builder, hook_commands)
        except Exception as e:
            logger.warning(f"[RENEWAL_COMPLETE] Ошибка при применении хуков: {e}")

        try:
            if callback_query:
                await edit_or_send_message(
                    target_message=callback_query.message,
                    text=response_message,
                    reply_markup=builder.as_markup(),
                )
            elif waiting_message:
                await edit_or_send_message(
                    target_message=waiting_message,
                    text=response_message,
                    reply_markup=builder.as_markup(),
                )
            else:
                await bot.send_message(tg_id, response_message, reply_markup=builder.as_markup())
        except Exception as e:
            logger.error(f"[Error] Ошибка при выводе финального сообщения: {e}")
            await bot.send_message(tg_id, response_message, reply_markup=builder.as_markup())

        logger.info(f"[Info] Продление ключа {client_id} завершено успешно (User: {tg_id})")

    except Exception as e:
        logger.error(f"[Error] Ошибка в complete_key_renewal: {e}")

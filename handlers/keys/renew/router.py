from collections import defaultdict
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Any

import pytz

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import NOTIFICATIONS_CONFIG
from core.settings.tariffs_config import normalize_tariff_config
from database import (
    check_tariff_exists,
    get_balance,
    get_key_by_server,
    get_key_details,
    get_tariff_by_id,
)
from database.models import Server
from database.notifications import check_cold_lead_discount, check_hot_lead_discount
from database.tariffs import create_subgroup_hash, find_subgroup_by_hash, get_subgroup_description, get_tariffs
from handlers.payments.fast_payment_flow import try_fast_payment_flow
from handlers.utils import edit_or_send_message, format_discount_time_left, render_text
from hooks.hook_buttons import insert_hook_buttons
from hooks.processors import (
    process_process_callback_renew_key,
    process_purchase_tariff_group_override,
    process_renew_tariffs,
    process_renewal_forbidden_groups,
)
from logger import logger
from services.payments.currency_rates import format_for_user
from settings.buttons import BACK, MAIN_MENU, PAYMENT
from settings.config import DISCOUNT_ACTIVE_HOURS, RENEW_BUTTON_BEFORE_DAYS, USE_NEW_PAYMENT_FLOW
from settings.texts import (
    ADDON_RESET_PLAN_WARNING,
    DISCOUNT_OFFER_MESSAGE,
    DISCOUNT_OFFER_STEP2,
    DISCOUNT_OFFER_STEP3,
    INSUFFICIENT_FUNDS_RENEWAL_MSG,
    KEY_NOT_FOUND_MSG,
    PLAN_SELECTION_HINT,
    PLAN_SELECTION_TEXT,
    SELECT_TARIFF_HINT,
    SUBGROUP_TITLE_TEMPLATE,
)

from .flow import _finalize_renewal, complete_key_renewal, normalize_expiry_ms
from .switch import _maybe_show_switch_confirm
from ..utils import (
    add_tariff_button_generic,
    build_key_callback,
    format_subgroup_description,
    format_tariff_descriptions,
    key_owned_by_user,
    order_tariff_items,
    resolve_key,
)


router = Router()
moscow_tz = pytz.timezone("Europe/Moscow")


@router.callback_query(F.data.startswith("renew_key|"), flags={"popup": True})
async def process_callback_renew_key(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    """Обрабатывает нажатие кнопки продления конкретного ключа."""
    tg_id = callback_query.message.chat.id
    key_ref = callback_query.data.split("|", 1)[1]
    key_obj = await resolve_key(session, callback_query.from_user.id, key_ref)
    key_name = key_obj.email if key_obj else key_ref

    try:
        record = await get_key_details(session, key_name)
        if not record:
            from handlers.keys.create.flow import handle_key_creation

            await handle_key_creation(
                tg_id=callback_query.from_user.id,
                state=state,
                session=session,
                message_or_query=callback_query,
            )
            return
        if not key_owned_by_user(record, callback_query.from_user.id):
            await callback_query.answer("Доступ запрещён.", show_alert=True)
            return

        client_id = record["client_id"]
        expiry_time_raw = record["expiry_time"]
        expiry_time = normalize_expiry_ms(expiry_time_raw)
        server_id = record["server_id"]
        tariff_id = record.get("tariff_id")

        renew_before_days = int(NOTIFICATIONS_CONFIG.get("RENEW_BUTTON_BEFORE_DAYS", RENEW_BUTTON_BEFORE_DAYS))
        expiry_utc = datetime.utcfromtimestamp(expiry_time / 1000).replace(tzinfo=pytz.UTC)
        available_from_utc = expiry_utc - timedelta(days=renew_before_days)
        now_utc = datetime.now(timezone.utc)

        if now_utc < available_from_utc:
            dt_msk = available_from_utc.astimezone(moscow_tz).strftime("%d.%m.%Y %H:%M")
            kb = InlineKeyboardBuilder()
            kb.row(InlineKeyboardButton(text=BACK, callback_data=build_key_callback("view_key", client_id, key_name)))

            hook_commands = await process_process_callback_renew_key(
                callback_query=callback_query, state=state, session=session
            )
            if hook_commands:
                kb = insert_hook_buttons(kb, hook_commands)

            await edit_or_send_message(
                target_message=callback_query.message,
                text=f"Продление доступно с {dt_msk}",
                reply_markup=kb.as_markup(),
            )
            return

        await state.update_data(renew_key_name=key_name, renew_client_id=client_id, renew_key_ref=key_ref)

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
        original_group_code = group_code

        if tariff_id:
            if await check_tariff_exists(session, tariff_id):
                current_tariff = await get_tariff_by_id(session, tariff_id)

                forbidden_groups = [
                    "discounts",
                    "discounts_max",
                    "cold_discounts",
                    "cold_discounts_max",
                    "gifts",
                    "trial",
                ]
                additional_groups = await process_renewal_forbidden_groups(chat_id=tg_id, admin=False, session=session)
                forbidden_groups.extend(additional_groups)

                if current_tariff["group_code"] not in forbidden_groups:
                    group_code = current_tariff["group_code"]
                    original_group_code = group_code

        discount_info = await check_hot_lead_discount(session, tg_id)
        if not discount_info.get("available"):
            cold_discount_info = await check_cold_lead_discount(session, tg_id)
            if cold_discount_info.get("available"):
                discount_info = cold_discount_info

        if discount_info.get("available"):
            group_code = discount_info["tariff_group"]

        override_result = await process_purchase_tariff_group_override(
            chat_id=tg_id, admin=False, session=session, original_group=group_code
        )
        if override_result:
            group_code = override_result["override_group"]
            logger.info(f"[RENEW] Тарифная группа переопределена хуком для продления: {group_code}")

        tariffs_data = await get_tariffs(session, group_code=group_code, with_subgroup_weights=True)
        tariffs = [t for t in tariffs_data["tariffs"] if t.get("is_active")]
        tariffs_data["subgroup_weights"]

        if not tariffs and discount_info.get("available"):
            logger.warning(f"[RENEW] Нет тарифов со скидкой {group_code}, fallback на {original_group_code}")
            group_code = original_group_code
            tariffs_data = await get_tariffs(session, group_code=group_code, with_subgroup_weights=True)
            tariffs = [t for t in tariffs_data["tariffs"] if t.get("is_active")]
            tariffs_data["subgroup_weights"]
            discount_info = {"available": False}

        if not tariffs:
            await callback_query.message.answer("❌ Нет доступных тарифов для продления.")
            return

        grouped_tariffs = defaultdict(list)
        for t in tariffs:
            subgroup = t.get("subgroup_title")
            grouped_tariffs[subgroup].append(t)

        builder = InlineKeyboardBuilder()

        language_code = getattr(callback_query.from_user, "language_code", None)

        for kind, payload in order_tariff_items(grouped_tariffs):
            if kind == "tariff":
                await add_tariff_button_generic(
                    builder=builder,
                    tariff=payload,
                    session=session,
                    tg_id=tg_id,
                    language_code=language_code,
                    callback_prefix="renew_plan",
                )
            else:
                subgroup_hash = create_subgroup_hash(payload, group_code)
                builder.row(
                    InlineKeyboardButton(
                        text=payload,
                        callback_data=f"renew_subgroup|{subgroup_hash}",
                    )
                )

        builder.row(InlineKeyboardButton(text=BACK, callback_data=build_key_callback("view_key", client_id, key_name)))

        hook_builder = InlineKeyboardBuilder()
        hook_builder.attach(builder)

        hook_commands = await process_renew_tariffs(chat_id=tg_id, admin=False, session=session)
        if hook_commands:
            hook_builder = insert_hook_buttons(hook_builder, hook_commands)

        final_markup = hook_builder.as_markup()

        balance_rub = await get_balance(session, tg_id) or 0
        balance = await format_for_user(session, tg_id, balance_rub, language_code)

        discount_message = ""
        if discount_info.get("available"):
            discount_active_hours = int(NOTIFICATIONS_CONFIG.get("DISCOUNT_ACTIVE_HOURS", DISCOUNT_ACTIVE_HOURS))
            offer_text = DISCOUNT_OFFER_STEP2 if discount_info["type"] == "hot_lead_step_2" else DISCOUNT_OFFER_STEP3
            expires_at = discount_info["expires_at"]
            time_left = format_discount_time_left(
                expires_at - timedelta(hours=discount_active_hours),
                discount_active_hours,
            )
            discount_message = DISCOUNT_OFFER_MESSAGE.format(offer_text=offer_text, time_left=time_left)

        addon_warning = ""
        _cur_dev = record.get("current_device_limit")
        _sel_dev = record.get("selected_device_limit")
        _cur_trf = record.get("current_traffic_limit")
        _sel_trf = record.get("selected_traffic_limit")
        _addon_parts: list[str] = []
        if _cur_dev is not None and _sel_dev is not None and int(_cur_dev) > int(_sel_dev):
            _addon_parts.append(f"+{int(_cur_dev) - int(_sel_dev)} устройств")
        if _cur_trf is not None and _sel_trf is not None and int(_cur_trf) > int(_sel_trf):
            _addon_parts.append(f"+{int(_cur_trf) - int(_sel_trf)} ГБ")
        if _addon_parts:
            addon_warning = ADDON_RESET_PLAN_WARNING.format(addons=", ".join(_addon_parts))

        expiry_moscow = datetime.fromtimestamp(expiry_time / 1000, tz=pytz.timezone("Europe/Moscow"))
        response_message = "\n".join(
            block
            for block in (
                render_text(
                    PLAN_SELECTION_TEXT,
                    balance=balance,
                    expiry=expiry_moscow.strftime("%d.%m.%Y %H:%M"),
                ),
                addon_warning,
                format_tariff_descriptions(grouped_tariffs.get(None, [])),
                discount_message,
                PLAN_SELECTION_HINT,
            )
            if block
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
    """Показывает тарифы внутри выбранной подгруппы при продлении."""
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
            from handlers.keys.create.flow import handle_key_creation

            await handle_key_creation(
                tg_id=callback.from_user.id,
                state=state,
                session=session,
                message_or_query=callback,
            )
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
        original_group_code = group_code

        tariff_id = record.get("tariff_id")
        if tariff_id:
            if await check_tariff_exists(session, tariff_id):
                current_tariff = await get_tariff_by_id(session, tariff_id)

                forbidden_groups = [
                    "discounts",
                    "discounts_max",
                    "cold_discounts",
                    "cold_discounts_max",
                    "gifts",
                    "trial",
                ]
                additional_groups = await process_renewal_forbidden_groups(
                    chat_id=callback.from_user.id, admin=False, session=session
                )
                forbidden_groups.extend(additional_groups)

                if current_tariff and current_tariff["group_code"] not in forbidden_groups:
                    group_code = current_tariff["group_code"]
                    original_group_code = group_code

        tg_id = callback.from_user.id
        language_code = callback.from_user.language_code
        discount_info = await check_hot_lead_discount(session, tg_id)
        if not discount_info.get("available"):
            cold_discount_info = await check_cold_lead_discount(session, tg_id)
            if cold_discount_info.get("available"):
                discount_info = cold_discount_info

        if discount_info.get("available"):
            group_code = discount_info["tariff_group"]

        override_result = await process_purchase_tariff_group_override(
            chat_id=tg_id, admin=False, session=session, original_group=group_code
        )
        if override_result:
            group_code = override_result["override_group"]
            logger.info(f"[RENEW_SUBGROUP] Тарифная группа переопределена хуком: {group_code}")

        subgroup = await find_subgroup_by_hash(session, subgroup_hash, group_code)
        if not subgroup:
            await callback.message.answer("❌ Подгруппа не найдена.")
            return

        tariffs = await get_tariffs(session, group_code=group_code)
        filtered = [t for t in tariffs if t["subgroup_title"] == subgroup and t["is_active"]]

        if not filtered and discount_info.get("available"):
            logger.warning(
                f"[RENEW_SUBGROUP] Нет тарифов со скидкой {group_code} в подгруппе '{subgroup}', fallback на {original_group_code}"
            )
            group_code = original_group_code
            subgroup = await find_subgroup_by_hash(session, subgroup_hash, group_code)
            if subgroup:
                tariffs = await get_tariffs(session, group_code=group_code)
                filtered = [t for t in tariffs if t["subgroup_title"] == subgroup and t["is_active"]]
            discount_info = {"available": False}

        if not filtered:
            await edit_or_send_message(
                target_message=callback.message,
                text="❌ В этой подгруппе пока нет тарифов.",
                reply_markup=None,
            )
            return

        builder = InlineKeyboardBuilder()
        for t in filtered:
            await add_tariff_button_generic(
                builder=builder,
                tariff=t,
                session=session,
                tg_id=tg_id,
                language_code=language_code,
                callback_prefix="renew_plan",
            )

        builder.row(
            InlineKeyboardButton(
                text=BACK,
                callback_data=build_key_callback("renew_key", data.get("renew_client_id"), key_name),
            )
        )
        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

        hook_builder = InlineKeyboardBuilder()
        hook_builder.attach(builder)

        hook_commands = await process_renew_tariffs(chat_id=callback.from_user.id, admin=False, session=session)
        if hook_commands:
            hook_builder = insert_hook_buttons(hook_builder, hook_commands)

        final_markup = hook_builder.as_markup()

        discount_message = ""
        if discount_info.get("available"):
            discount_active_hours = int(NOTIFICATIONS_CONFIG.get("DISCOUNT_ACTIVE_HOURS", DISCOUNT_ACTIVE_HOURS))
            offer_text = DISCOUNT_OFFER_STEP2 if discount_info["type"] == "hot_lead_step_2" else DISCOUNT_OFFER_STEP3
            expires_at = discount_info["expires_at"]
            time_left = format_discount_time_left(
                expires_at - timedelta(hours=discount_active_hours),
                discount_active_hours,
            )
            discount_message = DISCOUNT_OFFER_MESSAGE.format(offer_text=offer_text, time_left=time_left)

        sub_desc = await get_subgroup_description(session, group_code, subgroup)
        await edit_or_send_message(
            target_message=callback.message,
            text="\n".join(
                block
                for block in (
                    SUBGROUP_TITLE_TEMPLATE.format(subgroup=subgroup),
                    format_subgroup_description(sub_desc),
                    format_tariff_descriptions(filtered, total_limit=200),
                    discount_message,
                    SELECT_TARIFF_HINT,
                )
                if block
            ),
            reply_markup=final_markup,
        )

    except Exception as e:
        logger.error(f"[RENEW_SUBGROUP] Ошибка при отображении подгруппы: {e}")
        await callback.message.answer("❌ Произошла ошибка при отображении тарифов.")


@router.callback_query(F.data.startswith("renew_plan|"))
async def process_callback_renew_plan(callback_query: CallbackQuery, state: FSMContext, session: Any):
    """Обрабатывает выбор конкретного тарифа для продления."""
    tg_id = callback_query.from_user.id
    tariff_id = int(callback_query.data.split("|")[1])

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
            if not discount_info.get("available") or datetime.now(timezone.utc) >= discount_info["expires_at"]:
                builder = InlineKeyboardBuilder()
                builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
                await callback_query.message.answer(
                    "❌ Скидка недоступна или истекла. Пожалуйста, выберите тариф заново.",
                    reply_markup=builder.as_markup(),
                )
                return
        elif tariff.get("group_code") in ["cold_discounts", "cold_discounts_max"]:
            cold_discount_info = await check_cold_lead_discount(session, tg_id)
            if (
                not cold_discount_info.get("available")
                or datetime.now(timezone.utc) >= cold_discount_info["expires_at"]
            ):
                builder = InlineKeyboardBuilder()
                builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
                await callback_query.message.answer(
                    "❌ Скидка недоступна или истекла. Пожалуйста, выберите тариф заново.",
                    reply_markup=builder.as_markup(),
                )
                return

        duration_days = tariff["duration_days"]
        total_gb = tariff["traffic_limit"] or 0

        record = await get_key_by_server(session, tg_id, client_id)
        if not record:
            logger.info(f"[RENEW] Ключ client_id={client_id} удалён — открываем покупку нового.")
            from handlers.keys.create.flow import handle_key_creation

            await handle_key_creation(
                tg_id=callback_query.from_user.id,
                state=state,
                session=session,
                message_or_query=callback_query,
            )
            return

        email = record["email"]
        expiry_time_raw = record["expiry_time"]
        expiry_time = normalize_expiry_ms(expiry_time_raw)
        current_time = int(datetime.now(timezone.utc).timestamp() * 1000)

        from services.keys import compute_renewal_expiry

        new_expiry_time = await compute_renewal_expiry(
            session,
            now_ms=current_time,
            current_expiry_ms=expiry_time,
            current_tariff_id=record.get("tariff_id"),
            current_selected_device=record.get("selected_device_limit"),
            current_selected_traffic=record.get("selected_traffic_limit"),
            new_tariff_id=tariff_id,
            new_selected_device=None,
            new_selected_traffic=None,
            new_duration_days=duration_days,
        )

        if tariff.get("configurable"):
            cfg = normalize_tariff_config(tariff)

            traffic_options = cfg.get("traffic_options_gb") or tariff.get("traffic_options_gb") or []
            traffic_options_sorted = sorted([int(x) for x in traffic_options if str(x).isdigit()])

            device_options = cfg.get("device_options") or tariff.get("device_options") or []
            device_options_sorted = sorted([int(x) for x in device_options if str(x).isdigit()])

            selected_devices_db = record.get("selected_device_limit")
            selected_traffic_db = record.get("selected_traffic_limit")

            traffic_default = next(
                (x for x in traffic_options_sorted if x != 0),
                (traffic_options_sorted[0] if traffic_options_sorted else None),
            )
            device_default = next(
                (x for x in device_options_sorted if x != 0),
                (device_options_sorted[0] if device_options_sorted else None),
            )

            if traffic_options_sorted:
                selected_traffic_db = int(selected_traffic_db) if selected_traffic_db is not None else traffic_default
                if selected_traffic_db not in traffic_options_sorted:
                    selected_traffic_db = traffic_default
            else:
                selected_traffic_db = None

            if device_options_sorted:
                selected_devices_db = int(selected_devices_db) if selected_devices_db is not None else device_default
                if selected_devices_db not in device_options_sorted:
                    selected_devices_db = device_default
            else:
                selected_devices_db = None

            config_selected_devices = selected_devices_db
            if config_selected_devices is None:
                base_devices = tariff.get("device_limit")
                config_selected_devices = int(base_devices) if base_devices is not None else None

            config_selected_traffic_gb = selected_traffic_db
            if config_selected_traffic_gb is None:
                base_traffic_gb = tariff.get("traffic_limit")
                config_selected_traffic_gb = int(base_traffic_gb) if base_traffic_gb is not None else None

            await state.update_data(
                renew_mode="renew",
                renew_key_name=email,
                renew_client_id=client_id,
                renew_tariff_id=tariff_id,
                renew_new_expiry_time=new_expiry_time,
                config_selected_device_limit=config_selected_devices,
                config_selected_traffic_gb=config_selected_traffic_gb,
                renew_current_device_limit=record.get("current_device_limit"),
                renew_selected_device_limit=record.get("selected_device_limit"),
                renew_current_traffic_limit=record.get("current_traffic_limit"),
                renew_selected_traffic_limit=record.get("selected_traffic_limit"),
            )

            from handlers.tariffs.buy.config import start_tariff_config

            await start_tariff_config(
                callback_query=callback_query,
                state=state,
                session=session,
                tariff_id=tariff_id,
            )
            return

        if record.get("tariff_id") and int(record.get("tariff_id")) != int(tariff_id):
            shown = await _maybe_show_switch_confirm(
                callback_query,
                state,
                session,
                tg_id=tg_id,
                client_id=client_id,
                email=email,
                new_tariff_id=int(tariff_id),
                record=record,
                selected_device=None,
                selected_traffic=None,
            )
            if shown:
                return

        balance = round(await get_balance(session, tg_id), 2)

        cost = round(tariff["price_rub"], 2)

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
                        "selected_duration_days": duration_days,
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


@router.callback_query(F.data.startswith("cfg_renew_confirm|"))
async def handle_renew_config_confirm(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    """Подтверждает выбор параметров тарифа при продлении."""
    tg_id = callback_query.from_user.id

    try:
        data = await state.get_data()

        client_id = data.get("renew_client_id")
        email = data.get("renew_key_name")
        tariff_id = data.get("renew_tariff_id")
        selected_devices = data.get("config_selected_device_limit")
        selected_traffic_gb = data.get("config_selected_traffic_gb")

        if not client_id or not email or not tariff_id:
            await callback_query.message.answer("❌ Данные для продления не найдены.")
            return

        from services.tariffs import calculate_config_price

        tariff = await get_tariff_by_id(session, int(tariff_id))
        if not tariff or not tariff.get("configurable"):
            await callback_query.message.answer("❌ Тариф не найден или не поддерживает настройку.")
            return

        duration_days = int(tariff.get("duration_days") or 30)
        record = await get_key_details(session, email)
        if not record:
            await callback_query.message.answer("❌ Подписка не найдена.")
            return
        expiry_time = normalize_expiry_ms(record.get("expiry_time"))
        current_time = int(datetime.now(timezone.utc).timestamp() * 1000)
        from services.keys import compute_renewal_expiry

        new_expiry_time = await compute_renewal_expiry(
            session,
            now_ms=current_time,
            current_expiry_ms=expiry_time,
            current_tariff_id=record.get("tariff_id"),
            current_selected_device=record.get("selected_device_limit"),
            current_selected_traffic=record.get("selected_traffic_limit"),
            new_tariff_id=int(tariff_id),
            new_selected_device=int(selected_devices) if selected_devices is not None else None,
            new_selected_traffic=int(selected_traffic_gb) if selected_traffic_gb is not None else None,
            new_duration_days=duration_days,
        )

        final_price = calculate_config_price(
            tariff=tariff,
            selected_device_limit=int(selected_devices) if selected_devices is not None else None,
            selected_traffic_gb=int(selected_traffic_gb) if selected_traffic_gb is not None else None,
        )

        from services.addons import resolve_carried_addons

        carried = resolve_carried_addons(
            tariff,
            record,
            int(selected_devices) if selected_devices is not None else None,
            int(selected_traffic_gb) if selected_traffic_gb is not None else None,
        )
        final_price += carried.price_rub

        shown = await _maybe_show_switch_confirm(
            callback_query,
            state,
            session,
            tg_id=tg_id,
            client_id=client_id,
            email=email,
            new_tariff_id=int(tariff_id),
            record=record,
            selected_device=int(selected_devices) if selected_devices is not None else None,
            selected_traffic=int(selected_traffic_gb) if selected_traffic_gb is not None else None,
        )
        if shown:
            return

        balance = round(await get_balance(session, tg_id), 2)
        cost = round(final_price, 2)

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
                        "tariff_id": int(tariff_id),
                        "client_id": client_id,
                        "cost": cost,
                        "required_amount": required_amount,
                        "new_expiry_time": int(new_expiry_time),
                        "selected_duration_days": int(tariff["duration_days"]),
                        "total_gb": int(selected_traffic_gb or 0),
                        "email": email,
                        "selected_device_limit": selected_devices,
                        "selected_traffic_limit": selected_traffic_gb,
                        "selected_price_rub": int(final_price),
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

        logger.info(f"[RENEW_CONFIG] Продление ключа {client_id} с конфигурацией для пользователя {tg_id}")

        await complete_key_renewal(
            session=session,
            tg_id=tg_id,
            client_id=client_id,
            email=email,
            new_expiry_time=int(new_expiry_time),
            total_gb=int(selected_traffic_gb or 0),
            cost=cost,
            callback_query=callback_query,
            tariff_id=int(tariff_id),
            selected_device_limit=int(selected_devices) if selected_devices is not None else None,
            selected_traffic_limit=int(selected_traffic_gb) if selected_traffic_gb is not None else None,
            selected_price_rub=int(final_price),
        )

    except Exception as e:
        logger.error(f"[RENEW_CONFIG] Ошибка при продлении по конфигурации для пользователя {tg_id}: {e}")
        await callback_query.message.answer("❌ Произошла ошибка при продлении. Попробуйте позже.")


@router.callback_query(F.data == "renew_sw_confirm")
async def handle_renew_switch_confirm(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    """Подтверждение смены тарифа: остаток на баланс, новый тариф по полной цене."""
    tg_id = callback_query.from_user.id
    try:
        data = await state.get_data()

        client_id = data.get("renew_sw_client_id")
        email = data.get("renew_sw_email")
        tariff_id = data.get("renew_sw_tariff_id")
        if not client_id or not email or not tariff_id:
            await callback_query.message.answer("❌ Данные для смены тарифа не найдены.")
            return

        selected_device = data.get("renew_sw_selected_device")
        selected_traffic = data.get("renew_sw_selected_traffic")

        record = await get_key_details(session, email)
        if not record:
            await callback_query.message.answer(KEY_NOT_FOUND_MSG)
            return

        from services.keys import compute_renewal_quote

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        quote = await compute_renewal_quote(
            session,
            billing_user_id=tg_id,
            key_email=email,
            current_tariff_id=record.get("tariff_id"),
            current_selected_device=record.get("selected_device_limit"),
            current_selected_traffic=record.get("selected_traffic_limit"),
            current_expiry_ms=normalize_expiry_ms(record.get("expiry_time")),
            now_ms=now_ms,
            new_tariff_id=int(tariff_id),
            new_selected_device=selected_device,
            new_selected_traffic=selected_traffic,
        )

        await _finalize_renewal(
            callback_query,
            state,
            session,
            tg_id=tg_id,
            client_id=client_id,
            email=email,
            tariff_id=int(tariff_id),
            duration_days=quote.duration_days,
            total_gb=quote.total_gb,
            cost=float(quote.net_cost_rub),
            full_price=quote.new_full_price_rub,
            new_expiry_time=quote.new_expiry_ms,
            selected_device=selected_device,
            selected_traffic=selected_traffic,
        )
    except Exception as e:
        logger.error(f"[RENEW_SWITCH] Ошибка подтверждения смены для {tg_id}: {e}")
        await callback_query.message.answer("❌ Произошла ошибка. Попробуйте позже.")

from math import ceil

import sqlalchemy as sa

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import MODES_CONFIG
from core.settings.tariffs_config import TARIFFS_CONFIG, normalize_tariff_config
from database import (
    get_balance,
    get_key_details,
    get_tariff_by_id,
    save_key_config_with_mode,
)
from database.models import User
from handlers.keys.view.payload import render_key_info
from handlers.payments.fast_payment_flow import try_fast_payment_flow
from handlers.utils import edit_or_send_message
from hooks.processors import process_addon_purchase_complete
from logger import logger
from middlewares.session import release_session_early
from services.addons import (
    calc_pack_full_price_rub,
    get_pack_flags,
)
from services.payments.currency_rates import format_for_user
from services.tariffs.pricing import calculate_config_price
from services.tariffs.tariff_display import GB, get_effective_limits_for_key
from settings.buttons import PAYMENT
from settings.config import USE_NEW_PAYMENT_FLOW
from settings.texts import (
    ADDONS_NO_EXTRA_PAYMENT_TEXT,
    ADDONS_PACK_SUCCESS_TEXT,
    INSUFFICIENT_FUNDS_RENEWAL_MSG,
)

from ....keys.utils import resolve_key
from ..utils import (
    KeyAddonConfigState,
    calc_remaining_ratio_seconds,
)
from .screen import render_addons_screen


router = Router()


@router.callback_query(F.data.startswith("key_addons|"), flags={"popup": True})
async def start_key_addons(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    key_ref = callback.data.split("|", 1)[1]
    key_obj = await resolve_key(session, callback.from_user.id, key_ref)
    email = key_obj.email if key_obj else key_ref
    logger.debug(f"[ADDONS] PACK_MODE start_key_addons: tg_id={callback.from_user.id} email={email}")

    record = await get_key_details(session, email)
    if not record:
        logger.warning(f"[ADDONS] PACK_MODE: подписка {email} не найдена")
        await callback.message.answer("❌ Подписка не найдена.")
        return
    if record.get("tg_id") != callback.from_user.id:
        await callback.answer("Доступ запрещён.", show_alert=True)
        return

    tariff_id = record.get("tariff_id")
    if not tariff_id:
        logger.warning(f"[ADDONS] PACK_MODE: для подписки {email} не назначен тариф")
        await callback.message.answer("❌ Для этой подписки тариф не назначен, расширение недоступно.")
        return

    tariff = await get_tariff_by_id(session, int(tariff_id))
    if not tariff:
        logger.error(f"[ADDONS] PACK_MODE: тариф {tariff_id} не найден для email={email}")
        await callback.message.answer("❌ Тариф не найден.")
        return

    if not tariff.get("configurable"):
        logger.info(f"[ADDONS] PACK_MODE: тариф {tariff_id} не конфигурируемый, расширение недоступно")
        await callback.message.answer("❌ Для этого тарифа расширение через конфигуратор недоступно.")
        return

    cfg = normalize_tariff_config(tariff)

    raw_device_options = cfg.get("device_options") or tariff.get("device_options") or []
    raw_traffic_options = cfg.get("traffic_options_gb") or tariff.get("traffic_options_gb") or []

    device_int_options: list[int] = []
    for value in raw_device_options:
        try:
            device_int_options.append(int(value))
        except (TypeError, ValueError):
            continue

    traffic_int_options: list[int] = []
    for value in raw_traffic_options:
        try:
            traffic_int_options.append(int(value))
        except (TypeError, ValueError):
            continue

    base_device_limit = cfg.get("base_device_limit")
    if base_device_limit is None:
        base_device_limit = tariff.get("device_limit")
    if base_device_limit is not None:
        try:
            base_device_int = int(base_device_limit)
            if base_device_int not in device_int_options:
                raw_device_options.append(base_device_int)
                device_int_options.append(base_device_int)
        except (TypeError, ValueError):
            pass

    device_overrides_cfg = cfg.get("device_price_overrides") or tariff.get("device_overrides") or {}
    if "0" in device_overrides_cfg and 0 not in device_int_options:
        raw_device_options.append(0)
        device_int_options.append(0)

    base_traffic_gb = cfg.get("base_traffic_gb")
    if base_traffic_gb is None:
        raw_limit = tariff.get("traffic_limit")
        if raw_limit:
            raw_limit = int(raw_limit)
            if raw_limit >= GB:
                base_traffic_gb = int(raw_limit / GB)
            else:
                base_traffic_gb = raw_limit
    if base_traffic_gb is not None:
        try:
            base_traffic_int = int(base_traffic_gb)
            if base_traffic_int not in traffic_int_options:
                raw_traffic_options.append(base_traffic_int)
                traffic_int_options.append(base_traffic_int)
        except (TypeError, ValueError):
            pass

    traffic_overrides_cfg = cfg.get("traffic_price_overrides") or tariff.get("traffic_overrides") or {}
    if "0" in traffic_overrides_cfg and 0 not in traffic_int_options:
        raw_traffic_options.append(0)
        traffic_int_options.append(0)

    try:
        device_options = sorted(
            raw_device_options,
            key=lambda v: (int(v) == 0, int(v)),
        )
    except (TypeError, ValueError):
        device_options = raw_device_options

    try:
        traffic_options = sorted(
            raw_traffic_options,
            key=lambda v: (int(v) == 0, int(v)),
        )
    except (TypeError, ValueError):
        traffic_options = raw_traffic_options

    logger.info(
        "[ADDONS] PACK_MODE start_key_addons options: "
        f"email={email} tariff_id={tariff_id} "
        f"device_options={device_options} traffic_options={traffic_options}"
    )

    selected_device_limit_db = record.get("selected_device_limit")
    selected_traffic_limit_db = record.get("selected_traffic_limit")
    current_device_limit_db = record.get("current_device_limit")
    current_traffic_limit_db = record.get("current_traffic_limit")

    base_devices = tariff.get("device_limit")
    base_devices = int(base_devices) if base_devices is not None else None

    base_traffic_bytes = tariff.get("traffic_limit")
    base_traffic_gb_value = int(base_traffic_bytes / GB) if base_traffic_bytes else None

    current_devices = (
        int(current_device_limit_db)
        if current_device_limit_db is not None
        else (int(selected_device_limit_db) if selected_device_limit_db is not None else base_devices)
    )
    current_traffic_gb = (
        int(current_traffic_limit_db)
        if current_traffic_limit_db is not None
        else (int(selected_traffic_limit_db) if selected_traffic_limit_db is not None else base_traffic_gb_value)
    )

    pack_devices, pack_traffic, pack_mode = get_pack_flags()

    has_device_pack = (
        pack_devices and bool(device_options) and not (current_devices is not None and int(current_devices) == 0)
    )
    has_traffic_pack = (
        pack_traffic and bool(traffic_options) and not (current_traffic_gb is not None and int(current_traffic_gb) == 0)
    )

    if not (has_device_pack or has_traffic_pack):
        logger.warning(
            f"[ADDONS] PACK_MODE: пакеты недоступны, уже максимальные параметры "
            f"email={email} current_devices={current_devices} current_traffic_gb={current_traffic_gb} "
            f"pack_mode={pack_mode!r}"
        )
        await callback.message.answer("❌ Для этой подписки пакеты уже недоступны.")
        return

    cfg_for_state = dict(cfg)
    if device_options:
        cfg_for_state["device_options"] = device_options
    if traffic_options:
        cfg_for_state["traffic_options_gb"] = traffic_options

    await state.update_data(
        addon_key_email=email,
        addon_key_client_id=record.get("client_id"),
        addon_tariff_id=int(tariff_id),
        addon_tariff_config=cfg_for_state,
        addon_current_device_limit=current_devices,
        addon_current_traffic_gb=current_traffic_gb,
        addon_expiry_time=record.get("expiry_time"),
        addon_selected_device_limit=None,
        addon_selected_traffic_gb=None,
    )
    await state.set_state(KeyAddonConfigState.configuring)

    await render_addons_screen(callback, state, session)


@router.callback_query(F.data.startswith("key_addons_devices|"), KeyAddonConfigState.configuring, flags={"popup": True})
async def handle_addons_devices_choice(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    parts = callback.data.split("|", 2)
    if len(parts) < 3:
        await callback.answer("Некорректные данные", show_alert=True)
        return

    new_devices = int(parts[2])

    data = await state.get_data()
    selected_devices = data.get("addon_selected_device_limit")

    logger.debug(
        "[ADDONS] PACK_MODE handle_addons_devices_choice: "
        f"tg_id={callback.from_user.id} email={data.get('addon_key_email')} "
        f"new_devices={new_devices} selected_devices={selected_devices}"
    )

    repeated = selected_devices is not None and int(selected_devices) == new_devices
    await state.update_data(addon_selected_device_limit=None if repeated else new_devices)
    await render_addons_screen(callback, state, session)


@router.callback_query(F.data.startswith("key_addons_traffic|"), KeyAddonConfigState.configuring, flags={"popup": True})
async def handle_addons_traffic_choice(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    parts = callback.data.split("|", 2)
    if len(parts) < 3:
        await callback.answer("Некорректные данные", show_alert=True)
        return

    new_traffic_gb = int(parts[2])

    data = await state.get_data()
    selected_traffic_gb = data.get("addon_selected_traffic_gb")

    logger.debug(
        "[ADDONS] PACK_MODE handle_addons_traffic_choice: "
        f"tg_id={callback.from_user.id} email={data.get('addon_key_email')} "
        f"new_traffic_gb={new_traffic_gb} selected_traffic_gb={selected_traffic_gb}"
    )

    repeated = selected_traffic_gb is not None and int(selected_traffic_gb) == new_traffic_gb
    await state.update_data(addon_selected_traffic_gb=None if repeated else new_traffic_gb)
    await render_addons_screen(callback, state, session)


@router.callback_query(F.data == "key_addons_confirm", KeyAddonConfigState.configuring, flags={"popup": True})
async def handle_addons_confirm(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    from services.operations import renew_key_in_cluster

    tg_id = callback.from_user.id
    data = await state.get_data()

    if data.get("addon_confirm_in_progress"):
        await callback.answer("⏳ Уже обрабатываю...", show_alert=False)
        return

    await state.update_data(addon_confirm_in_progress=True)

    try:
        email = data.get("addon_key_email")
        tariff_id = data.get("addon_tariff_id")
        selected_devices = data.get("addon_selected_device_limit")
        selected_traffic_gb = data.get("addon_selected_traffic_gb")
        current_devices = data.get("addon_current_device_limit")
        current_traffic_gb = data.get("addon_current_traffic_gb")

        logger.info(
            "[ADDONS] PACK_MODE handle_addons_confirm: "
            f"tg_id={tg_id} email={email} tariff_id={tariff_id} "
            f"selected_devices={selected_devices} selected_traffic_gb={selected_traffic_gb} "
            f"current_devices={current_devices} current_traffic_gb={current_traffic_gb}"
        )

        if not email or not tariff_id:
            await callback.message.answer("❌ Данные для изменения подписки не найдены.")
            await state.clear()
            return

        if selected_devices is None and selected_traffic_gb is None:
            await callback.answer("Выберите пакет устройств или трафика", show_alert=True)
            return

        record = await get_key_details(session, email)
        if not record:
            logger.warning(f"[ADDONS] PACK_MODE: подписка {email} не найдена в handle_addons_confirm")
            await callback.message.answer("❌ Подписка не найдена.")
            await state.clear()
            return

        tariff = await get_tariff_by_id(session, int(tariff_id))
        if not tariff:
            logger.error(f"[ADDONS] PACK_MODE: тариф {tariff_id} не найден в handle_addons_confirm")
            await callback.message.answer("❌ Тариф не найден.")
            await state.clear()
            return

        cfg = data.get("addon_tariff_config") or {}
        device_options = cfg.get("device_options") or []
        traffic_options = cfg.get("traffic_options_gb") or []

        pack_devices, pack_traffic, pack_mode = get_pack_flags()

        has_device_option = pack_devices and bool(device_options)
        has_traffic_option = pack_traffic and bool(traffic_options)

        if has_device_option and current_devices is not None and int(current_devices) == 0:
            has_device_option = False
            selected_devices = None

        if has_traffic_option and current_traffic_gb is not None and int(current_traffic_gb) == 0:
            has_traffic_option = False
            selected_traffic_gb = None

        current_devices_for_price = int(current_devices) if current_devices is not None else None
        current_traffic_for_price = int(current_traffic_gb) if current_traffic_gb is not None else None

        base_price_for_current = calculate_config_price(
            tariff=tariff,
            selected_device_limit=current_devices_for_price,
            selected_traffic_gb=current_traffic_for_price,
        )
        try:
            base_price_for_current_int = int(base_price_for_current) if base_price_for_current is not None else 0
        except (TypeError, ValueError):
            base_price_for_current_int = 0

        recalc_enabled = bool(
            MODES_CONFIG.get(
                "KEY_ADDONS_RECALC_PRICE",
                TARIFFS_CONFIG.get("KEY_ADDONS_RECALC_PRICE", False),
            )
        )

        diff_full = calc_pack_full_price_rub(
            tariff=tariff,
            has_device_option=has_device_option,
            has_traffic_option=has_traffic_option,
            selected_devices=int(selected_devices) if selected_devices is not None else None,
            selected_traffic_gb=int(selected_traffic_gb) if selected_traffic_gb is not None else None,
        )

        if recalc_enabled:
            remaining_seconds, total_seconds = calc_remaining_ratio_seconds(record.get("expiry_time"), tariff)
            extra_price = int((diff_full * remaining_seconds + total_seconds - 1) // total_seconds)
            total_price_after_purchase = base_price_for_current_int + extra_price
        else:
            extra_price = int(diff_full)
            total_price_after_purchase = base_price_for_current_int + diff_full

        logger.debug(
            "[ADDONS] PACK_MODE confirm prices: "
            f"base_price_for_current={base_price_for_current_int} diff_full={diff_full} "
            f"extra_price={extra_price} recalc_enabled={recalc_enabled} total_price_after_purchase={total_price_after_purchase} "
            f"has_device_option={has_device_option} has_traffic_option={has_traffic_option} "
            f"pack_mode={pack_mode!r}"
        )

        if extra_price <= 0:
            logger.info(f"[ADDONS] PACK_MODE: extra_price <= 0, доплата не требуется, email={email}")
            await state.clear()
            await render_key_info(callback.message, session, email, "img/pic_view.jpg")
            await callback.answer(ADDONS_NO_EXTRA_PAYMENT_TEXT, show_alert=True)
            return

        balance = await get_balance(session, tg_id)
        logger.debug(f"[ADDONS] PACK_MODE balance check: tg_id={tg_id} balance={balance} extra_price={extra_price}")

        if balance < extra_price:
            required_amount = ceil(extra_price - balance)
            language_code = getattr(callback.from_user, "language_code", None)
            required_amount_text = await format_for_user(session, tg_id, float(required_amount), language_code)

            logger.info(
                "[ADDONS] PACK_MODE: недостаточно средств "
                f"balance={balance} extra_price={extra_price} required_amount={required_amount} "
                f"tg_id={tg_id} USE_NEW_PAYMENT_FLOW={USE_NEW_PAYMENT_FLOW}"
            )

            if USE_NEW_PAYMENT_FLOW:
                handled = await try_fast_payment_flow(
                    callback,
                    session,
                    state,
                    tg_id=tg_id,
                    temp_key="waiting_for_addons_payment",
                    temp_payload={
                        "email": email,
                        "tariff_id": int(tariff_id),
                        "selected_device_limit": selected_devices,
                        "selected_traffic_gb": selected_traffic_gb,
                        "current_device_limit": current_devices,
                        "current_traffic_gb": current_traffic_gb,
                        "required_amount": required_amount,
                    },
                    required_amount=required_amount,
                )
                logger.debug(f"[ADDONS] PACK_MODE try_fast_payment_flow handled={handled} tg_id={tg_id} email={email}")
                if handled:
                    return

            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text=PAYMENT, callback_data="pay"))
            await edit_or_send_message(
                target_message=callback.message,
                text=INSUFFICIENT_FUNDS_RENEWAL_MSG.format(required_amount=required_amount_text),
                reply_markup=builder.as_markup(),
            )
            return

        try:
            expiry_time = record["expiry_time"]
            client_id = record["client_id"]
            server_id = record["server_id"]

            device_limit_effective_current, traffic_limit_bytes_effective_current = await get_effective_limits_for_key(
                session=session,
                tariff_id=int(tariff_id),
                selected_device_limit=int(current_devices) if current_devices is not None else None,
                selected_traffic_gb=int(current_traffic_gb) if current_traffic_gb is not None else None,
            )
            traffic_limit_gb_effective_current = (
                int(traffic_limit_bytes_effective_current / GB) if traffic_limit_bytes_effective_current else 0
            )

            new_device_limit_effective = device_limit_effective_current
            new_traffic_limit_gb_effective = traffic_limit_gb_effective_current

            if has_device_option and selected_devices is not None:
                pack_devices_val = int(selected_devices)
                if pack_devices_val <= 0 or (
                    new_device_limit_effective is not None and new_device_limit_effective <= 0
                ):
                    new_device_limit_effective = 0
                else:
                    if new_device_limit_effective is None:
                        new_device_limit_effective = pack_devices_val
                    else:
                        new_device_limit_effective = new_device_limit_effective + pack_devices_val

            if has_traffic_option and selected_traffic_gb is not None:
                pack_traffic_val = int(selected_traffic_gb)
                if pack_traffic_val <= 0 or new_traffic_limit_gb_effective <= 0:
                    new_traffic_limit_gb_effective = 0
                else:
                    new_traffic_limit_gb_effective = new_traffic_limit_gb_effective + pack_traffic_val

            current_subgroup = None
            try:
                current_tariff_id = record.get("tariff_id")
                if current_tariff_id:
                    current_tariff = await get_tariff_by_id(session, int(current_tariff_id))
                    if current_tariff:
                        current_subgroup = current_tariff.get("subgroup_title")
            except Exception as error:
                logger.warning(f"[ADDONS] PACK_MODE: не удалось определить текущую подгруппу: {error}")

            target_subgroup = tariff.get("subgroup_title")
            old_subgroup = current_subgroup

            total_gb = new_traffic_limit_gb_effective
            hwid_device_limit_to_set = new_device_limit_effective

            logger.debug(
                "[ADDONS] PACK_MODE renew_key_in_cluster params: "
                f"server_id={server_id} email={email} client_id={client_id} total_gb={total_gb} "
                f"hwid_device_limit_to_set={hwid_device_limit_to_set} target_subgroup={target_subgroup} "
                f"old_subgroup={old_subgroup}"
            )

            await release_session_early(session)
            await renew_key_in_cluster(
                cluster_id=server_id,
                email=email,
                client_id=client_id,
                new_expiry_time=expiry_time,
                total_gb=total_gb,
                session=session,
                hwid_device_limit=hwid_device_limit_to_set,
                reset_traffic=False,
                target_subgroup=target_subgroup,
                old_subgroup=old_subgroup,
                plan=int(tariff_id),
            )

            result = await session.execute(
                sa.update(User)
                .where(User.tg_id == tg_id, User.balance >= extra_price)
                .values(balance=User.balance - extra_price)
            )
            if (result.rowcount or 0) != 1:
                await session.rollback()
                await callback.answer("❌ Недостаточно средств.", show_alert=True)
                await state.clear()
                return

            await save_key_config_with_mode(
                session=session,
                email=email,
                selected_devices=new_device_limit_effective,
                selected_traffic_gb=new_traffic_limit_gb_effective,
                total_price=int(total_price_after_purchase),
                has_device_choice=has_device_option,
                has_traffic_choice=has_traffic_option,
                config_mode="pack",
            )

            await session.commit()

            logger.info(
                "[ADDONS] PACK_MODE успешная покупка пакета: "
                f"tg_id={tg_id} email={email} extra_price={extra_price} "
                f"new_device_limit_effective={new_device_limit_effective} "
                f"new_traffic_limit_gb_effective={new_traffic_limit_gb_effective} "
                f"recalc_enabled={recalc_enabled} pack_mode={pack_mode!r}"
            )

            await state.clear()

            intercepted = await process_addon_purchase_complete(
                chat_id=callback.from_user.id,
                session=session,
                email=email,
                message=callback.message,
            )
            if not intercepted:
                await render_key_info(callback.message, session, email, "img/pic_view.jpg")

            await callback.answer(ADDONS_PACK_SUCCESS_TEXT, show_alert=True)

        except Exception as error:
            logger.error(f"[ADDONS] PACK_MODE ошибка при покупке пакета для {email}: {error}")
            await callback.message.answer("❌ Ошибка при обновлении подписки. Попробуйте позже.")
            await state.clear()

    finally:
        try:
            await state.update_data(addon_confirm_in_progress=False)
        except Exception:
            pass

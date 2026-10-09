from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from enum import Enum, auto
from typing import NamedTuple

from sqlalchemy import update

from database import (
    add_notification,
    check_notification_time,
    delete_notification,
    get_balance,
    update_balance,
    update_key_expiry,
    update_key_tariff,
)
from database.access.resolution import UserId
from database.keys import lock_owned_key_for_operation, resolve_current_key_owner, resolve_key_operation_owner
from database.models import Key
from database.tariffs import check_tariff_exists, get_tariff_by_id, get_tariffs_for_cluster
from handlers.notifications.context import NotificationContext
from hooks.hooks import run_hooks
from logger import logger
from middlewares.session import operation_session
from services.operations import renew_key_in_cluster
from services.tariffs.tariff_display import GB, get_effective_limits_for_key, resolve_price_to_charge


class RenewalStatus(Enum):
    SUCCESS = auto()
    FORBIDDEN_TARIFF = auto()
    NO_BALANCE = auto()
    NO_TARIFF = auto()
    COOLDOWN = auto()
    PANEL_FAILED = auto()


class RenewalResult(NamedTuple):
    status: RenewalStatus
    tariff: dict | None = None
    new_expiry_time: int | None = None


FORBIDDEN_GROUPS = ["trial", "discounts", "discounts_max", "cold_discounts", "cold_discounts_max", "gifts"]


async def try_auto_renew(ctx: NotificationContext, key) -> RenewalResult:
    async with operation_session(ctx.session) as session:
        if session is not ctx.session:
            return await try_auto_renew(replace(ctx, session=session), key)
    owner_ref = UserId(key.user_id)
    original_state = (key.expiry_time, key.tariff_id, key.server_id, getattr(key, "is_frozen", False))
    key = await lock_owned_key_for_operation(ctx.session, owner_ref, key.client_id, key.email or "")
    if key is None or key.is_frozen:
        return RenewalResult(RenewalStatus.COOLDOWN)
    if (key.expiry_time, key.tariff_id, key.server_id, key.is_frozen) != original_state:
        return RenewalResult(RenewalStatus.COOLDOWN)
    tg_id = key.tg_id
    email = key.email or ""
    renew_notification_id = f"{email}_renew"

    can_renew = await check_notification_time(ctx.session, owner_ref, renew_notification_id, hours=24)
    if not can_renew:
        return RenewalResult(RenewalStatus.COOLDOWN)

    server_id = key.server_id
    tariff_id = key.tariff_id

    tariffs = await get_tariffs_for_cluster(ctx.session, server_id)
    if not tariffs:
        return RenewalResult(RenewalStatus.NO_TARIFF)

    current_tariff = None
    if tariff_id:
        current_tariff = ctx.get_tariff(tariff_id)
        if not current_tariff and await check_tariff_exists(ctx.session, tariff_id):
            current_tariff = await get_tariff_by_id(ctx.session, tariff_id)

    if not current_tariff:
        return RenewalResult(RenewalStatus.NO_TARIFF)

    if current_tariff.get("is_active") is False:
        return RenewalResult(RenewalStatus.FORBIDDEN_TARIFF)

    forbidden = list(FORBIDDEN_GROUPS)
    try:
        hook_results = await run_hooks("renewal_forbidden_groups", chat_id=tg_id, admin=False, session=ctx.session)
        for hr in hook_results:
            forbidden.extend(hr.get("additional_groups", []))
    except Exception as error:
        logger.warning(f"[RENEW] Ошибка хуков forbidden_groups: {error}")

    if current_tariff["group_code"] in forbidden:
        return RenewalResult(RenewalStatus.FORBIDDEN_TARIFF)

    balance = await get_balance(ctx.session, owner_ref)

    renewal_cost = await resolve_price_to_charge(
        ctx.session,
        {
            "tariff_id": current_tariff.get("id"),
            "selected_device_limit": getattr(key, "selected_device_limit", None),
            "selected_traffic_limit": getattr(key, "selected_traffic_limit", None),
            "selected_price_rub": None,
        },
    )

    if renewal_cost is None or balance < renewal_cost:
        return RenewalResult(RenewalStatus.NO_BALANCE)

    client_id = key.client_id
    current_expiry = key.expiry_time
    original_config = {
        "current_device_limit": key.current_device_limit,
        "current_traffic_limit": key.current_traffic_limit,
        "selected_price_rub": key.selected_price_rub,
    }
    duration_days = current_tariff["duration_days"]

    selected_device_limit = getattr(key, "selected_device_limit", None)
    selected_traffic_limit = getattr(key, "selected_traffic_limit", None)
    selected_traffic_gb = int(selected_traffic_limit) if selected_traffic_limit is not None else None

    device_limit_effective, traffic_limit_bytes_effective = await get_effective_limits_for_key(
        session=ctx.session,
        tariff_id=int(current_tariff["id"]),
        selected_device_limit=int(selected_device_limit) if selected_device_limit is not None else None,
        selected_traffic_gb=selected_traffic_gb,
    )
    traffic_limit_gb = int(traffic_limit_bytes_effective / GB) if traffic_limit_bytes_effective else 0

    now_ms = datetime.now(timezone.utc).timestamp() * 1000
    base_expiry = current_expiry if current_expiry > now_ms else now_ms
    new_expiry_time = int(base_expiry + duration_days * 24 * 60 * 60 * 1000)

    logger.info(f"Продление {email} на {duration_days}д для {tg_id}. Баланс: {balance}, списываем: {renewal_cost}")

    key_subgroup = current_tariff.get("subgroup_title")

    debited = await update_balance(ctx.session, owner_ref, -renewal_cost)
    if debited is None:
        return RenewalResult(RenewalStatus.NO_BALANCE)

    await update_key_expiry(ctx.session, client_id, new_expiry_time, record_event=False, user_id=owner_ref)
    await update_key_tariff(ctx.session, client_id, current_tariff["id"], user_id=owner_ref)
    await ctx.session.execute(
        update(Key)
        .where(Key.user_id == owner_ref, Key.client_id == client_id)
        .values(
            current_device_limit=selected_device_limit,
            current_traffic_limit=selected_traffic_limit,
            selected_price_rub=renewal_cost,
        )
    )
    await add_notification(ctx.session, owner_ref, renew_notification_id)

    try:
        renewed = await renew_key_in_cluster(
            cluster_id=server_id,
            email=email,
            client_id=client_id,
            new_expiry_time=new_expiry_time,
            total_gb=traffic_limit_gb,
            hwid_device_limit=device_limit_effective,
            session=ctx.session,
            target_subgroup=key_subgroup,
            old_subgroup=key_subgroup,
            plan=current_tariff["id"],
        )
    except Exception as error:
        logger.error(f"[RENEW] Панель не продлила {email}: {error}")
        renewed = False

    if not renewed:
        await _revert_renewal(
            ctx,
            user_id=owner_ref,
            client_id=client_id,
            renewal_cost=renewal_cost,
            old_expiry=current_expiry,
            old_tariff_id=tariff_id,
            expected_expiry=new_expiry_time,
            original_config=original_config,
            notification_id=renew_notification_id,
            email=email,
        )
        return RenewalResult(RenewalStatus.PANEL_FAILED)

    current_owner = await resolve_key_operation_owner(ctx.session, owner_ref, client_id, email)
    if current_owner is None:
        raise ValueError("Владелец продлённого ключа изменился")
    await update_key_expiry(
        ctx.session, client_id, new_expiry_time, price_rub=float(renewal_cost), user_id=current_owner
    )

    return RenewalResult(RenewalStatus.SUCCESS, current_tariff, new_expiry_time)


async def _revert_renewal(
    ctx: NotificationContext,
    *,
    user_id: UserId,
    client_id: str,
    renewal_cost: float,
    old_expiry: int,
    old_tariff_id: int | None,
    expected_expiry: int,
    original_config: dict,
    notification_id: str,
    email: str,
) -> None:
    """Возвращает деньги и прежний срок, если панель продление не подтвердила."""
    try:
        refunded = await update_balance(ctx.session, user_id, renewal_cost)
        if refunded is None:
            current_owner = await resolve_current_key_owner(ctx.session, client_id, email)
            if current_owner is None:
                raise ValueError("Не удалось определить владельца возврата за продление")
            user_id = current_owner
            refunded = await update_balance(ctx.session, user_id, renewal_cost)
            if refunded is None:
                raise ValueError("Не удалось вернуть средства за продление")
        key = await lock_owned_key_for_operation(ctx.session, user_id, client_id, email)
        if key is None or key.expiry_time != expected_expiry:
            logger.warning("[RENEW] {}: деньги возвращены, изменённый ключ оставлен без отката", email)
            return
        await update_key_expiry(ctx.session, client_id, old_expiry, record_event=False, user_id=user_id)
        if old_tariff_id is not None:
            await update_key_tariff(ctx.session, client_id, old_tariff_id, user_id=user_id)
        await ctx.session.execute(
            update(Key).where(Key.user_id == user_id, Key.client_id == client_id).values(**original_config)
        )
        await delete_notification(ctx.session, user_id, notification_id)
        logger.warning(f"[RENEW] {email}: продление отменено, {renewal_cost} возвращены на баланс")
    except Exception as error:
        logger.error(f"[RENEW] {email}: не удалось откатить продление ({renewal_cost}): {error}")
        raise

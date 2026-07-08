from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, NamedTuple

from sqlalchemy import update

from database import (
    add_notification,
    check_notification_time,
    get_balance,
    update_balance,
    update_key_expiry,
    update_key_tariff,
)
from database.models import Key
from database.tariffs import check_tariff_exists, get_tariff_by_id, get_tariffs_for_cluster
from handlers.notifications.context import NotificationContext
from hooks.hooks import run_hooks
from logger import logger
from middlewares.session import release_session_early
from services.operations import renew_key_in_cluster
from services.tariffs.tariff_display import GB, get_effective_limits_for_key, resolve_price_to_charge


class RenewalStatus(Enum):
    SUCCESS = auto()
    FORBIDDEN_TARIFF = auto()
    NO_BALANCE = auto()
    NO_TARIFF = auto()
    COOLDOWN = auto()


class RenewalResult(NamedTuple):
    status: RenewalStatus
    tariff: dict | None = None
    new_expiry_time: int | None = None


FORBIDDEN_GROUPS = ["trial", "discounts", "discounts_max", "cold_discounts", "cold_discounts_max", "gifts"]


async def try_auto_renew(ctx: NotificationContext, key) -> RenewalResult:
    tg_id = key.tg_id
    email = key.email or ""
    renew_notification_id = f"{email}_renew"

    can_renew = await check_notification_time(ctx.session, tg_id, renew_notification_id, hours=24)
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

    if ctx.preload_data and tg_id in ctx.preload_data.get("balances_cache", {}):
        balance = ctx.preload_data["balances_cache"][tg_id]
    else:
        balance = await get_balance(ctx.session, tg_id)

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

    await release_session_early(ctx.session)
    await renew_key_in_cluster(
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

    await ctx.session.execute(
        update(Key)
        .where(Key.client_id == client_id)
        .values(
            current_device_limit=selected_device_limit,
            current_traffic_limit=selected_traffic_limit,
            selected_price_rub=renewal_cost,
        )
    )

    if ctx.bulk_updates is not None:
        bc = ctx.bulk_updates["balance_changes"]
        bc[tg_id] = bc.get(tg_id, 0) - renewal_cost
        ctx.bulk_updates["key_expiry_updates"].append((client_id, new_expiry_time))
        ctx.bulk_updates["key_tariff_updates"].append((client_id, current_tariff["id"]))
        ctx.bulk_updates["notifications_to_add"].append((tg_id, renew_notification_id))
    else:
        debited = await update_balance(ctx.session, tg_id, -renewal_cost)
        if debited is None:
            return RenewalResult(RenewalStatus.NO_BALANCE)
        await update_key_expiry(ctx.session, client_id, new_expiry_time)
        await update_key_tariff(ctx.session, client_id, current_tariff["id"])
        await add_notification(ctx.session, tg_id, renew_notification_id)

    return RenewalResult(RenewalStatus.SUCCESS, current_tariff, new_expiry_time)

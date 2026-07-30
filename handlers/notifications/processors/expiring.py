from __future__ import annotations

import asyncio

from datetime import datetime, timedelta

import pytz

from sqlalchemy.ext.asyncio import async_sessionmaker

from config import EXECUTOR_POOL_SIZE
from database import add_notification, check_notification_time_bulk, check_notifications_bulk
from database.web_notifications import notify_web
from handlers.notifications.context import NotificationContext
from handlers.notifications.keyboards import (
    build_change_tariff_kb,
    build_notification_expired_kb,
    build_notification_kb,
)
from handlers.notifications.renewal import RenewalResult, RenewalStatus, try_auto_renew
from handlers.notifications.sender import (
    NotificationRateLimiter,
    prepare_key_expiry_data,
    send_messages_with_limit,
    send_notification,
)
from handlers.texts import KEY_CANNOT_RENEW_CURRENT, KEY_EXPIRY, get_renewal_message
from handlers.utils import get_russian_month
from logger import logger
from middlewares.session import wrap_session
from services.tariffs.tariff_display import GB, get_effective_limits_for_key


moscow_tz = pytz.timezone("Europe/Moscow")


async def process_expiring_keys(
    ctx: NotificationContext,
    keys: list,
    min_hours: int,
    max_hours: int,
    notify_type: str,
    photo: str,
    notify_renew_enabled: bool,
    sessionmaker: async_sessionmaker | None = None,
):
    min_threshold = int((datetime.now(moscow_tz) + timedelta(hours=min_hours)).timestamp() * 1000)
    max_threshold = int((datetime.now(moscow_tz) + timedelta(hours=max_hours)).timestamp() * 1000)
    expiring_keys = [k for k in keys if k.expiry_time and min_threshold < k.expiry_time <= max_threshold]

    if not expiring_keys:
        return

    logger.info(f"[{notify_type}] Найдено {len(expiring_keys)} истекающих ключей")

    tg_ids = [k.tg_id for k in expiring_keys]
    emails = [k.email or "" for k in expiring_keys]
    allowed = await check_notifications_bulk(ctx.session, notify_type, max_hours, tg_ids=tg_ids, emails=emails)
    allowed_set = {(u["tg_id"], u["email"]) for u in allowed}

    notify_pairs = [
        (k.tg_id, f"{(k.email or '')}_{notify_type}") for k in expiring_keys if (k.tg_id, k.email or "") in allowed_set
    ]
    can_notify_set = await check_notification_time_bulk(ctx.session, notify_pairs, max_hours)

    renew_candidates = []
    simple_notify = []

    for key in expiring_keys:
        tg_id = key.tg_id
        email = key.email or ""
        notification_id = f"{email}_{notify_type}"

        if (tg_id, email) not in allowed_set:
            continue
        if (tg_id, notification_id) not in can_notify_set:
            continue

        if notify_renew_enabled:
            renew_candidates.append((key, notification_id))
        else:
            simple_notify.append((key, notification_id))

    if renew_candidates:
        await _process_renew_candidates(ctx, renew_candidates, photo, sessionmaker)

    if simple_notify:
        await _send_simple_warnings(ctx, simple_notify, photo, notify_type)


async def _process_renew_candidates(
    ctx: NotificationContext,
    candidates: list[tuple],
    photo: str,
    sessionmaker: async_sessionmaker | None,
):
    use_parallel = sessionmaker is not None and EXECUTOR_POOL_SIZE > 1
    rate_limiter = NotificationRateLimiter(max_rate=30, window=1.0)

    if use_parallel:
        semaphore = asyncio.Semaphore(EXECUTOR_POOL_SIZE)

        async def do_one(key, notification_id):
            async with semaphore:
                async with sessionmaker() as session:
                    session = wrap_session(session, sessionmaker)
                    one_ctx = NotificationContext(
                        bot=ctx.bot,
                        session=session,
                        current_time=ctx.current_time,
                        preload_data=ctx.preload_data,
                        bulk_updates=None,
                    )
                    try:
                        result = await try_auto_renew(one_ctx, key)
                        return (key, notification_id, result)
                    except Exception as e:
                        logger.error(f"Ошибка продления {key.tg_id} ({key.email}): {e}")
                        return (key, notification_id, RenewalResult(RenewalStatus.NO_TARIFF))

        tasks = [do_one(k, nid) for k, nid in candidates]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    else:
        results = []
        for key, notification_id in candidates:
            try:
                result = await try_auto_renew(ctx, key)
                results.append((key, notification_id, result))
            except Exception as e:
                logger.error(f"Ошибка продления {key.tg_id}: {e}")
                results.append((key, notification_id, RenewalResult(RenewalStatus.NO_TARIFF)))

    for item in results:
        if isinstance(item, Exception):
            logger.error(f"Ошибка задачи продления: {item}")
            continue

        key, notification_id, renewal_result = item
        tg_id = key.tg_id

        await rate_limiter.acquire()

        if renewal_result.status == RenewalStatus.SUCCESS:
            await _send_renewed(ctx, key, renewal_result.tariff, renewal_result.new_expiry_time)
        elif renewal_result.status in (RenewalStatus.FORBIDDEN_TARIFF, RenewalStatus.NO_TARIFF):
            await _send_change_tariff(ctx, key, photo)
        else:
            await _send_expiry_warning(ctx, key, photo)

        await add_notification(ctx.session, tg_id, notification_id)


async def _send_simple_warnings(ctx: NotificationContext, items: list[tuple], photo: str, notify_type: str):
    messages = []
    for key, notification_id in items:
        tg_id = key.tg_id
        email = key.email or ""

        expiry_data = await prepare_key_expiry_data(key, ctx.session, ctx.current_time)
        text = KEY_EXPIRY.format(
            email=email,
            hours_left_formatted=expiry_data["hours_left_formatted"],
            formatted_expiry_date=expiry_data["formatted_expiry_date"],
            tariff_name=expiry_data["tariff_name"],
            tariff_details=expiry_data["tariff_details"],
        )
        keyboard = build_notification_kb(email, getattr(key, "client_id", None))
        messages.append({
            "tg_id": tg_id,
            "text": text,
            "photo": photo,
            "keyboard": keyboard,
            "notification_id": notification_id,
        })

        try:
            await notify_web(
                ctx.session, tg_id=tg_id, type="key_expiry", template_vars={"email": email}, data={"email": email}
            )
        except Exception as e:
            logger.warning(f"[Notifications] web-уведомление key_expiry tg_id={tg_id}: {e}")

    if messages:
        results = await send_messages_with_limit(ctx.bot, messages)
        for msg, result in zip(messages, results, strict=False):
            await add_notification(ctx.session, msg["tg_id"], msg["notification_id"])
            if result:
                logger.info(f"Уведомление {notify_type} отправлено {msg['tg_id']}")


async def _send_expiry_warning(ctx: NotificationContext, key, photo: str) -> bool:
    expiry_data = await prepare_key_expiry_data(key, ctx.session, ctx.current_time)
    text = KEY_EXPIRY.format(
        email=key.email or "",
        hours_left_formatted=expiry_data["hours_left_formatted"],
        formatted_expiry_date=expiry_data["formatted_expiry_date"],
        tariff_name=expiry_data["tariff_name"],
        tariff_details=expiry_data["tariff_details"],
    )
    keyboard = build_notification_kb(key.email or "", getattr(key, "client_id", None))
    return await send_notification(ctx.bot, key.tg_id, photo, text, keyboard)


async def _send_change_tariff(ctx: NotificationContext, key, photo: str) -> bool:
    expiry_data = await prepare_key_expiry_data(key, ctx.session, ctx.current_time)
    text = KEY_CANNOT_RENEW_CURRENT.format(
        email=key.email or "",
        hours_left_formatted=expiry_data["hours_left_formatted"],
        formatted_expiry_date=expiry_data["formatted_expiry_date"],
        tariff_name=expiry_data["tariff_name"],
        tariff_details=expiry_data["tariff_details"],
    )
    keyboard = build_change_tariff_kb(key.email or "", getattr(key, "client_id", None))
    return await send_notification(ctx.bot, key.tg_id, photo, text, keyboard)


async def _send_renewed(ctx: NotificationContext, key, tariff: dict, new_expiry_time: int) -> bool:
    selected_device_limit = getattr(key, "selected_device_limit", None)
    selected_traffic_limit = getattr(key, "selected_traffic_limit", None)
    selected_traffic_gb = int(selected_traffic_limit) if selected_traffic_limit is not None else None

    device_limit_effective, traffic_limit_bytes_effective = await get_effective_limits_for_key(
        session=ctx.session,
        tariff_id=int(tariff["id"]),
        selected_device_limit=int(selected_device_limit) if selected_device_limit is not None else None,
        selected_traffic_gb=selected_traffic_gb,
    )
    traffic_limit_gb = int(traffic_limit_bytes_effective / GB) if traffic_limit_bytes_effective else 0

    formatted_expiry_date = datetime.fromtimestamp(new_expiry_time / 1000, tz=moscow_tz).strftime("%d %B %Y, %H:%M")
    formatted_expiry_date = formatted_expiry_date.replace(
        datetime.fromtimestamp(new_expiry_time / 1000, tz=moscow_tz).strftime("%B"),
        get_russian_month(datetime.fromtimestamp(new_expiry_time / 1000, tz=moscow_tz)),
    )

    text = get_renewal_message(
        tariff_name=tariff["name"],
        traffic_limit=traffic_limit_gb,
        device_limit=device_limit_effective,
        expiry_date=formatted_expiry_date,
        subgroup_title=tariff.get("subgroup_title", ""),
    )

    keyboard = build_notification_expired_kb()
    return await send_notification(ctx.bot, key.tg_id, "pic_renewed.jpg", text, keyboard)

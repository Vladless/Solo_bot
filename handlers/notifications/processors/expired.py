from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from sqlalchemy import exists, or_, select

from database import (
    add_notification,
    check_notifications_bulk,
    delete_key,
    delete_notification,
    get_last_notification_times_bulk,
)
from database.access.resolution import UserId
from database.keys import lock_owned_key_for_operation, resolve_key_operation_owner
from database.models import Key
from database.models.users import BlockedUser, ManualBan
from database.web_notifications import notify_web
from handlers.notifications.context import NotificationContext
from handlers.notifications.keyboards import build_notification_expired_kb, build_notification_kb
from handlers.notifications.renewal import RenewalStatus, try_auto_renew
from handlers.notifications.sender import chat_ids_for_user_ids, send_messages_with_limit
from logger import logger
from middlewares.session import operation_session
from services.formatting import format_hours, format_minutes
from services.operations import delete_key_from_cluster
from settings.texts import KEY_DELETED_MSG, KEY_EXPIRED_DELAY_MSG, KEY_EXPIRED_NO_DELAY_MSG

from .expiring import _send_renewed


_EXPIRED_PHOTO = "notify_expired.jpg"


def _format_remaining_time(total_minutes: int) -> str:
    hours = total_minutes // 60
    minutes = total_minutes % 60
    if hours > 0 and minutes > 0:
        return f"{format_hours(hours)} и {format_minutes(minutes)}"
    if hours > 0:
        return format_hours(hours)
    return format_minutes(minutes)


async def _notify_web_expired(ctx: NotificationContext, key) -> None:
    """Дублирует уведомление об истёкшей подписке в веб-кабинет."""
    email = key.email or ""
    await notify_web(
        ctx.session,
        user_ref=UserId(key.user_id),
        type="key_expired",
        template_vars={"email": email},
        data={"email": email, "client_id": getattr(key, "client_id", None)},
    )


def _build_grace_message(key, remaining_minutes: int) -> dict:
    email = key.email or ""
    text = KEY_EXPIRED_DELAY_MSG.format(
        email=email,
        time_formatted=_format_remaining_time(remaining_minutes),
    )
    return {
        "tg_id": key.tg_id,
        "user_id": UserId(key.user_id),
        "text": text,
        "photo": _EXPIRED_PHOTO,
        "keyboard": build_notification_kb(email, getattr(key, "client_id", None)),
    }


def _build_expired_message(key, delete_delay_minutes: int) -> dict:
    email = key.email or ""
    if delete_delay_minutes > 0:
        text = KEY_EXPIRED_DELAY_MSG.format(
            email=email,
            time_formatted=_format_remaining_time(delete_delay_minutes),
        )
    else:
        text = KEY_EXPIRED_NO_DELAY_MSG.format(email=email)
    return {
        "tg_id": key.tg_id,
        "user_id": UserId(key.user_id),
        "text": text,
        "photo": _EXPIRED_PHOTO,
        "keyboard": build_notification_kb(email, getattr(key, "client_id", None)),
    }


def _build_deleted_message(key) -> dict:
    email = key.email or ""
    return {
        "tg_id": key.tg_id,
        "user_id": UserId(key.user_id),
        "text": KEY_DELETED_MSG.format(email=email),
        "photo": _EXPIRED_PHOTO,
        "keyboard": build_notification_expired_kb(),
    }


async def process_expired_keys(
    ctx: NotificationContext,
    keys: list,
    notify_renew_expired: bool,
    notify_delete_key: bool,
    delete_delay_minutes: int,
):
    expired_keys = [k for k in keys if k.expiry_time and k.expiry_time < ctx.current_time]

    try:
        blocked_expired = await _get_blocked_expired_keys(ctx.session, ctx.current_time)
        if blocked_expired:
            existing_ids = {k.client_id for k in expired_keys}
            for bk in blocked_expired:
                if bk.client_id not in existing_ids:
                    expired_keys.append(bk)
            logger.info(f"[Expired] +{len(blocked_expired)} ключей заблокированных")
    except Exception as e:
        logger.error(f"Ошибка получения ключей заблокированных: {e}")

    if not expired_keys:
        return

    logger.info(f"[Expired] Найдено {len(expired_keys)} истекших ключей")

    user_ids = [UserId(k.user_id) for k in expired_keys]
    emails = [k.email or "" for k in expired_keys]
    users = await check_notifications_bulk(ctx.session, "key_expired", 0, user_ids=user_ids, emails=emails)
    users_set = {(u["user_id"], u["email"]) for u in users}

    notification_pairs = [(UserId(k.user_id), f"{k.email or ''}_key_expired") for k in expired_keys]
    last_times = await get_last_notification_times_bulk(ctx.session, notification_pairs)

    messages: list[dict] = []
    renewed_notifications: list[tuple] = []
    pending_notifications: list[tuple[int, str]] = []

    for key in expired_keys:
        async with operation_session(ctx.session) as session:
            one_ctx = replace(ctx, session=session)
            key = await lock_owned_key_for_operation(one_ctx.session, key.user_id, key.client_id, key.email or "")
            if key is None or key.is_frozen or not key.expiry_time or key.expiry_time >= one_ctx.current_time:
                continue
            tg_id = key.tg_id
            owner_ref = UserId(key.user_id)
            email = key.email or ""
            client_id = key.client_id
            server_id = key.server_id
            notification_id = f"{email}_key_expired"
            last_notification_time = last_times.get((owner_ref, notification_id))

            expired_ms = one_ctx.current_time - key.expiry_time
            delay_ms = delete_delay_minutes * 60 * 1000
            is_grace = notify_delete_key and delete_delay_minutes > 0 and expired_ms < delay_ms
            is_delete = not is_grace

            if notify_renew_expired:
                try:
                    result = await try_auto_renew(one_ctx, key)
                except Exception as e:
                    await one_ctx.session.rollback()
                    logger.error(f"Ошибка продления для {tg_id}: {e}")
                    continue

                if result.status == RenewalStatus.SUCCESS:
                    if one_ctx.bulk_updates:
                        one_ctx.bulk_updates["notifications_to_delete"].append((owner_ref, notification_id))
                    else:
                        await delete_notification(one_ctx.session, owner_ref, notification_id, commit=False)
                    renewed_notifications.append((key, result.tariff, result.new_expiry_time))
                    continue

                key = await lock_owned_key_for_operation(one_ctx.session, owner_ref, client_id, email)
                if key is None or key.is_frozen or not key.expiry_time or key.expiry_time >= one_ctx.current_time:
                    continue
                server_id = key.server_id
                expired_ms = one_ctx.current_time - key.expiry_time
                is_grace = notify_delete_key and delete_delay_minutes > 0 and expired_ms < delay_ms
                is_delete = not is_grace

            if is_grace:
                if last_notification_time is None and (owner_ref, email) in users_set:
                    remaining_ms = delay_ms - expired_ms
                    remaining_minutes = max(1, int(remaining_ms / (60 * 1000)))
                    messages.append(_build_grace_message(key, remaining_minutes))
                    pending_notifications.append((owner_ref, notification_id))
                    await _notify_web_expired(one_ctx, key)
                continue

            if is_delete and notify_delete_key:
                should_delete = False
                if delete_delay_minutes == 0:
                    should_delete = True
                elif last_notification_time is not None:
                    minutes_passed = expired_ms / (60 * 1000)
                    should_delete = minutes_passed >= delete_delay_minutes

                if should_delete:
                    try:
                        await delete_key_from_cluster(server_id, email, client_id, one_ctx.session)
                        current_owner = await resolve_key_operation_owner(one_ctx.session, owner_ref, client_id, email)
                        if current_owner is None:
                            raise ValueError("Владелец удаляемого ключа изменился")
                        await delete_key(one_ctx.session, client_id, user_id=current_owner)
                        logger.info(f"Ключ {client_id} для {tg_id} удалён")
                        messages.append(_build_deleted_message(key))
                    except Exception as e:
                        await one_ctx.session.rollback()
                        logger.error(f"Ошибка удаления ключа {client_id}: {e}")
                    continue

            if last_notification_time is None and (owner_ref, email) in users_set:
                messages.append(_build_expired_message(key, delete_delay_minutes))
                pending_notifications.append((owner_ref, notification_id))
                await _notify_web_expired(one_ctx, key)

    for key, tariff, new_expiry_time in renewed_notifications:
        try:
            async with operation_session(ctx.session) as session:
                await _send_renewed(replace(ctx, session=session), key, tariff, new_expiry_time)
        except Exception as exc:
            logger.warning(f"Не удалось отправить уведомление о продлении {key.client_id}: {exc}")

    if messages:
        chat_ids = await chat_ids_for_user_ids(ctx.session, [msg["user_id"] for msg in messages])
        messages = [{**msg, "tg_id": chat_ids.get(msg["user_id"])} for msg in messages]
        await send_messages_with_limit(ctx.bot, messages)

    for tg_id, notification_id in pending_notifications:
        await add_notification(ctx.session, tg_id, notification_id)

    logger.info("[Expired] Обработка завершена")


async def _get_blocked_expired_keys(session, current_time: int) -> list:
    stmt = select(Key).where(
        Key.is_frozen.is_(False),
        Key.expiry_time.isnot(None),
        Key.expiry_time < current_time,
        or_(
            exists().where(BlockedUser.user_id == Key.user_id),
            exists().where(
                ManualBan.user_id == Key.user_id,
                or_(ManualBan.until.is_(None), ManualBan.until > datetime.now(timezone.utc)),
            ),
        ),
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())

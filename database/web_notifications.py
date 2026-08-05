from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.redis_cache import cache_delete, cache_delete_pattern, cache_get, cache_key, cache_set
from database.access.resolution import resolve_user_optional
from database.models import User, WebNotification, WebPushSubscription
from logger import logger


_NOTIF_LIST_TTL_SEC = 30
_NOTIF_UNREAD_TTL_SEC = 60


async def _invalidate_notif_cache(identity_id: str | None) -> None:
    if not identity_id:
        return
    await cache_delete(cache_key("notif_unread", identity_id))
    await cache_delete_pattern(cache_key("notif_list", identity_id, "*"))


async def upsert_push_subscription(
    session: AsyncSession,
    *,
    user_id: int,
    identity_id: str | None,
    endpoint: str,
    keys_json: dict,
) -> WebPushSubscription:
    """Upsert push subscription by endpoint (unique)."""
    stmt = (
        pg_insert(WebPushSubscription)
        .values(
            user_id=user_id,
            identity_id=identity_id,
            endpoint=endpoint,
            keys_json=keys_json,
            created_at=datetime.now(UTC),
        )
        .on_conflict_do_update(
            index_elements=["endpoint"],
            set_={
                "user_id": user_id,
                "identity_id": identity_id,
                "keys_json": keys_json,
                "created_at": datetime.now(UTC),
            },
        )
        .returning(WebPushSubscription)
    )
    result = await session.execute(stmt)
    return result.scalar_one()


async def get_push_subscriptions_by_user(
    session: AsyncSession,
    user_id: int,
) -> list[WebPushSubscription]:
    result = await session.execute(select(WebPushSubscription).where(WebPushSubscription.user_id == user_id))
    return list(result.scalars().all())


async def get_push_subscriptions_by_identity(
    session: AsyncSession,
    identity_id: str,
) -> list[WebPushSubscription]:
    result = await session.execute(select(WebPushSubscription).where(WebPushSubscription.identity_id == identity_id))
    return list(result.scalars().all())


async def delete_push_subscription_by_endpoint(
    session: AsyncSession,
    endpoint: str,
) -> None:
    await session.execute(delete(WebPushSubscription).where(WebPushSubscription.endpoint == endpoint))


async def get_notifications_for_identity(
    session: AsyncSession,
    identity_id: str,
    limit: int = 20,
    offset: int = 0,
) -> list[WebNotification]:
    ckey = cache_key("notif_list", identity_id, limit, offset)
    cached = await cache_get(ckey)
    if isinstance(cached, list):
        return [
            SimpleNamespace(
                **{
                    **d,
                    "created_at": datetime.fromisoformat(d["created_at"]) if d.get("created_at") else None,
                }
            )
            for d in cached
        ]
    result = await session.execute(
        select(WebNotification)
        .where(WebNotification.identity_id == identity_id)
        .order_by(WebNotification.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = list(result.scalars().all())
    serialized = [
        {
            "id": n.id,
            "type": n.type,
            "title": n.title,
            "message": n.message,
            "read": bool(n.read),
            "created_at": n.created_at.isoformat() if n.created_at else None,
            "data": n.data,
        }
        for n in rows
    ]
    await cache_set(ckey, serialized, _NOTIF_LIST_TTL_SEC)
    return rows


async def count_unread_for_identity(
    session: AsyncSession,
    identity_id: str,
) -> int:
    ckey = cache_key("notif_unread", identity_id)
    cached = await cache_get(ckey)
    if cached is not None:
        try:
            return int(cached)
        except (TypeError, ValueError):
            pass
    result = await session.execute(
        select(func.count())
        .select_from(WebNotification)
        .where(
            WebNotification.identity_id == identity_id,
            WebNotification.read == False,  # noqa: E712 SQLAlchemy expression
        )
    )
    count = result.scalar() or 0
    await cache_set(ckey, count, _NOTIF_UNREAD_TTL_SEC)
    return count


async def mark_all_read_for_identity(
    session: AsyncSession,
    identity_id: str,
) -> int:
    result = await session.execute(
        update(WebNotification)
        .where(
            WebNotification.identity_id == identity_id,
            WebNotification.read == False,  # noqa: E712 SQLAlchemy expression
        )
        .values(read=True)
    )
    await _invalidate_notif_cache(identity_id)
    return result.rowcount


async def mark_one_read_for_identity(
    session: AsyncSession,
    identity_id: str,
    notification_id: str,
) -> bool:
    """Mark single notification as read. Returns True if updated."""
    result = await session.execute(
        update(WebNotification)
        .where(
            WebNotification.identity_id == identity_id,
            WebNotification.id == notification_id,
        )
        .values(read=True)
    )
    await _invalidate_notif_cache(identity_id)
    return (result.rowcount or 0) > 0


async def delete_one_for_identity(
    session: AsyncSession,
    identity_id: str,
    notification_id: str,
) -> bool:
    """Delete single notification owned by identity. Returns True if deleted."""
    from sqlalchemy import delete as sql_delete

    result = await session.execute(
        sql_delete(WebNotification).where(
            WebNotification.identity_id == identity_id,
            WebNotification.id == notification_id,
        )
    )
    await _invalidate_notif_cache(identity_id)
    return (result.rowcount or 0) > 0


async def delete_all_for_identity(
    session: AsyncSession,
    identity_id: str,
) -> int:
    from sqlalchemy import delete as sql_delete

    result = await session.execute(sql_delete(WebNotification).where(WebNotification.identity_id == identity_id))
    await _invalidate_notif_cache(identity_id)
    return result.rowcount or 0


async def resolve_identity_id_by_tg_id(
    session: AsyncSession,
    tg_id: int,
) -> str | None:
    """Resolve identity_id from user's tg_id."""
    result = await session.execute(select(User.identity_id).where(User.tg_id == tg_id))
    return result.scalar_one_or_none()


async def create_notification(
    session: AsyncSession,
    *,
    user_id: int,
    identity_id: str | None,
    type: str = "system",
    title: str,
    message: str = "",
    data: dict | None = None,
) -> WebNotification:
    notif = WebNotification(
        user_id=user_id,
        identity_id=identity_id,
        type=type,
        title=title,
        message=message,
        data=data,
    )
    session.add(notif)
    await session.flush()
    await _invalidate_notif_cache(identity_id)
    return notif


class _SafeFormatDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _render_template(template: str, **kwargs: object) -> str:
    """Safe format — unknown placeholders stay as-is."""
    try:
        return template.format_map(_SafeFormatDict({k: str(v) for k, v in kwargs.items() if v is not None}))
    except Exception:
        return template


def _get_web_config_str(key: str, default: str) -> str:
    try:
        from core.settings.web_config import WEB_CONFIG

        val = WEB_CONFIG.get(key)
        return str(val).strip() if val else default
    except Exception:
        return default


async def notify_web(
    session: AsyncSession,
    *,
    tg_id: int,
    type: str = "system",
    title: str | None = None,
    message: str | None = None,
    data: dict | None = None,
    template_vars: dict | None = None,
) -> WebNotification | None:
    """Создаёт web-уведомление по legacy-ref (tg_id или User.id).

    title/message — если None, берутся из WEB_CONFIG шаблонов по type.
    template_vars — подстановки в шаблон ({email}, {amount}, {name}, {duration}).
    """
    try:
        user = await resolve_user_optional(session, tg_id)
        if user is None or user.identity_id is None:
            return None
        identity_id = user.identity_id
        notif_user_id = int(user.id)

        vars_ = template_vars or {}

        type_key_map = {
            "payment": ("WEB_NOTIFY_PAYMENT_TITLE", "WEB_NOTIFY_PAYMENT_MESSAGE"),
            "key_created": ("WEB_NOTIFY_KEY_CREATED_TITLE", "WEB_NOTIFY_KEY_CREATED_MESSAGE"),
            "key_expiry": ("WEB_NOTIFY_KEY_EXPIRY_TITLE", "WEB_NOTIFY_KEY_EXPIRY_MESSAGE"),
            "gift_received": ("WEB_NOTIFY_GIFT_TITLE", "WEB_NOTIFY_GIFT_MESSAGE"),
        }
        title_key, msg_key = type_key_map.get(type, (None, None))

        resolved_title = title
        if resolved_title is None and title_key:
            resolved_title = _render_template(_get_web_config_str(title_key, ""), **vars_)
        resolved_title = resolved_title or type

        resolved_message = message
        if resolved_message is None and msg_key:
            resolved_message = _render_template(_get_web_config_str(msg_key, ""), **vars_)
        resolved_message = resolved_message or ""

        notif = await create_notification(
            session,
            user_id=notif_user_id,
            identity_id=identity_id,
            type=type,
            title=resolved_title,
            message=resolved_message,
            data=data,
        )

        try:
            from services.web_push import push_enabled, send_push_to_many

            if push_enabled():
                subs = await get_push_subscriptions_by_identity(session, identity_id)
                if subs:
                    sub_infos = [{"endpoint": s.endpoint, "keys": s.keys_json} for s in subs]
                    sent, dead = await send_push_to_many(
                        sub_infos,
                        title=resolved_title,
                        body=resolved_message,
                        url="/dashboard/notifications",
                    )
                    for endpoint in dead:
                        await delete_push_subscription_by_endpoint(session, endpoint)
                    logger.debug(
                        "[notify_web] push sent to {}/{} subscriptions, removed {} dead",
                        sent,
                        len(sub_infos),
                        len(dead),
                    )
        except Exception as push_err:
            logger.warning("[notify_web] push delivery failed: {}", push_err)

        return notif
    except Exception:
        return None

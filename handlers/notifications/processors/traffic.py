import time

from html import escape, unescape

from core.bootstrap import NOTIFICATIONS_CONFIG
from core.settings.traffic_notifications import traffic_notification_levels
from database.access.resolution import UserId, notify_telegram_chat_id
from database.traffic_notifications import (
    claim_traffic_notification,
    complete_traffic_notification,
    key_traffic_snapshot,
    lock_traffic_notification_claim,
)
from database.web_notifications import notify_web
from handlers.admin.panel.headers import strip_tags
from handlers.notifications.context import NotificationContext
from handlers.notifications.keyboards import build_notification_kb
from handlers.notifications.sender import NotificationRateLimiter, send_notification
from handlers.utils import render_text
from logger import logger
from middlewares.session import operation_session
from services.traffic_notifications import collect_traffic_notification_meters
from settings.texts import (
    TRAFFIC_EXHAUSTED_TEXT,
    TRAFFIC_EXHAUSTED_WEB_TITLE,
    TRAFFIC_WARNING_TEXT,
    TRAFFIC_WARNING_WEB_TITLE,
)


def _traffic_text(claim) -> str:
    """Подставляет безопасные значения в шаблон уведомления."""
    meter = claim.meter
    return render_text(
        TRAFFIC_EXHAUSTED_TEXT if claim.level == 0 else TRAFFIC_WARNING_TEXT,
        email=escape(claim.email),
        server_name=escape(meter.server_name),
        remaining_gb=f"{meter.remaining_bytes / 1024**3:.2f}",
        limit_gb=f"{meter.limit_bytes / 1024**3:.2f}",
        remaining_percent=f"{meter.remaining_bytes * 100 / meter.limit_bytes:.1f}",
    )


async def process_traffic_notifications(ctx: NotificationContext, keys: list) -> None:
    """Отправляет одно актуальное предупреждение на ключ и цикл."""
    levels = traffic_notification_levels(NOTIFICATIONS_CONFIG)
    if not levels:
        return
    candidates = [
        key for key in keys if not key.is_frozen and (not key.expiry_time or key.expiry_time > ctx.current_time)
    ]
    if not candidates:
        return
    snapshots = {(int(key.user_id), key.client_id): key_traffic_snapshot(key) for key in candidates}
    meters_by_key = await collect_traffic_notification_meters(ctx.session, candidates, ctx.preload_data)
    limiter = NotificationRateLimiter(max_rate=25, window=1.0)
    for key in candidates:
        owner_ref = UserId(key.user_id)
        owner_pair = (int(owner_ref), key.client_id)
        meters = meters_by_key.get(owner_pair)
        if not meters:
            continue
        try:
            async with operation_session(ctx.session) as session:
                claim = await claim_traffic_notification(
                    session,
                    user_id=owner_ref,
                    client_id=key.client_id,
                    email=key.email or "",
                    expected_key=snapshots[owner_pair],
                    meters=meters,
                    levels=levels,
                    now_ms=int(time.time() * 1000),
                )
                await session.commit()
            if claim is None:
                continue
            await limiter.acquire()
            async with operation_session(ctx.session) as session:
                if not await lock_traffic_notification_claim(session, claim):
                    continue
                text = _traffic_text(claim)
                web = await notify_web(
                    session,
                    user_ref=owner_ref,
                    type="traffic_exhausted" if claim.level == 0 else "traffic_warning",
                    title=TRAFFIC_EXHAUSTED_WEB_TITLE if claim.level == 0 else TRAFFIC_WARNING_WEB_TITLE,
                    message=unescape(strip_tags(text)),
                    data={
                        "client_id": claim.client_id,
                        "email": claim.email,
                        "remaining_bytes": claim.meter.remaining_bytes,
                        "limit_bytes": claim.meter.limit_bytes,
                        "level": claim.level,
                    },
                )
                chat_id = await notify_telegram_chat_id(session, owner_ref)
                delivered = False
                if chat_id is not None:
                    delivered = await send_notification(
                        ctx.bot,
                        chat_id,
                        None,
                        text,
                        build_notification_kb(claim.email, claim.client_id),
                        user_id=owner_ref,
                    )
                await complete_traffic_notification(
                    session, claim, delivered=bool(web is not None or delivered), now_ms=int(time.time() * 1000)
                )
                await session.commit()
        except Exception as error:
            logger.warning("[TrafficNotify] Ошибка ключа {}: {}", key.client_id, type(error).__name__)

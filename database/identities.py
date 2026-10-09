import hashlib
import secrets

from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.client_origin import client_campaign, client_origin
from core.executor import run_cpu, run_io
from database import identity_sessions as _idsess
from database.access.identity_cache import invalidate_identity_cache
from database.access.resolution import TelegramId, UserId, get_user_by_tg_id
from database.access.tg_mirror import freeze_legacy_tg_owner, refresh_tg_mirrors_for_user, release_tg_mirrors
from database.models import (
    Admin,
    AuditEvent,
    BlockedUser,
    CouponHold,
    CouponUsage,
    DailyBonusClaim,
    Gift,
    GiftUsage,
    Identity,
    IdentityNotifPref,
    IdentitySession,
    Key,
    ManualBan,
    Notification,
    Payment,
    Referral,
    ScheduledBroadcast,
    SubscriptionEvent,
    TemporaryData,
    Ticket,
    User,
    WebErrorReport,
    WebNotification,
    WebPushSubscription,
)
from database.partners import transfer_partner_user_data
from database.users import (
    add_user,
    check_user_exists,
    invalidate_balance_cache,
    invalidate_profile_cache,
    update_balance,
)
from database.yookassa_autopay import transfer_recurring_user_data
from logger import logger
from settings.config import API_TOKEN_TTL_DAYS
from utils.cpu_tasks import check_password, hash_password


def _request_meta(request) -> tuple[str | None, str | None]:
    if request is None:
        return None, None
    try:
        ua = request.headers.get("user-agent")
    except Exception:
        ua = None
    ip = None
    try:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            ip = xff.split(",")[0].strip()
        elif request.client and request.client.host:
            ip = request.client.host
    except Exception:
        ip = None
    return ua, ip


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_token() -> str:
    return secrets.token_urlsafe(32)


async def create_identity(
    session: AsyncSession,
    email: str | None = None,
    tg_id: int | None = None,
) -> Identity:
    """Создаёт идентичность; можно задать email и/или tg_id."""
    if tg_id:
        await _lock_identity_channel(session, "tg", tg_id)
        billing_user = (
            await session.execute(
                select(User).where(User.tg_id == tg_id).with_for_update().execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if billing_user is not None and billing_user.identity_id is not None:
            raise ValueError("Identity has a foreign Telegram owner")
    identity = Identity(
        email=email.strip().lower() if email else None,
        tg_id=tg_id,
        signup_origin=client_origin(),
    )
    session.add(identity)
    await session.flush()
    if tg_id:
        await session.execute(User.__table__.update().where(User.tg_id == tg_id).values(identity_id=identity.id))
    await session.refresh(identity)
    return identity


async def get_identity_by_id(session: AsyncSession, identity_id: str) -> Identity | None:
    """Возвращает идентичность по id."""
    result = await session.execute(select(Identity).where(Identity.id == identity_id))
    return result.scalar_one_or_none()


async def get_identity_by_email(session: AsyncSession, email: str) -> Identity | None:
    """Возвращает идентичность по email."""
    if not email or not email.strip():
        return None
    result = await session.execute(select(Identity).where(Identity.email == email.strip().lower()))
    return result.scalar_one_or_none()


async def get_identity_by_tg_id(session: AsyncSession, tg_id: int) -> Identity | None:
    """Возвращает идентичность по tg_id."""
    result = await session.execute(select(Identity).where(Identity.tg_id == tg_id))
    return result.scalar_one_or_none()


async def get_identity_by_google_sub(session: AsyncSession, google_sub: str) -> Identity | None:
    """Возвращает идентичность по Google sub."""
    if not google_sub:
        return None
    result = await session.execute(select(Identity).where(Identity.google_sub == google_sub))
    return result.scalar_one_or_none()


async def get_or_create_identity_for_google(
    session: AsyncSession,
    google_sub: str,
    email: str | None = None,
) -> Identity:
    """Находит или создаёт идентичность для входа через Google."""
    identity = await get_identity_by_google_sub(session, google_sub)
    if identity:
        if email and not identity.email:
            email_clean = email.strip().lower()
            if email_clean and not await get_identity_by_email(session, email_clean):
                identity.email = email_clean
                identity.email_verified = True
                await session.flush()
        return identity

    if email:
        email_clean = email.strip().lower()
        existing_by_email = await get_identity_by_email(session, email_clean)
        if existing_by_email and existing_by_email.google_sub is None:
            existing_by_email.google_sub = google_sub
            existing_by_email.email_verified = True
            await session.flush()
            await session.refresh(existing_by_email)
            return existing_by_email

    identity = Identity(
        google_sub=google_sub,
        email=(email.strip().lower() if email else None),
        email_verified=bool(email),
    )
    session.add(identity)
    await session.flush()
    await session.refresh(identity)
    return identity


async def get_identity_by_yandex_sub(session: AsyncSession, yandex_sub: str) -> Identity | None:
    """Возвращает идентичность по Яндекс ID."""
    if not yandex_sub:
        return None
    result = await session.execute(select(Identity).where(Identity.yandex_sub == yandex_sub))
    return result.scalar_one_or_none()


async def get_or_create_identity_for_yandex(
    session: AsyncSession,
    yandex_sub: str,
    email: str | None = None,
) -> Identity:
    """Находит или создаёт идентичность для входа через Яндекс."""
    identity = await get_identity_by_yandex_sub(session, yandex_sub)
    if identity:
        if email and not identity.email:
            email_clean = email.strip().lower()
            if email_clean and not await get_identity_by_email(session, email_clean):
                identity.email = email_clean
                identity.email_verified = True
                await session.flush()
        return identity

    if email:
        email_clean = email.strip().lower()
        existing_by_email = await get_identity_by_email(session, email_clean)
        if existing_by_email and existing_by_email.yandex_sub is None:
            existing_by_email.yandex_sub = yandex_sub
            existing_by_email.email_verified = True
            await session.flush()
            await session.refresh(existing_by_email)
            return existing_by_email

    identity = Identity(
        yandex_sub=yandex_sub,
        email=(email.strip().lower() if email else None),
        email_verified=bool(email),
    )
    session.add(identity)
    await session.flush()
    await session.refresh(identity)
    return identity


async def issue_token_for_identity(
    session: AsyncSession,
    identity: Identity,
    *,
    request=None,
) -> str:
    """Выдаёт токен и создаёт сессию идентичности."""
    token = generate_token()
    token_hash = await run_io(hash_token, token)
    user_agent, ip = _request_meta(request)
    await _idsess.create_identity_session(
        session,
        identity=identity,
        token_hash=token_hash,
        user_agent=user_agent,
        ip=ip,
    )
    return token


async def create_identity_with_token(
    session: AsyncSession,
    email: str | None = None,
    password: str | None = None,
    tg_id: int | None = None,
    *,
    request=None,
) -> tuple[Identity, str]:
    """Создаёт идентичность и выдаёт токен."""
    identity = await create_identity(session, email=email, tg_id=tg_id)
    if password:
        identity.password_hash = await run_cpu(hash_password, password)
        await session.flush()
        await session.refresh(identity)
    token = await issue_token_for_identity(session, identity, request=request)
    return identity, token


async def verify_identity_token(session: AsyncSession, identity_id: str, token: str) -> Identity | None:
    """Проверяет пару identity_id + token через identity_sessions; возвращает Identity или None."""
    token_hash = await run_io(hash_token, token)
    sess = await _idsess.get_session_by_token_hash(session, token_hash)
    if sess is None or sess.identity_id != identity_id:
        return None
    if sess.expires_at is not None and sess.expires_at <= datetime.utcnow():
        return None
    return await get_identity_by_id(session, identity_id)


async def login_by_email(
    session: AsyncSession,
    email: str,
    password: str,
    *,
    request=None,
) -> tuple[Identity, str] | None:
    """Проверяет пароль и выдаёт токен входа по email."""
    email_clean = email.strip().lower()
    identity = await get_identity_by_email(session, email_clean)
    if not identity:
        return None
    password_hash = identity.password_hash
    if not await run_cpu(check_password, password, password_hash):
        return None
    identity = (await _lock_identities(session, identity.id)).get(identity.id)
    if identity is None or identity.email != email_clean or identity.password_hash != password_hash:
        return None
    token = await issue_token_for_identity(session, identity, request=request)
    return identity, token


async def login_by_verified_email(session: AsyncSession, email: str, *, request=None) -> tuple[Identity, str] | None:
    """Выдаёт сессию владельцу подтверждённой почты."""
    email_clean = email.strip().lower()
    identity = await get_identity_by_email(session, email_clean)
    if identity is None:
        return None
    identity = (await _lock_identities(session, identity.id)).get(identity.id)
    if identity is None or identity.email != email_clean:
        return None
    identity.email_verified = True
    await session.flush()
    token = await issue_token_for_identity(session, identity, request=request)
    return identity, token


async def mark_identity_email_verified(session: AsyncSession, identity_id: str, email: str) -> Identity | None:
    """Подтверждает только проверенный адрес текущего аккаунта."""
    identity = (await _lock_identities(session, identity_id)).get(identity_id)
    if identity is None or identity.email != email.strip().lower():
        return None
    identity.email_verified = True
    await session.flush()
    return identity


async def set_initial_password(
    session: AsyncSession,
    identity_id: str,
    password: str,
) -> Identity | None:
    """Устанавливает первый пароль без перезаписи существующего."""
    identity = await get_identity_by_id(session, identity_id)
    if not identity or identity.password_hash:
        return None
    password_hash = await run_cpu(hash_password, password)
    identity = (await _lock_identities(session, identity_id)).get(identity_id)
    if identity is None or identity.password_hash:
        return None
    identity.password_hash = password_hash
    await session.flush()
    await session.refresh(identity)
    return identity


async def set_password_for_identity(
    session: AsyncSession,
    identity_id: str,
    new_password: str,
    *,
    expected_email: str | None = None,
) -> Identity | None:
    """Меняет пароль после проверки текущей привязки почты."""
    identity = await get_identity_by_id(session, identity_id)
    if not identity:
        return None
    password_hash = await run_cpu(hash_password, new_password)
    identity = (await _lock_identities(session, identity_id)).get(identity_id)
    if identity is None or (expected_email is not None and identity.email != expected_email.strip().lower()):
        return None
    identity.password_hash = password_hash
    await session.flush()
    await session.refresh(identity)
    return identity


async def change_identity_password(
    session: AsyncSession,
    identity_id: str,
    current_password: str,
    new_password: str,
) -> str | None:
    """Меняет пароль после проверки текущего."""
    identity = await get_identity_by_id(session, identity_id)
    if not identity:
        return "wrong_password"
    if not identity.password_hash:
        return "no_password"
    previous_hash = identity.password_hash
    if not await run_cpu(check_password, current_password, previous_hash):
        return "wrong_password"
    password_hash = await run_cpu(hash_password, new_password)
    identity = (await _lock_identities(session, identity_id)).get(identity_id)
    if identity is None or identity.password_hash != previous_hash:
        return "wrong_password"
    identity.password_hash = password_hash
    await session.flush()
    await session.refresh(identity)
    return None


async def _attribute_client_campaign(session: AsyncSession, user_id: int | None) -> None:
    """Закрепляет рекламный источник посетителя сайта за клиентом."""
    campaign = client_campaign()
    if not campaign or user_id is None:
        return
    from database.tracking_sources import attribute_source_if_known

    await attribute_source_if_known(session, UserId(user_id), campaign)


async def ensure_billing_user_for_identity(session: AsyncSession, identity: Identity) -> int:
    identity = (await _lock_identities(session, identity.id)).get(identity.id)
    if identity is None:
        raise ValueError("Identity disappeared during registration")
    if identity.tg_id is not None:
        tid = TelegramId(identity.tg_id)
        if not await check_user_exists(session, tid):
            await add_user(session, tid)
        ur = await session.execute(
            select(User).where(User.tg_id == tid).with_for_update().execution_options(populate_existing=True)
        )
        u = ur.scalar_one()
        if u.identity_id is not None and u.identity_id != identity.id:
            raise ValueError("Identity has a foreign Telegram owner")
        if u.identity_id != identity.id:
            await session.execute(update(User).where(User.id == u.id).values(identity_id=identity.id))
        await _consolidate_billing_users(session, identity, u.id)
        await _attribute_client_campaign(session, u.id)
        return UserId(u.id)
    res = await session.execute(
        select(User).where(User.identity_id == identity.id).order_by(User.id).execution_options(populate_existing=True)
    )
    row = res.scalars().first()
    if row is not None:
        await _consolidate_billing_users(session, identity, row.id)
        await _attribute_client_campaign(session, row.id)
        return UserId(row.id)
    if await session.scalar(select(Identity.id).where(Identity.id == identity.id).with_for_update()) is None:
        raise ValueError("Identity disappeared during registration")
    row = (
        (
            await session.execute(
                select(User).where(User.identity_id == identity.id).execution_options(populate_existing=True)
            )
        )
        .scalars()
        .first()
    )
    if row is not None:
        await _consolidate_billing_users(session, identity, row.id)
        await _attribute_client_campaign(session, row.id)
        return UserId(row.id)
    new_u = User(identity_id=identity.id, tg_id=None)
    session.add(new_u)
    await session.flush()
    await _attribute_client_campaign(session, new_u.id)
    try:
        from services.admin_notify import notify_new_client

        await notify_new_client(session, int(new_u.id))
    except Exception as exc:
        logger.warning("[Identity] Уведомление админам о новом клиенте не ушло: {}", exc)
    return UserId(new_u.id)


async def _lock_identities(session: AsyncSession, *identity_ids: str) -> dict[str, Identity]:
    rows = (
        (
            await session.execute(
                select(Identity)
                .where(Identity.id.in_(identity_ids))
                .order_by(Identity.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    return {row.id: row for row in rows}


async def _lock_identity_channel(session: AsyncSession, channel: str, value: str | int) -> None:
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"identity:{channel}:{value}", 0))))


async def _lock_users(session: AsyncSession, *user_ids: int) -> dict[int, User]:
    rows = (
        (
            await session.execute(
                select(User)
                .where(User.id.in_(user_ids))
                .order_by(User.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    return {int(row.id): row for row in rows}


async def _combine_billing_users(session: AsyncSession, src_uid: int, dst_uid: int, identity_id: str) -> None:
    if src_uid == dst_uid:
        return
    owners = await _lock_users(session, src_uid, dst_uid)
    source, target = owners.get(src_uid), owners.get(dst_uid)
    if source is None or target is None:
        raise ValueError("Account disappeared during merge")
    merged_balance = await update_balance(session, UserId(dst_uid), float(source.balance or 0.0), allow_negative=True)
    if merged_balance is None:
        raise ValueError("Account balance could not be transferred")
    source_trial, target_trial = int(source.trial or 0), int(target.trial or 0)
    merged_trial = (
        min(source_trial, target_trial)
        if source_trial in (-1, 0) and target_trial in (-1, 0)
        else max(source_trial, target_trial)
    )
    if merged_trial != target_trial:
        await session.execute(update(User).where(User.id == dst_uid).values(trial=merged_trial))
    await _transfer_user_data(session, src_uid, dst_uid, target.tg_id, identity_id)


async def _consolidate_billing_users(session: AsyncSession, identity: Identity, preferred_uid: int) -> None:
    rows = (
        (
            await session.execute(
                select(User)
                .where(User.identity_id == identity.id)
                .order_by(User.id)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    if any(row.tg_id is not None and row.tg_id > 0 and row.tg_id != identity.tg_id for row in rows):
        raise ValueError("Identity has conflicting Telegram owners")
    if len(rows) < 2:
        return
    await _lock_identities(session, identity.id)
    rows = (
        (
            await session.execute(
                select(User)
                .where(User.identity_id == identity.id)
                .order_by(User.id)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    if any(row.tg_id is not None and row.tg_id > 0 and row.tg_id != identity.tg_id for row in rows):
        raise ValueError("Identity has conflicting Telegram owners")
    for row in rows:
        if row.id != preferred_uid:
            await _combine_billing_users(session, row.id, preferred_uid, identity.id)


async def _transfer_user_data(
    session: AsyncSession,
    src_uid: int,
    dst_uid: int,
    dst_tg: int | None,
    dst_identity_id: str,
) -> None:
    """Переносит данные между записями клиентов."""
    if src_uid == dst_uid:
        return
    by_id = await _lock_users(session, src_uid, dst_uid)
    if src_uid not in by_id or dst_uid not in by_id:
        raise ValueError("Account disappeared during merge")
    source, target = by_id[src_uid], by_id[dst_uid]
    await freeze_legacy_tg_owner(session, src_uid)
    await freeze_legacy_tg_owner(session, dst_uid)
    values = {
        field: getattr(source, field)
        for field in ("username", "first_name", "last_name", "language_code", "source_code")
        if not getattr(target, field) and getattr(source, field)
    }
    for field in ("created_at", "legal_accepted_at"):
        src_value, dst_value = getattr(source, field), getattr(target, field)
        if src_value is not None and (dst_value is None or src_value < dst_value):
            values[field] = src_value
    if values:
        await session.execute(update(User).where(User.id == dst_uid).values(**values))
    await invalidate_identity_cache(session, identity_ids=(dst_identity_id,), user_ids=(src_uid, dst_uid))
    await session.execute(update(Key).where(Key.user_id == src_uid).values(user_id=dst_uid))
    await session.execute(update(Payment).where(Payment.user_id == src_uid).values(user_id=dst_uid))
    await transfer_recurring_user_data(session, src_uid, dst_uid)

    for notification in (
        (await session.execute(select(Notification).where(Notification.user_id == src_uid))).scalars().all()
    ):
        existing = await session.scalar(
            select(Notification).where(
                Notification.user_id == dst_uid,
                Notification.notification_type == notification.notification_type,
            )
        )
        if existing is not None:
            if notification.last_notification_time is not None and (
                existing.last_notification_time is None
                or notification.last_notification_time > existing.last_notification_time
            ):
                existing.last_notification_time = notification.last_notification_time
            await session.execute(
                delete(Notification).where(
                    Notification.user_id == src_uid,
                    Notification.notification_type == notification.notification_type,
                )
            )
    await session.execute(update(Notification).where(Notification.user_id == src_uid).values(user_id=dst_uid))

    await session.execute(update(Gift).where(Gift.sender_user_id == src_uid).values(sender_user_id=dst_uid))
    await session.execute(update(Gift).where(Gift.recipient_user_id == src_uid).values(recipient_user_id=dst_uid))

    await session.execute(
        text(
            "DELETE FROM gift_usages AS g1 USING gift_usages AS g2 "
            "WHERE g1.user_id = :src AND g2.user_id = :dst AND g1.gift_id = g2.gift_id"
        ),
        {"src": src_uid, "dst": dst_uid},
    )
    await session.execute(update(GiftUsage).where(GiftUsage.user_id == src_uid).values(user_id=dst_uid))

    await session.execute(
        text(
            "DELETE FROM coupon_usages AS c1 USING coupon_usages AS c2 "
            "WHERE c1.user_id = :src AND c2.user_id = :dst AND c1.coupon_id = c2.coupon_id"
        ),
        {"src": src_uid, "dst": dst_uid},
    )
    await session.execute(update(CouponUsage).where(CouponUsage.user_id == src_uid).values(user_id=dst_uid))

    for model in (TemporaryData, CouponHold):
        if await session.scalar(select(model.user_id).where(model.user_id == dst_uid)) is not None:
            await session.execute(delete(model).where(model.user_id == src_uid))
        else:
            await session.execute(update(model).where(model.user_id == src_uid).values(user_id=dst_uid))
    await session.execute(
        update(DailyBonusClaim).where(DailyBonusClaim.user_id == src_uid).values(user_id=dst_uid, tg_id=dst_tg)
    )
    await session.execute(update(SubscriptionEvent).where(SubscriptionEvent.user_id == src_uid).values(user_id=dst_uid))

    for referral in (
        (
            await session.execute(
                select(Referral).where(
                    (Referral.referred_user_id == src_uid) | (Referral.referrer_user_id == src_uid),
                    Referral.reward_issued.is_(True),
                )
            )
        )
        .scalars()
        .all()
    ):
        referred = dst_uid if referral.referred_user_id == src_uid else referral.referred_user_id
        referrer = dst_uid if referral.referrer_user_id == src_uid else referral.referrer_user_id
        await session.execute(
            update(Referral)
            .where(
                Referral.referred_user_id == referred,
                Referral.referrer_user_id == referrer,
            )
            .values(reward_issued=True)
        )
    await session.execute(
        update(ScheduledBroadcast)
        .where(ScheduledBroadcast.created_by_user_id == src_uid)
        .values(created_by_user_id=dst_uid)
    )

    await session.execute(
        text(
            "DELETE FROM referrals AS r1 USING referrals AS r2 "
            "WHERE r1.referred_user_id = :src AND r2.referred_user_id = :dst "
            "AND r1.referrer_user_id = r2.referrer_user_id"
        ),
        {"src": src_uid, "dst": dst_uid},
    )
    await session.execute(
        text(
            "DELETE FROM referrals AS r1 USING referrals AS r2 "
            "WHERE r1.referrer_user_id = :src AND r2.referrer_user_id = :dst "
            "AND r1.referred_user_id = r2.referred_user_id"
        ),
        {"src": src_uid, "dst": dst_uid},
    )
    await session.execute(
        text(
            "DELETE FROM referrals "
            "WHERE (referred_user_id = :src AND referrer_user_id = :dst) "
            "OR (referred_user_id = :dst AND referrer_user_id = :src)"
        ),
        {"src": src_uid, "dst": dst_uid},
    )
    dst_has_referrer = (
        await session.execute(select(Referral.referrer_user_id).where(Referral.referred_user_id == dst_uid).limit(1))
    ).first() is not None
    if dst_has_referrer:
        await session.execute(delete(Referral).where(Referral.referred_user_id == src_uid))
    await session.execute(update(Referral).where(Referral.referred_user_id == src_uid).values(referred_user_id=dst_uid))
    await session.execute(update(Referral).where(Referral.referrer_user_id == src_uid).values(referrer_user_id=dst_uid))

    for model in (WebPushSubscription, WebNotification):
        scope = (model.user_id == src_uid) & model.identity_id.is_(None)
        if source.identity_id is not None:
            scope |= model.identity_id == source.identity_id
        await session.execute(update(model).where(scope).values(user_id=dst_uid))

    dst_ban = (await session.execute(select(ManualBan).where(ManualBan.user_id == dst_uid))).scalar_one_or_none()
    src_ban = (await session.execute(select(ManualBan).where(ManualBan.user_id == src_uid))).scalar_one_or_none()
    if src_ban is not None and dst_ban is None:
        session.add(
            ManualBan(
                user_id=dst_uid,
                tg_id=dst_tg,
                banned_at=src_ban.banned_at,
                reason=src_ban.reason,
                banned_by=src_ban.banned_by,
                until=src_ban.until,
            )
        )
    elif src_ban is not None and dst_ban is not None and dst_ban.until is not None:
        if src_ban.until is None or src_ban.until > dst_ban.until:
            for field in ("banned_at", "reason", "banned_by", "until"):
                setattr(dst_ban, field, getattr(src_ban, field))

    dst_block = (await session.execute(select(BlockedUser).where(BlockedUser.user_id == dst_uid))).scalar_one_or_none()
    src_block = (await session.execute(select(BlockedUser).where(BlockedUser.user_id == src_uid))).scalar_one_or_none()
    if src_block is not None and dst_block is None:
        session.add(BlockedUser(user_id=dst_uid, tg_id=dst_tg))

    if dst_tg is not None and dst_tg > 0:
        await session.execute(
            update(AuditEvent)
            .where(AuditEvent.actor_identity_id == dst_identity_id, AuditEvent.actor_tg_id.is_(None))
            .values(actor_tg_id=dst_tg)
        )

    await refresh_tg_mirrors_for_user(session, dst_uid)
    await transfer_partner_user_data(session, src_uid, dst_uid)

    src_tg = (await session.execute(select(User.tg_id).where(User.id == src_uid))).scalar_one_or_none()
    from hooks.hooks import run_hooks

    await run_hooks(
        "user_data_transfer",
        require_enabled=False,
        raise_on_error=True,
        src_user_id=UserId(src_uid),
        dst_user_id=UserId(dst_uid),
        src_tg_id=src_tg,
        dst_tg_id=dst_tg,
        partner_data_transferred=True,
        session=session,
    )

    await session.execute(delete(User).where(User.id == src_uid))
    await session.execute(update(User).where(User.id == dst_uid).values(identity_id=dst_identity_id))

    await invalidate_balance_cache(src_uid)
    await invalidate_profile_cache(src_uid)
    await invalidate_balance_cache(dst_uid)
    await invalidate_profile_cache(dst_uid)


def _can_merge_identity_channels(source: Identity, target: Identity) -> bool:
    return all(
        not (getattr(source, field) and getattr(target, field) and getattr(source, field) != getattr(target, field))
        for field in ("google_sub", "yandex_sub")
    )


async def _transfer_identity_data(session: AsyncSession, source: Identity, target: Identity) -> None:
    """Переносит сессии и историю объединяемой идентичности."""
    await invalidate_identity_cache(session, identity_ids=(source.id, target.id))
    channels = {field: getattr(source, field) for field in ("google_sub", "yandex_sub")}
    source.email = None
    source.tg_id = None
    source.google_sub = None
    source.yandex_sub = None
    await session.flush()
    for field, value in channels.items():
        if value and not getattr(target, field):
            setattr(target, field, value)
    for field in (
        "password_hash",
        "display_name",
        "username",
        "avatar_url",
        "signup_origin",
        "onboarding_completed_at",
        "onboarding_stage",
    ):
        if not getattr(target, field) and getattr(source, field):
            setattr(target, field, getattr(source, field))
    for model, column in (
        (User, User.identity_id),
        (IdentitySession, IdentitySession.identity_id),
        (Ticket, Ticket.identity_id),
        (AuditEvent, AuditEvent.actor_identity_id),
        (WebNotification, WebNotification.identity_id),
        (WebPushSubscription, WebPushSubscription.identity_id),
        (WebErrorReport, WebErrorReport.last_identity_id),
    ):
        await session.execute(update(model).where(column == source.id).values({column.key: target.id}))
    await session.execute(
        update(AuditEvent)
        .where(
            AuditEvent.entity_type == "identity",
            AuditEvent.entity_id == source.id,
        )
        .values(entity_id=target.id)
    )
    if source.api_token_hash and not await session.scalar(
        select(IdentitySession.id).where(
            IdentitySession.token_hash == source.api_token_hash,
        )
    ):
        issued = source.token_issued_at or datetime.utcnow()
        session.add(
            IdentitySession(
                identity_id=target.id,
                token_hash=source.api_token_hash,
                created_at=issued,
                last_seen_at=issued,
                expires_at=issued + timedelta(days=API_TOKEN_TTL_DAYS) if API_TOKEN_TTL_DAYS else None,
            )
        )
    for pref in (
        (await session.execute(select(IdentityNotifPref).where(IdentityNotifPref.identity_id == source.id)))
        .scalars()
        .all()
    ):
        exists = await session.scalar(
            select(IdentityNotifPref.identity_id).where(
                IdentityNotifPref.identity_id == target.id,
                IdentityNotifPref.channel == pref.channel,
            )
        )
        if exists is not None:
            await session.execute(
                delete(IdentityNotifPref).where(
                    IdentityNotifPref.identity_id == source.id,
                    IdentityNotifPref.channel == pref.channel,
                )
            )
        else:
            pref.identity_id = target.id
    await session.flush()
    await session.execute(delete(Identity).where(Identity.id == source.id))


async def merge_billing_user_into_telegram(
    session: AsyncSession,
    identity_id: str,
    telegram_tg_id: int,
    *,
    telegram_identity_id: str | None = None,
) -> None:
    res = await session.execute(select(User).where(User.identity_id == identity_id).order_by(User.id))
    rows = res.scalars().all()
    dst_tg = int(telegram_tg_id)
    if any(row.tg_id is not None and row.tg_id > 0 and int(row.tg_id) != dst_tg for row in rows):
        raise ValueError("Identity has conflicting Telegram owners")

    dst_u = await get_user_by_tg_id(session, dst_tg)
    if telegram_identity_id is None:
        telegram_identity_id = await session.scalar(select(Identity.id).where(Identity.tg_id == dst_tg))
    source_ids = tuple(int(row.id) for row in rows)
    locked = await _lock_users(session, *source_ids, *((dst_u.id,) if dst_u is not None else ()))
    if dst_u is not None:
        dst_u = locked.get(dst_u.id)
        if dst_u is None or dst_u.tg_id != dst_tg:
            raise ValueError("Telegram owner changed during merge")
        if dst_u.identity_id not in (None, identity_id, telegram_identity_id):
            raise ValueError("Identity has a foreign Telegram owner")
    if not rows:
        return
    billing = locked[source_ids[0]]
    await invalidate_identity_cache(
        session,
        identity_ids=(identity_id,),
        user_ids=source_ids if dst_u is None else (*source_ids, dst_u.id),
        tg_ids=(dst_tg,),
    )
    if dst_u is None:
        new_u = User(
            tg_id=dst_tg,
            identity_id=identity_id,
            username=billing.username,
            first_name=billing.first_name,
            last_name=billing.last_name,
            language_code=billing.language_code,
            is_bot=billing.is_bot or False,
            balance=0.0,
            trial=int(billing.trial or 0),
            preferred_currency=billing.preferred_currency or "RUB",
            source_code=billing.source_code,
        )
        session.add(new_u)
        await session.flush()
        dst_uid = int(new_u.id)
    else:
        dst_uid = int(dst_u.id)

    for src_uid in source_ids:
        await _combine_billing_users(session, src_uid, dst_uid, identity_id)


async def attach_email(
    session: AsyncSession,
    identity_id: str,
    email: str,
    *,
    verified: bool = False,
    allow_merge: bool = True,
    only_if_unbound: bool = False,
) -> Identity | None:
    """Привязывает email с объединением совместимых аккаунтов."""
    email_clean = email.strip().lower() if email else None
    if email_clean:
        await _lock_identity_channel(session, "email", email_clean)
    identity = await get_identity_by_id(session, identity_id)
    if not identity:
        return None
    if not email_clean:
        return identity
    existing = await get_identity_by_email(session, email_clean)
    locked = await _lock_identities(session, identity_id, *((existing.id,) if existing is not None else ()))
    identity = locked.get(identity_id)
    existing = locked.get(existing.id) if existing is not None else None
    if identity is None:
        return None
    if only_if_unbound and identity.email:
        return None
    if existing and existing.id != identity_id:
        if not allow_merge:
            return None
        our_tg = identity.tg_id
        their_tg = existing.tg_id
        can_merge = their_tg is None or (our_tg is not None and int(their_tg) == int(our_tg))
        if not can_merge or not _can_merge_identity_channels(existing, identity):
            return None

        src_users = (
            (await session.execute(select(User).where(User.identity_id == existing.id).order_by(User.id)))
            .scalars()
            .all()
        )
        if any(user.tg_id is not None and user.tg_id > 0 and user.tg_id != existing.tg_id for user in src_users):
            return None
        dst_uid = await ensure_billing_user_for_identity(session, identity)
        for src_user in src_users:
            await _combine_billing_users(session, int(src_user.id), int(dst_uid), identity_id)

        if existing.password_hash:
            identity.password_hash = existing.password_hash
        identity.email_verified = existing.email_verified
        await _transfer_identity_data(session, existing, identity)
    elif identity.email != email_clean:
        identity.email_verified = False

    await invalidate_identity_cache(session, identity_ids=(identity.id,))
    identity.email = email_clean
    if verified:
        identity.email_verified = True
    await session.flush()
    await session.refresh(identity)
    return identity


async def attach_telegram(session: AsyncSession, identity_id: str, tg_id: int) -> Identity | None:
    """Привязывает Telegram с объединением совместимых аккаунтов."""
    await _lock_identity_channel(session, "tg", tg_id)
    identity = await get_identity_by_id(session, identity_id)
    if not identity:
        return None
    if identity.tg_id is not None and int(identity.tg_id) != int(tg_id):
        return None
    existing = await get_identity_by_tg_id(session, tg_id)
    locked = await _lock_identities(session, identity_id, *((existing.id,) if existing is not None else ()))
    identity = locked.get(identity_id)
    existing = locked.get(existing.id) if existing is not None else None
    if identity is None or (identity.tg_id is not None and int(identity.tg_id) != int(tg_id)):
        return None
    if existing and existing.id != identity_id:
        our_email = str(identity.email).strip().lower() if identity.email else None
        their_email = str(existing.email).strip().lower() if existing.email else None
        can_merge = their_email is None or (our_email is not None and their_email == our_email)
        if not can_merge or not _can_merge_identity_channels(existing, identity):
            return None
    billing_user = await get_user_by_tg_id(session, tg_id)
    if billing_user is not None and billing_user.identity_id not in (
        None,
        identity_id,
        existing.id if existing is not None else None,
    ):
        return None
    await invalidate_identity_cache(session, identity_ids=(identity.id,), tg_ids=(tg_id,))
    await merge_billing_user_into_telegram(
        session, identity_id, tg_id, telegram_identity_id=existing.id if existing is not None else None
    )
    if existing and existing.id != identity_id:
        await _transfer_identity_data(session, existing, identity)
    identity = await get_identity_by_id(session, identity_id)
    if not identity:
        return None
    identity.tg_id = tg_id
    admin_row = await session.execute(select(Admin).where(Admin.tg_id == tg_id))
    if admin_row.scalar_one_or_none():
        identity.is_admin = True
    await session.execute(User.__table__.update().where(User.tg_id == tg_id).values(identity_id=identity_id))
    billing_user = await get_user_by_tg_id(session, tg_id)
    if billing_user is not None:
        await _consolidate_billing_users(session, identity, billing_user.id)
    await session.flush()
    await session.refresh(identity)
    return identity


async def detach_email(session: AsyncSession, identity_id: str) -> Identity | None:
    """Отвязывает email при наличии Telegram."""
    identity = (await _lock_identities(session, identity_id)).get(identity_id)
    if not identity:
        return None
    if identity.email is None:
        return identity
    if identity.tg_id is None:
        return None
    await invalidate_identity_cache(session, identity_ids=(identity_id,))
    identity.email = None
    identity.email_verified = False
    identity.password_hash = None
    await session.flush()
    await session.refresh(identity)
    return identity


async def detach_telegram(session: AsyncSession, identity_id: str) -> Identity | None:
    """Отвязывает Telegram при наличии email."""
    identity = (await _lock_identities(session, identity_id)).get(identity_id)
    if not identity:
        return None
    if identity.tg_id is None:
        return identity
    if identity.email is None:
        return None
    await invalidate_identity_cache(session, identity_ids=(identity_id,))
    old_tg = int(identity.tg_id)
    affected = (
        (await session.execute(select(User.id).where(User.identity_id == identity_id, User.tg_id == old_tg)))
        .scalars()
        .all()
    )

    identity.tg_id = None
    identity.is_admin = False
    await release_tg_mirrors(session, old_tg)
    await session.execute(update(User).where(User.identity_id == identity_id, User.tg_id == old_tg).values(tg_id=None))
    for user_id in affected:
        await refresh_tg_mirrors_for_user(session, user_id)
    await session.flush()
    await session.refresh(identity)
    return identity


async def get_or_create_identity_for_tg(session: AsyncSession, tg_id: int) -> Identity:
    """Находит или создаёт идентичность для Telegram."""
    await _lock_identity_channel(session, "tg", tg_id)
    is_admin = (await session.execute(select(Admin).where(Admin.tg_id == tg_id))).scalar_one_or_none() is not None
    identity = await get_identity_by_tg_id(session, tg_id)
    if identity:
        identity = (await _lock_identities(session, identity.id)).get(identity.id)
    if identity is not None and identity.tg_id == tg_id:
        if is_admin and not identity.is_admin:
            identity.is_admin = True
            await session.flush()
            await session.refresh(identity)
        return identity
    billing_user = (
        await session.execute(
            select(User).where(User.tg_id == tg_id).with_for_update().execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if billing_user is not None and billing_user.identity_id is not None:
        raise ValueError("Identity has a foreign Telegram owner")
    identity = Identity(tg_id=tg_id, is_admin=is_admin, signup_origin=client_origin())
    session.add(identity)
    await session.flush()
    await session.execute(User.__table__.update().where(User.tg_id == tg_id).values(identity_id=identity.id))
    await session.refresh(identity)
    return identity

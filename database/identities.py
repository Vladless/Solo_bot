import hashlib
import secrets

from datetime import datetime, timedelta

import bcrypt

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.executor import run_cpu, run_io
from database import identity_sessions as _idsess
from database.access.tg_mirror import refresh_tg_mirrors_for_user
from database.models import Admin, Identity, User
from logger import logger
from settings.config import API_TOKEN_TTL_DAYS


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


_BCRYPT_MAX_PASSWORD_BYTES = 72
_BCRYPT_ROUNDS = 12


def _password_bytes(password: str) -> bytes:
    """Пароль в байтах, не длиннее 72 байт (ограничение bcrypt)."""
    raw = password.encode("utf-8")
    if len(raw) > _BCRYPT_MAX_PASSWORD_BYTES:
        return raw[:_BCRYPT_MAX_PASSWORD_BYTES]
    return raw


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(password: str) -> str:
    """Хеш пароля через bcrypt (соль уникальна на каждый пароль)."""
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    return bcrypt.hashpw(_password_bytes(password), salt).decode("ascii")


def check_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(_password_bytes(password), password_hash.encode("ascii"))
    except Exception:
        return False


def generate_token() -> str:
    return secrets.token_urlsafe(32)


async def create_identity(
    session: AsyncSession,
    email: str | None = None,
    tg_id: int | None = None,
) -> Identity:
    """Создаёт идентичность; можно задать email и/или tg_id."""
    identity = Identity(email=email.strip().lower() if email else None, tg_id=tg_id)
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
    """Возвращает идентичность по Google `sub` (стабильный ID пользователя от Google)."""
    if not google_sub:
        return None
    result = await session.execute(select(Identity).where(Identity.google_sub == google_sub))
    return result.scalar_one_or_none()


async def get_or_create_identity_for_google(
    session: AsyncSession,
    google_sub: str,
    email: str | None = None,
) -> Identity:
    """Для Google `sub` возвращает существующую идентичность или создаёт новую.

    Логика мёрджа:
    1. Если identity с этим google_sub уже есть — возвращаем её.
    2. Если нет, но есть identity с таким же email (верифицированный или нет) — подхватываем её, пишем google_sub, ставим email_verified=True.
    3. Иначе создаём новую identity с google_sub + email.
    """
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


async def attach_google(
    session: AsyncSession,
    identity_id: str,
    google_sub: str,
) -> Identity | None:
    """Привязывает Google-аккаунт к существующей identity.

    Возвращает None, если этот google_sub уже привязан к другой identity.
    """
    identity = await get_identity_by_id(session, identity_id)
    if not identity:
        return None
    if identity.google_sub == google_sub:
        return identity
    existing = await get_identity_by_google_sub(session, google_sub)
    if existing and existing.id != identity_id:
        return None
    identity.google_sub = google_sub
    await session.flush()
    await session.refresh(identity)
    return identity


async def detach_google(session: AsyncSession, identity_id: str) -> Identity | None:
    """Отвязывает Google от identity. Запрещено если это единственный канал."""
    identity = await get_identity_by_id(session, identity_id)
    if not identity:
        return None
    if identity.google_sub is None:
        return identity
    if identity.email is None and identity.tg_id is None:
        return None
    identity.google_sub = None
    await session.flush()
    await session.refresh(identity)
    return identity


async def get_identity_by_yandex_sub(session: AsyncSession, yandex_sub: str) -> Identity | None:
    """Возвращает идентичность по Яндекс ID (поле `id` из https://login.yandex.ru/info)."""
    if not yandex_sub:
        return None
    result = await session.execute(select(Identity).where(Identity.yandex_sub == yandex_sub))
    return result.scalar_one_or_none()


async def get_or_create_identity_for_yandex(
    session: AsyncSession,
    yandex_sub: str,
    email: str | None = None,
) -> Identity:
    """Для Яндекс `id` возвращает существующую идентичность или создаёт новую.

    Мёрджится с existing identity по email, если тот свободен. Email от Яндекса
    считается верифицированным (Яндекс не отдаёт default_email без подтверждения).
    """
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


async def attach_yandex(
    session: AsyncSession,
    identity_id: str,
    yandex_sub: str,
) -> Identity | None:
    """Привязывает Яндекс-аккаунт к existing identity. None если yandex_sub занят чужой identity."""
    identity = await get_identity_by_id(session, identity_id)
    if not identity:
        return None
    if identity.yandex_sub == yandex_sub:
        return identity
    existing = await get_identity_by_yandex_sub(session, yandex_sub)
    if existing and existing.id != identity_id:
        return None
    identity.yandex_sub = yandex_sub
    await session.flush()
    await session.refresh(identity)
    return identity


async def detach_yandex(session: AsyncSession, identity_id: str) -> Identity | None:
    """Отвязывает Яндекс от identity. Запрещено если это единственный канал."""
    identity = await get_identity_by_id(session, identity_id)
    if not identity:
        return None
    if identity.yandex_sub is None:
        return identity
    if identity.email is None and identity.tg_id is None and identity.google_sub is None:
        return None
    identity.yandex_sub = None
    await session.flush()
    await session.refresh(identity)
    return identity


async def get_identity_by_token_hash(session: AsyncSession, token_hash: str) -> Identity | None:
    """Возвращает идентичность по хешу API-токена."""
    result = await session.execute(select(Identity).where(Identity.api_token_hash == token_hash))
    return result.scalar_one_or_none()


async def issue_token_for_identity(
    session: AsyncSession,
    identity: Identity,
    *,
    request=None,
) -> str:
    """Генерирует токен и создаёт новую сессию в identity_sessions (не затирая другие устройства)."""
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
    """Создаёт идентичность и выдаёт API-токен. При регистрации по почте передать email и password."""
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
    """Вход по email и паролю: проверяет пароль, выдаёт новый токен; возвращает (identity, token) или None."""
    identity = await get_identity_by_email(session, email)
    if not identity:
        return None
    if not await run_cpu(check_password, password, identity.password_hash):
        return None
    token = await issue_token_for_identity(session, identity, request=request)
    return identity, token


async def set_initial_password(
    session: AsyncSession,
    identity_id: str,
    password: str,
) -> Identity | None:
    identity = await get_identity_by_id(session, identity_id)
    if not identity or identity.password_hash:
        return None
    identity.password_hash = await run_cpu(hash_password, password)
    await session.flush()
    await session.refresh(identity)
    return identity


async def set_password_for_identity(
    session: AsyncSession,
    identity_id: str,
    new_password: str,
) -> Identity | None:
    identity = await get_identity_by_id(session, identity_id)
    if not identity:
        return None
    identity.password_hash = await run_cpu(hash_password, new_password)
    await session.flush()
    await session.refresh(identity)
    return identity


async def change_identity_password(
    session: AsyncSession,
    identity_id: str,
    current_password: str,
    new_password: str,
) -> str | None:
    """Возвращает None при успехе, иначе код: no_password | wrong_password."""
    identity = await get_identity_by_id(session, identity_id)
    if not identity:
        return "wrong_password"
    if not identity.password_hash:
        return "no_password"
    if not await run_cpu(check_password, current_password, identity.password_hash):
        return "wrong_password"
    identity.password_hash = await run_cpu(hash_password, new_password)
    await session.flush()
    await session.refresh(identity)
    return None


async def _assign_synthetic_tg_id(session: AsyncSession, uid: int) -> None:
    synthetic = -int(uid)
    try:
        async with session.begin_nested():
            await session.execute(update(User).where(User.id == uid).values(tg_id=synthetic))
    except Exception as exc:
        logger.warning("[ensure_billing_user] синтетический tg_id для user {} не присвоен: {}", uid, exc)


async def ensure_billing_user_for_identity(session: AsyncSession, identity: Identity) -> int:
    from database.users import add_user, check_user_exists

    if identity.tg_id is not None:
        tid = int(identity.tg_id)
        if not await check_user_exists(session, tid):
            await add_user(session, tid)
        ur = await session.execute(select(User).where(User.tg_id == tid).limit(1))
        u = ur.scalar_one()
        await session.execute(update(User).where(User.id == u.id).values(identity_id=identity.id))
        return int(u.id)
    res = await session.execute(select(User).where(User.identity_id == identity.id))
    row = res.scalars().first()
    if row is not None:
        if row.tg_id is None:
            await _assign_synthetic_tg_id(session, row.id)
        return int(row.id)
    new_u = User(identity_id=identity.id, tg_id=None)
    session.add(new_u)
    await session.flush()
    await _assign_synthetic_tg_id(session, new_u.id)
    return int(new_u.id)


async def _transfer_user_data(
    session: AsyncSession,
    src_uid: int,
    dst_uid: int,
    dst_tg: int | None,
    dst_identity_id: str,
) -> None:
    """Переносит все данные с src User на dst User. Dedup там, где есть уникальные ключи."""
    from database.models import (
        AuditEvent,
        BlockedUser,
        CouponUsage,
        Gift,
        GiftUsage,
        Key,
        ManualBan,
        Notification,
        Payment,
        Referral,
        ScheduledBroadcast,
        TemporaryData,
        WebNotification,
        WebPushSubscription,
    )
    from database.users import invalidate_balance_cache, invalidate_profile_cache

    await session.execute(update(Key).where(Key.user_id == src_uid).values(user_id=dst_uid))
    await session.execute(update(Payment).where(Payment.user_id == src_uid).values(user_id=dst_uid))

    await session.execute(
        text(
            "DELETE FROM notifications AS n1 USING notifications AS n2 "
            "WHERE n1.user_id = :src AND n2.user_id = :dst AND n1.notification_type = n2.notification_type"
        ),
        {"src": src_uid, "dst": dst_uid},
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

    await session.execute(update(TemporaryData).where(TemporaryData.user_id == src_uid).values(user_id=dst_uid))

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
    await session.execute(update(Referral).where(Referral.referred_user_id == src_uid).values(referred_user_id=dst_uid))
    await session.execute(update(Referral).where(Referral.referrer_user_id == src_uid).values(referrer_user_id=dst_uid))

    await session.execute(
        update(WebPushSubscription).where(WebPushSubscription.user_id == src_uid).values(user_id=dst_uid)
    )
    await session.execute(update(WebNotification).where(WebNotification.user_id == src_uid).values(user_id=dst_uid))

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

    dst_block = (await session.execute(select(BlockedUser).where(BlockedUser.user_id == dst_uid))).scalar_one_or_none()
    src_block = (await session.execute(select(BlockedUser).where(BlockedUser.user_id == src_uid))).scalar_one_or_none()
    if src_block is not None and dst_block is None:
        session.add(BlockedUser(user_id=dst_uid, tg_id=dst_tg))

    if dst_tg is not None:
        await session.execute(
            update(AuditEvent)
            .where(AuditEvent.actor_identity_id == dst_identity_id, AuditEvent.actor_tg_id.is_(None))
            .values(actor_tg_id=dst_tg)
        )

    await refresh_tg_mirrors_for_user(session, dst_uid)

    src_tg = (await session.execute(select(User.tg_id).where(User.id == src_uid))).scalar_one_or_none()
    from hooks.hooks import run_hooks

    await run_hooks(
        "user_data_transfer",
        src_user_id=src_uid,
        dst_user_id=dst_uid,
        src_tg_id=src_tg,
        dst_tg_id=dst_tg,
        session=session,
    )

    await session.execute(delete(User).where(User.id == src_uid))
    await session.execute(update(User).where(User.id == dst_uid).values(identity_id=dst_identity_id))

    await invalidate_balance_cache(src_uid)
    await invalidate_profile_cache(src_uid)
    await invalidate_balance_cache(dst_uid)
    await invalidate_profile_cache(dst_uid)


async def merge_billing_user_into_telegram(session: AsyncSession, identity_id: str, telegram_tg_id: int) -> None:
    from database.access.resolution import resolve_user_optional
    from database.models import User as _User  # noqa: F401
    from database.users import update_balance

    res = await session.execute(select(User).where(User.identity_id == identity_id))
    rows = res.scalars().all()
    if not rows:
        return
    billing = rows[0]
    src_uid = int(billing.id)
    dst_tg = int(telegram_tg_id)
    if billing.tg_id is not None and int(billing.tg_id) > 0:
        return

    dst_u = await resolve_user_optional(session, dst_tg)
    if dst_u is None:
        new_u = User(
            tg_id=dst_tg,
            identity_id=identity_id,
            username=billing.username,
            first_name=billing.first_name,
            last_name=billing.last_name,
            language_code=billing.language_code,
            is_bot=billing.is_bot or False,
            balance=float(billing.balance or 0.0),
            trial=int(billing.trial or 0),
            preferred_currency=billing.preferred_currency or "RUB",
            source_code=billing.source_code,
        )
        session.add(new_u)
        await session.flush()
        dst_uid = int(new_u.id)
    else:
        dst_uid = int(dst_u.id)
        bal = float(billing.balance or 0.0)
        if bal:
            await update_balance(session, dst_uid, bal)
        st = int(billing.trial or 0)
        dt_r = await session.execute(select(User.trial).where(User.id == dst_uid))
        dt_val = dt_r.scalar_one_or_none()
        if dt_val is not None and st > int(dt_val or 0):
            await session.execute(update(User).where(User.id == dst_uid).values(trial=st))

    await _transfer_user_data(session, src_uid, dst_uid, dst_tg, identity_id)


async def resolve_tg_id(session: AsyncSession, identity_id: str) -> int | None:
    """По identity_id возвращает внутренний user id (users.id) для биллинга и ключей."""
    identity = await get_identity_by_id(session, identity_id)
    if not identity:
        return None
    return await ensure_billing_user_for_identity(session, identity)


async def attach_email(session: AsyncSession, identity_id: str, email: str) -> Identity | None:
    """Привязывает email к идентичности.

    Если email уже занят другой identity — пытаемся смёржить.
    Условия мёрджа: у занимающей identity нет tg_id ИЛИ tg_id совпадает с нашим.
    Иначе — возврат None (email принадлежит другому человеку, не отдаём).
    """
    identity = await get_identity_by_id(session, identity_id)
    if not identity:
        return None
    email_clean = email.strip().lower() if email else None
    if not email_clean:
        return identity
    existing = await get_identity_by_email(session, email_clean)
    if existing and existing.id != identity_id:
        our_tg = identity.tg_id
        their_tg = existing.tg_id
        can_merge = their_tg is None or (our_tg is not None and int(their_tg) == int(our_tg))
        if not can_merge:
            return None

        src_user = (await session.execute(select(User).where(User.identity_id == existing.id))).scalars().first()
        dst_uid = await ensure_billing_user_for_identity(session, identity)
        dst_tg = int(identity.tg_id) if identity.tg_id is not None else None

        if src_user is not None and int(src_user.id) != int(dst_uid):
            from database.users import update_balance

            src_uid = int(src_user.id)
            bal = float(src_user.balance or 0.0)
            if bal:
                await update_balance(session, dst_uid, bal)
            src_trial = int(src_user.trial or 0)
            dst_trial_val = await session.scalar(select(User.trial).where(User.id == dst_uid))
            if dst_trial_val is not None and src_trial > int(dst_trial_val or 0):
                await session.execute(update(User).where(User.id == dst_uid).values(trial=src_trial))
            await _transfer_user_data(session, src_uid, dst_uid, dst_tg, identity_id)

        existing.email = None
        await session.flush()
        await session.execute(delete(Identity).where(Identity.id == existing.id))

    identity.email = email_clean
    await session.flush()
    await session.refresh(identity)
    return identity


async def attach_telegram(session: AsyncSession, identity_id: str, tg_id: int) -> Identity | None:
    """Привязывает Telegram (tg_id) к идентичности и связывает User с identity.

    Если tg_id уже висит на другой identity — пытаемся смёржить.
    Условия мёрджа: у занимающей identity нет email ИЛИ email совпадает с нашим.
    Иначе — возврат None (TG принадлежит другому человеку).
    """
    identity = await get_identity_by_id(session, identity_id)
    if not identity:
        return None
    existing = await get_identity_by_tg_id(session, tg_id)
    if existing and existing.id != identity_id:
        our_email = str(identity.email).strip().lower() if identity.email else None
        their_email = str(existing.email).strip().lower() if existing.email else None
        can_merge = their_email is None or (our_email is not None and their_email == our_email)
        if not can_merge:
            return None
        existing.email = None
        existing.tg_id = None
        await session.flush()
        await session.execute(delete(Identity).where(Identity.id == existing.id))
        await session.flush()
    await merge_billing_user_into_telegram(session, identity_id, tg_id)
    identity = await get_identity_by_id(session, identity_id)
    if not identity:
        return None
    identity.tg_id = tg_id
    admin_row = await session.execute(select(Admin).where(Admin.tg_id == tg_id))
    if admin_row.scalar_one_or_none():
        identity.is_admin = True
    await session.execute(User.__table__.update().where(User.tg_id == tg_id).values(identity_id=identity_id))
    await session.flush()
    await session.refresh(identity)
    return identity


async def detach_email(session: AsyncSession, identity_id: str) -> Identity | None:
    """Отвязывает email от identity. Возвращает None если у identity не осталось
    ни одного канала (email + tg_id оба пустые) — в этом случае отвязка запрещена,
    иначе identity станет orphan.
    """
    identity = await get_identity_by_id(session, identity_id)
    if not identity:
        return None
    if identity.email is None:
        return identity
    if identity.tg_id is None:
        return None
    identity.email = None
    identity.email_verified = False
    identity.password_hash = None
    await session.flush()
    await session.refresh(identity)
    return identity


async def detach_telegram(session: AsyncSession, identity_id: str) -> Identity | None:
    """Отвязывает Telegram от identity. Запрещено если это единственный канал."""
    identity = await get_identity_by_id(session, identity_id)
    if not identity:
        return None
    if identity.tg_id is None:
        return identity
    if identity.email is None:
        return None
    old_tg = int(identity.tg_id)
    identity.tg_id = None
    identity.is_admin = False
    await session.execute(update(User).where(User.identity_id == identity_id, User.tg_id == old_tg).values(tg_id=None))
    await session.flush()
    await session.refresh(identity)
    return identity


async def get_or_create_identity_for_tg(session: AsyncSession, tg_id: int) -> Identity:
    """Для tg_id возвращает существующую идентичность или создаёт новую и привязывает User."""
    is_admin = (await session.execute(select(Admin).where(Admin.tg_id == tg_id))).scalar_one_or_none() is not None
    identity = await get_identity_by_tg_id(session, tg_id)
    if identity:
        if is_admin and not identity.is_admin:
            identity.is_admin = True
            await session.flush()
            await session.refresh(identity)
        return identity
    identity = Identity(tg_id=tg_id, is_admin=is_admin)
    session.add(identity)
    await session.flush()
    await session.execute(User.__table__.update().where(User.tg_id == tg_id).values(identity_id=identity.id))
    await session.refresh(identity)
    return identity

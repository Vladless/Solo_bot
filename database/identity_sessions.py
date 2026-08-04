from datetime import datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Identity, IdentitySession
from settings.config import API_TOKEN_TTL_DAYS


_LAST_SEEN_TOUCH_SECONDS = 60


def _device_label_from_user_agent(user_agent: str | None) -> str:
    if not user_agent:
        return "Unknown device"
    ua = user_agent.lower()
    browser = "Browser"
    for needle, label in (
        ("edg/", "Edge"),
        ("opr/", "Opera"),
        ("yabrowser", "Yandex"),
        ("firefox", "Firefox"),
        ("chrome", "Chrome"),
        ("safari", "Safari"),
    ):
        if needle in ua:
            browser = label
            break
    platform = "Desktop"
    if "iphone" in ua or "ipad" in ua or "ios" in ua:
        platform = "iOS"
    elif "android" in ua:
        platform = "Android"
    elif "macintosh" in ua or "mac os" in ua:
        platform = "macOS"
    elif "windows" in ua:
        platform = "Windows"
    elif "linux" in ua:
        platform = "Linux"
    return f"{browser} · {platform}"[:128]


async def create_identity_session(
    session: AsyncSession,
    *,
    identity: Identity,
    token_hash: str,
    user_agent: str | None = None,
    ip: str | None = None,
    device_label: str | None = None,
) -> IdentitySession:
    """Создаёт/переиспользует сессию: один и тот же device (identity + user_agent) не плодит дубли."""
    now = datetime.utcnow()
    expires_at = now + timedelta(days=API_TOKEN_TTL_DAYS) if API_TOKEN_TTL_DAYS else None
    label = device_label or _device_label_from_user_agent(user_agent)

    if user_agent:
        existing = await session.scalar(
            select(IdentitySession)
            .where(
                IdentitySession.identity_id == identity.id,
                IdentitySession.user_agent == user_agent,
            )
            .order_by(IdentitySession.last_seen_at.desc())
            .limit(1)
        )
        if existing is not None:
            await session.execute(
                delete(IdentitySession).where(
                    IdentitySession.identity_id == identity.id,
                    IdentitySession.user_agent == user_agent,
                    IdentitySession.id != existing.id,
                )
            )
            existing.token_hash = token_hash
            existing.device_label = label
            existing.ip = ip
            existing.last_seen_at = now
            existing.expires_at = expires_at
            await session.flush()
            return existing

    obj = IdentitySession(
        identity_id=identity.id,
        token_hash=token_hash,
        device_label=label,
        user_agent=user_agent,
        ip=ip,
        created_at=now,
        last_seen_at=now,
        expires_at=expires_at,
    )
    session.add(obj)
    await session.flush()
    return obj


async def get_session_by_token_hash(session: AsyncSession, token_hash: str) -> IdentitySession | None:
    result = await session.execute(select(IdentitySession).where(IdentitySession.token_hash == token_hash))
    return result.scalar_one_or_none()


async def list_sessions_for_identity(session: AsyncSession, identity_id: str) -> list[IdentitySession]:
    now = datetime.utcnow()
    result = await session.execute(
        select(IdentitySession)
        .where(IdentitySession.identity_id == identity_id)
        .where((IdentitySession.expires_at.is_(None)) | (IdentitySession.expires_at > now))
        .order_by(IdentitySession.last_seen_at.desc())
    )
    return list(result.scalars().all())


async def delete_session_by_id(session: AsyncSession, *, session_id: str, identity_id: str) -> bool:
    """Удаляет сессию по id при условии, что она принадлежит identity_id."""
    result = await session.execute(
        delete(IdentitySession)
        .where(IdentitySession.id == session_id)
        .where(IdentitySession.identity_id == identity_id)
    )
    return (result.rowcount or 0) > 0


async def delete_session_by_token_hash(session: AsyncSession, token_hash: str) -> bool:
    result = await session.execute(delete(IdentitySession).where(IdentitySession.token_hash == token_hash))
    return (result.rowcount or 0) > 0


async def delete_other_sessions(session: AsyncSession, *, identity_id: str, keep_token_hash: str) -> int:
    """Удаляет все сессии identity кроме той, которая соответствует keep_token_hash."""
    result = await session.execute(
        delete(IdentitySession)
        .where(IdentitySession.identity_id == identity_id)
        .where(IdentitySession.token_hash != keep_token_hash)
    )
    return int(result.rowcount or 0)


async def touch_session_last_seen(session: AsyncSession, sess: IdentitySession) -> None:
    """Обновляет last_seen_at, но только если прошло ≥60 секунд — чтобы не писать в БД на каждый запрос."""
    now = datetime.utcnow()
    if (now - sess.last_seen_at).total_seconds() < _LAST_SEEN_TOUCH_SECONDS:
        return
    await session.execute(update(IdentitySession).where(IdentitySession.id == sess.id).values(last_seen_at=now))
    sess.last_seen_at = now


async def cleanup_expired_sessions(session: AsyncSession) -> int:
    """Удаляет все сессии с expires_at в прошлом. Возвращает количество удалённых."""
    now = datetime.utcnow()
    result = await session.execute(
        delete(IdentitySession).where(IdentitySession.expires_at.is_not(None)).where(IdentitySession.expires_at <= now)
    )
    return int(result.rowcount or 0)

from dataclasses import dataclass
from enum import Enum, StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Identity, User


class ActorSurface(StrEnum):
    TELEGRAM = "telegram"
    WEB = "web"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ResolvedActor:
    surface: ActorSurface
    billing_user_id: int | None
    telegram_chat_id: int | None
    identity_id: str | None


def telegram_chat_id(user: User | None) -> int | None:
    if user is None:
        return None
    return user.tg_id


async def resolve_user_optional(session: AsyncSession, legacy_id: int) -> User | None:
    r = await session.execute(select(User).where(User.tg_id == legacy_id))
    u = r.scalar_one_or_none()
    if u is not None:
        return u
    r2 = await session.execute(select(User).where(User.id == legacy_id))
    return r2.scalar_one_or_none()


async def invalidate_uid_cache(*legacy_ids: int | None) -> None:
    """Снимает кешированный маппинг legacy_ref → users.id."""
    from core.redis_cache import cache_delete, cache_key

    for ref in legacy_ids:
        if ref is None:
            continue
        await cache_delete(cache_key("uref", ref))
        await cache_delete(cache_key("user_exists", ref))


async def resolve_uid_cached(session: AsyncSession, legacy_id: int) -> int | None:
    """Кешированный резолв legacy_ref → users.id для горячих читателей.

    Маппинг практически иммутабелен (id не меняется, tg_id уникален), поэтому
    безопасно кешируется. Негатив не кешируем — юзер может появиться сразу после /start.
    """
    from core.redis_cache import cache_get, cache_key, cache_set
    from settings.cache_config import UREF_CACHE_TTL_SEC

    cached = await cache_get(cache_key("uref", legacy_id))
    if cached is not None:
        try:
            return int(cached)
        except (TypeError, ValueError):
            pass
    u = await resolve_user_optional(session, legacy_id)
    if u is None:
        return None
    uid = int(u.id)
    await cache_set(cache_key("uref", legacy_id), uid, UREF_CACHE_TTL_SEC)
    return uid


async def panel_identity_fields(session: AsyncSession, legacy_ref: int) -> tuple[int | None, str | None]:
    """Поля владельца ключа для внешней панели: (telegram_id, email).

    Источник истины — identity. Три случая:
    - только сайт: (None, email);
    - только Telegram: (tg, None);
    - связанные аккаунты: (tg, email).
    """
    user = await resolve_user_optional(session, legacy_ref)
    if user is None:
        return None, None
    identity = None
    if user.identity_id:
        from database.identities import get_identity_by_id

        identity = await get_identity_by_id(session, user.identity_id)
    src_tg = identity.tg_id if identity is not None else user.tg_id
    tg = int(src_tg) if src_tg is not None and int(src_tg) > 0 else None
    email = identity.email if identity is not None else None
    return tg, email


async def notify_telegram_chat_id(session: AsyncSession, legacy_ref: int) -> int | None:
    payer = await resolve_user_optional(session, legacy_ref)
    tg = telegram_chat_id(payer)
    if tg is not None:
        return tg
    if payer is None:
        return legacy_ref
    return None


async def resolve_actor_from_legacy_ref(session: AsyncSession, legacy_ref: int) -> ResolvedActor:
    user = await resolve_user_optional(session, legacy_ref)
    if user is None:
        return ResolvedActor(
            surface=ActorSurface.UNKNOWN,
            billing_user_id=None,
            telegram_chat_id=legacy_ref,
            identity_id=None,
        )

    user_tg = telegram_chat_id(user)
    if user_tg is not None and int(user_tg) == int(legacy_ref):
        surface = ActorSurface.TELEGRAM
    elif int(user.id) == int(legacy_ref):
        surface = ActorSurface.WEB
    elif user_tg is None:
        surface = ActorSurface.WEB
    else:
        surface = ActorSurface.UNKNOWN

    return ResolvedActor(
        surface=surface,
        billing_user_id=int(user.id),
        telegram_chat_id=user_tg,
        identity_id=user.identity_id,
    )


async def resolve_actor_from_identity(session: AsyncSession, identity: Identity) -> ResolvedActor:
    from database.identities import ensure_billing_user_for_identity

    billing_uid = await ensure_billing_user_for_identity(session, identity)
    user = await resolve_user_optional(session, billing_uid)
    return ResolvedActor(
        surface=ActorSurface.WEB,
        billing_user_id=billing_uid,
        telegram_chat_id=telegram_chat_id(user),
        identity_id=identity.id,
    )

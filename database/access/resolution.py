from dataclasses import dataclass
from enum import Enum, StrEnum

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.redis_cache import cache_delete, cache_get, cache_key, cache_set
from database.cache_purge import is_purge_pending
from database.models import Identity, User
from settings.cache_config import UREF_CACHE_TTL_SEC


class UserId(int):
    """Ссылка на клиента строго по users.id."""


class TelegramId(int):
    """Ссылка на клиента строго по users.tg_id."""


class AmbiguousUserRef(ValueError):
    """Ошибка старой ссылки, соответствующей двум разным клиентам."""


def parse_user_ref(value: str) -> int:
    if value.startswith("u"):
        return UserId(value[1:])
    if value.startswith("t"):
        return TelegramId(value[1:])
    return int(value)


async def resolve_admin_user_ref(session: AsyncSession, ref: int) -> int | None:
    if not isinstance(ref, UserId | TelegramId):
        by_id = await get_user_by_id(session, ref)
        by_tg = await get_user_by_tg_id(session, ref)
        if by_id is not None and by_tg is not None and by_id.id != by_tg.id:
            raise AmbiguousUserRef(str(ref))
        user = by_tg if by_tg is not None else by_id
    else:
        user = await resolve_user_optional(session, ref)
    return UserId(user.id) if user is not None else None


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

    def __post_init__(self) -> None:
        if self.billing_user_id is not None:
            object.__setattr__(self, "billing_user_id", UserId(self.billing_user_id))


def public_tg_id(value: int | None) -> int | None:
    """Возвращает только положительный Telegram ID."""
    if value is None:
        return None
    tg = int(value)
    return tg if tg > 0 else None


def telegram_chat_id(user: User | None) -> int | None:
    """Адрес чата в Telegram; у синтетического tg_id чата нет."""
    return public_tg_id(None if user is None else user.tg_id)


async def user_id_from_legacy_ref(session: AsyncSession, ref: int) -> int | None:
    """Возвращает users.id для заданной ссылки на клиента."""
    user = await resolve_user_optional(session, ref)
    return UserId(user.id) if user is not None else None


async def chat_id_for_user(session: AsyncSession, user_id: int) -> int | None:
    """Адрес чата клиента; None у клиента без Telegram."""
    tg = await session.scalar(select(User.tg_id).where(User.id == user_id))
    return None if tg is None or int(tg) <= 0 else int(tg)


async def get_user_by_id(session: AsyncSession, user_id: int) -> User | None:
    result = await session.execute(select(User).where(User.id == int(user_id)))
    return result.scalar_one_or_none()


async def get_user_by_tg_id(session: AsyncSession, tg_id: int) -> User | None:
    result = await session.execute(select(User).where(User.tg_id == int(tg_id)))
    return result.scalar_one_or_none()


async def resolve_user_optional(session: AsyncSession, legacy_id: int) -> User | None:
    if isinstance(legacy_id, UserId):
        return await get_user_by_id(session, legacy_id)
    if isinstance(legacy_id, TelegramId):
        return await get_user_by_tg_id(session, legacy_id)
    by_tg = await get_user_by_tg_id(session, legacy_id)
    return by_tg if by_tg is not None else await get_user_by_id(session, legacy_id)


def user_ref_cache_key(prefix: str, ref: int) -> str:
    namespace = "id" if isinstance(ref, UserId) else "tg" if isinstance(ref, TelegramId) else "legacy"
    return cache_key(prefix, namespace, int(ref))


def user_ref_cache_keys(*refs: int | None) -> list[str]:
    return [
        key
        for ref in refs
        if ref is not None
        for prefix in ("uref", "user_exists", "pref_ccy")
        for key in (
            cache_key(prefix, ref),
            *(user_ref_cache_key(prefix, typed) for typed in (UserId(ref), TelegramId(ref), int(ref))),
        )
    ]


async def invalidate_uid_cache(*legacy_ids: int | None) -> None:
    """Снимает кешированный маппинг legacy_ref → users.id."""
    for key in user_ref_cache_keys(*legacy_ids):
        await cache_delete(key)


async def resolve_uid_cached(session: AsyncSession, legacy_id: int) -> int | None:
    """Разрешает ссылку на клиента с проверкой кешированного владельца."""
    cache_ref = user_ref_cache_key("uref", legacy_id)
    pending = is_purge_pending(session, cache_ref)
    cached = None if pending else await cache_get(cache_ref)
    if cached is not None:
        try:
            cached_uid = UserId(cached)
        except (TypeError, ValueError):
            pass
        else:
            current = await resolve_user_optional(session, legacy_id)
            current_uid = UserId(current.id) if current is not None else None
            if cached_uid != current_uid:
                await cache_delete(cache_ref)
            return current_uid
    u = await resolve_user_optional(session, legacy_id)
    if u is None:
        return None
    uid = UserId(u.id)
    if not pending:
        await cache_set(cache_ref, uid, UREF_CACHE_TTL_SEC)
        current = await resolve_user_optional(session, legacy_id)
        current_uid = UserId(current.id) if current is not None else None
        if current_uid != uid:
            await cache_delete(cache_ref)
            return current_uid
    return uid


async def ensure_legacy_tg_ref(session: AsyncSession, user_id: int) -> int:
    """Создаёт совместимый Telegram-адрес клиента при его отсутствии."""
    current = await session.scalar(select(User.tg_id).where(User.id == int(user_id)))
    if current is not None:
        return int(current)
    synthetic = -int(user_id)
    result = await session.execute(
        update(User).where(User.id == int(user_id), User.tg_id.is_(None)).values(tg_id=synthetic).returning(User.tg_id)
    )
    assigned = result.scalar_one_or_none()
    if assigned is not None:
        return int(assigned)
    current = await session.scalar(select(User.tg_id).where(User.id == int(user_id)))
    if current is None:
        raise ValueError("Billing user disappeared while creating a compatibility reference")
    return int(current)


async def subscription_owner_ref(session: AsyncSession, legacy_ref: int) -> int:
    """Возвращает числовой адрес владельца для ссылки на подписку."""
    user = await resolve_user_optional(session, legacy_ref)
    if user is not None and user.tg_id is not None:
        return int(user.tg_id)
    return int(legacy_ref)


async def panel_identity_fields(session: AsyncSession, legacy_ref: int) -> tuple[int | None, str | None]:
    """Возвращает Telegram ID и email владельца для внешней панели."""
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
    if payer is not None:
        return await chat_id_for_user(session, payer.id)
    return None if isinstance(legacy_ref, UserId) else public_tg_id(legacy_ref)


async def resolve_actor_from_legacy_ref(session: AsyncSession, legacy_ref: int) -> ResolvedActor:
    user = await resolve_user_optional(session, legacy_ref)
    if user is None:
        return ResolvedActor(
            surface=ActorSurface.UNKNOWN,
            billing_user_id=None,
            telegram_chat_id=None if isinstance(legacy_ref, UserId) else public_tg_id(legacy_ref),
            identity_id=None,
        )

    user_tg = telegram_chat_id(user)
    if isinstance(legacy_ref, UserId):
        surface = ActorSurface.WEB
    elif isinstance(legacy_ref, TelegramId):
        surface = ActorSurface.TELEGRAM
    elif user_tg is not None and int(user_tg) == int(legacy_ref):
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
    return ResolvedActor(
        surface=ActorSurface.WEB,
        billing_user_id=billing_uid,
        telegram_chat_id=await chat_id_for_user(session, billing_uid),
        identity_id=identity.id,
    )

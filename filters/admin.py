import time

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from config import ADMIN_ID
from database.db import async_session_maker
from database.models import Admin

from .permissions import normalize_permissions


_ADMIN_CACHE: dict[int, tuple[float, bool, bool, frozenset[str]]] = {}
_ADMIN_CACHE_TTL = 60


def _get_cached_admin(user_id: int) -> tuple[bool, bool, frozenset[str]] | None:
    now = time.time()
    entry = _ADMIN_CACHE.get(user_id)
    if entry and entry[0] > now:
        return entry[1], entry[2], entry[3]
    return None


def _set_cached_admin(user_id: int, is_admin: bool, is_superadmin: bool, permissions: frozenset[str]) -> None:
    _ADMIN_CACHE[user_id] = (time.time() + _ADMIN_CACHE_TTL, is_admin, is_superadmin, permissions)


def invalidate_admin_cache(user_id: int | None = None) -> None:
    if user_id is None:
        _ADMIN_CACHE.clear()
    else:
        _ADMIN_CACHE.pop(user_id, None)


async def _resolve_admin(user_id: int) -> tuple[bool, bool, frozenset[str]]:
    cached = _get_cached_admin(user_id)
    if cached is not None:
        return cached

    try:
        async with async_session_maker() as session:
            admin = (await session.execute(select(Admin).where(Admin.tg_id == user_id))).scalar_one_or_none()
            admin_ids = (ADMIN_ID,) if isinstance(ADMIN_ID, int) else ADMIN_ID
            admin_role = (getattr(admin, "role", None) or "").strip().lower() if admin else ""
            if user_id in admin_ids:
                is_admin = True
                is_super = True
                perms = frozenset()
            elif admin is not None and admin_role == "designer":
                is_admin = False
                is_super = False
                perms = frozenset()
            elif admin is not None:
                is_admin = True
                is_super = admin_role != "moderator"
                perms = frozenset(normalize_permissions(admin.permissions))
            else:
                is_admin = False
                is_super = False
                perms = frozenset()
            _set_cached_admin(user_id, is_admin, is_super, perms)
            await session.commit()
            return is_admin, is_super, perms
    except Exception:
        return False, False, frozenset()


class IsAdminFilter(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        if not event.from_user:
            return False
        is_admin, _, _ = await _resolve_admin(event.from_user.id)
        return is_admin


class IsSuperAdminFilter(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        if not event.from_user:
            return False
        _, is_super, _ = await _resolve_admin(event.from_user.id)
        return is_super


class HasPermission(BaseFilter):
    def __init__(self, *permissions: str, require_all: bool = False) -> None:
        if not permissions:
            raise ValueError("HasPermission requires at least one permission id")
        self.permissions = tuple(permissions)
        self.require_all = require_all

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        if not event.from_user:
            return False
        is_admin, is_super, perms = await _resolve_admin(event.from_user.id)
        if not is_admin:
            return False
        if is_super:
            return True
        if self.require_all:
            return all(p in perms for p in self.permissions)
        return any(p in perms for p in self.permissions)


async def get_admin_context(user_id: int) -> tuple[bool, bool, frozenset[str]]:
    return await _resolve_admin(user_id)

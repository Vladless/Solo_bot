import time

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from config import ADMIN_ID
from database.db import async_session_maker
from database.models import Admin


_ADMIN_CACHE: dict[int, tuple[float, bool, bool]] = {}
_ADMIN_CACHE_TTL = 60


def _get_cached_admin(user_id: int) -> tuple[bool, bool] | None:
    now = time.time()
    entry = _ADMIN_CACHE.get(user_id)
    if entry and entry[0] > now:
        return entry[1], entry[2]
    return None


def _set_cached_admin(user_id: int, is_admin: bool, is_superadmin: bool) -> None:
    _ADMIN_CACHE[user_id] = (time.time() + _ADMIN_CACHE_TTL, is_admin, is_superadmin)


class IsAdminFilter(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        if not event.from_user:
            return False

        user_id = event.from_user.id
        cached = _get_cached_admin(user_id)
        if cached is not None:
            return cached[0]

        try:
            async with async_session_maker() as session:
                admin = (await session.execute(select(Admin).where(Admin.tg_id == user_id))).scalar_one_or_none()
                admin_ids = (ADMIN_ID,) if isinstance(ADMIN_ID, int) else ADMIN_ID
                is_admin = admin is not None or user_id in admin_ids
                is_super = admin.role != "moderator" if admin else (user_id in admin_ids)
                _set_cached_admin(user_id, is_admin, is_super)
                return is_admin
        except (Exception,):
            return False


class IsSuperAdminFilter(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        if not event.from_user:
            return False

        user_id = event.from_user.id
        cached = _get_cached_admin(user_id)
        if cached is not None:
            return cached[1]

        try:
            async with async_session_maker() as session:
                admin = (await session.execute(select(Admin).where(Admin.tg_id == user_id))).scalar_one_or_none()
                if not admin:
                    _set_cached_admin(user_id, False, False)
                    return False
                is_super = admin.role != "moderator"
                _set_cached_admin(user_id, True, is_super)
                return is_super
        except (Exception,):
            return False

from . import admin, user
from ._common import router, stats_router, user_router
from .admin_subs import subs_router


__all__ = ["router", "stats_router", "subs_router", "user_router"]

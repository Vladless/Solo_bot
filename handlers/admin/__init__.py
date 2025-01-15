__all__ = ("router",)

from aiogram import Router

from .admin_backups import router as backups_router
from .admin_bans import router as bans_router
from .admin_coupons import router as coupons_router
from .admin_panel import router as panel_router
from .admin_restart import router as restart_router
from .admin_sender import router as sender_router
from .admin_servers import router as servers_router
from .admin_stats import router as stats_router
from .admin_users import router as users_router

router = Router(name="admins_main_router")

router.include_routers(
    panel_router,
    servers_router,
    users_router,
    stats_router,
    backups_router,
    sender_router,
    coupons_router,
    restart_router,
    bans_router,
)

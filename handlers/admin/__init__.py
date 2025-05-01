__all__ = ("router",)

from aiogram import Router

from .ads import router as ads_router
from .backups import router as backups_router
from .bans import router as bans_router
from .clusters import router as clusters_router
from .coupons import router as coupons_router
from .management import router as management_router
from .panel import router as panel_router
from .restart import router as restart_router
from .sender import router as sender_router
from .servers import router as servers_router
from .stats import router as stats_router
from .users import router as users_router


router = Router(name="admins_main_router")

router.include_routers(
    panel_router,
    management_router,
    servers_router,
    clusters_router,
    users_router,
    stats_router,
    backups_router,
    sender_router,
    coupons_router,
    restart_router,
    bans_router,
    ads_router,
)

__all__ = ('router',)

from aiogram import Router

from .admin_coupons import router as coupons_router
from .admin_panel import router as panel_router
from .admin_servers import router as servers_router
from .admin_user_editor import router as user_editor_router

router = Router(name=__name__)

router.include_routers(
    panel_router,
    servers_router,
    coupons_router,
    user_editor_router,
)

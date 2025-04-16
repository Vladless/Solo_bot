__all__ = ("router",)

from aiogram import Router

from .keys import router as keys_router
from .key_view import router as view_router
from .key_renew import router as renew_router
from .key_freeze import router as freeze_router
from .key_connect import router as connect_router
from .key_mode import router as key_mode_router

router = Router(name="keys_main_router")

router.include_routers(
    keys_router,
    view_router,
    renew_router,
    freeze_router,
    connect_router,
    key_mode_router
)

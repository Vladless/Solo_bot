__all__ = ('router',)

from aiogram import Router

from .admin import router as admin_router
from .common import router as common_router
from .instructions import router as instructions_router
from .keys import router as keys_router

router = Router(name=__name__)

router.include_routers(
    common_router,
    admin_router,
    instructions_router,
    keys_router,
)

__all__ = ('router',)

from aiogram import Router

from .key_management import router as key_management_router
from .keys import router as keys_router

router = Router(name=__name__)

router.include_routers(
    keys_router,
    key_management_router,
)

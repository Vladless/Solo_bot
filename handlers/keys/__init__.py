__all__ = ("router",)

from aiogram import Router

from .key_management import router as management_router
from .keys import router as keys_router


router = Router(name="keys_main_router")

router.include_routers(
    keys_router,
    management_router,
)

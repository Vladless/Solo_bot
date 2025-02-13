__all__ = ("router",)

from aiogram import Router

from .general_notifications import router as general_notifications_router
from .special_notifications import router as special_notifications_router

router = Router(name="notifications_main_router")

router.include_routers(general_notifications_router, special_notifications_router)

__all__ = ("router",)

from aiogram import Router

from .addons.key_addons import router as addons_router


router = Router(name="keys_main_router")

router.include_routers(addons_router)

__all__ = ("router",)

from aiogram import Router

from .instructions import router as instructions_router


router = Router(name="instructions_main_router")

router.include_routers(
    instructions_router,
)

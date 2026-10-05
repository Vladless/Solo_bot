from aiogram import Router

from .poll_handler import router as _poll_router
from .sender_handler import router as _sender_router


router = Router(name="admin_sender_pkg")
router.include_router(_sender_router)
router.include_router(_poll_router)


__all__ = ["router"]

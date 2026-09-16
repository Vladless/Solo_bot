__all__ = (
    "router",
    "_task_lifecycle",
)

from aiogram import Router

from core.tasks import lifecycle as _task_lifecycle


router = Router(name="notifications_main_router")

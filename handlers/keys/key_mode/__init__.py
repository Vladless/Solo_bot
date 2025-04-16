__all__ = ("router",)

from aiogram import Router

from .key_cluster_mode import router as cluster_router
from .key_country_mode import router as country_router
from .key_create import router as create_router


router = Router(name="key_mode_router")

router.include_routers(
    create_router,
    cluster_router,
    country_router,
)

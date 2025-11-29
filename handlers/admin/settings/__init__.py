from aiogram import Router

from .settings_buttons import router as settings_buttons_router
from .settings_cashboxes import router as settings_cashboxes_router
from .settings_manage import router as settings_manage_router
from .settings_modes import router as settings_modes_router
from .settings_money import router as settings_panels_router
from .settings_notifications import router as settings_notifications_router
from .settings_tariffs import router as settings_tariffs_router


router = Router(name="admin_settings")
router.include_router(settings_manage_router)
router.include_router(settings_buttons_router)
router.include_router(settings_cashboxes_router)
router.include_router(settings_panels_router)
router.include_router(settings_notifications_router)
router.include_router(settings_modes_router)
router.include_router(settings_tariffs_router)

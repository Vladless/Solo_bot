from aiogram import Router

from filters.admin import HasPermission
from filters.permissions import PERM_SETTINGS

from .settings_buttons import router as settings_buttons_router
from .settings_cashboxes import router as settings_cashboxes_router
from .settings_email import router as settings_email_router
from .settings_manage import router as settings_manage_router
from .settings_menu_layout import router as settings_menu_layout_router
from .settings_modes import router as settings_modes_router
from .settings_money import router as settings_panels_router
from .settings_notifications import router as settings_notifications_router
from .settings_remnawave import router as settings_remnawave_router
from .settings_tariffs import router as settings_tariffs_router
from .settings_web import router as settings_web_router


router = Router(name="admin_settings")
router.callback_query.filter(HasPermission(PERM_SETTINGS))
router.message.filter(HasPermission(PERM_SETTINGS))
router.include_router(settings_manage_router)
router.include_router(settings_buttons_router)
router.include_router(settings_menu_layout_router)
router.include_router(settings_cashboxes_router)
router.include_router(settings_panels_router)
router.include_router(settings_notifications_router)
router.include_router(settings_modes_router)
router.include_router(settings_tariffs_router)
router.include_router(settings_web_router)
router.include_router(settings_email_router)
router.include_router(settings_remnawave_router)

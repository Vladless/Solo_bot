from aiogram import Router

from filters.admin import HasPermission
from filters.permissions import PERM_ADMINS, PERM_MANAGEMENT


router = Router()
router.callback_query.filter(HasPermission(PERM_MANAGEMENT, PERM_ADMINS))
router.message.filter(HasPermission(PERM_MANAGEMENT, PERM_ADMINS))

from . import admins, database, domain, file_upload, import_3xui, import_remnawave, maintenance, update_handler


__all__ = (
    "router",
    "admins",
    "database",
    "domain",
    "file_upload",
    "import_3xui",
    "import_remnawave",
    "maintenance",
    "update_handler",
)

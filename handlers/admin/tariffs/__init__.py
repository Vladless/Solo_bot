from aiogram import Router

from filters.admin import HasPermission
from filters.permissions import PERM_TARIFFS


router = Router()
router.callback_query.filter(HasPermission(PERM_TARIFFS))
router.message.filter(HasPermission(PERM_TARIFFS))

from . import tariff_configurator, tariff_manage, tariff_sorting, tariff_subgroups


__all__ = (
    "router",
    "tariff_configurator",
    "tariff_manage",
    "tariff_sorting",
    "tariff_subgroups",
)

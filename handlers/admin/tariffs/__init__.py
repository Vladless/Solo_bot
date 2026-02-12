from aiogram import Router


router = Router()

from . import (  # noqa: F401
    tariff_configurator,
    tariff_manage,
    tariff_sorting,
    tariff_subgroups,
)

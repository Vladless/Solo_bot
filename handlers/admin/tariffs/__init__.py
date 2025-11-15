from aiogram import Router


router = Router()

from . import (
    tariff_manage,  # noqa: F401
    tariff_sorting,  # noqa: F401
    tariff_subgroups,  # noqa: F401
)

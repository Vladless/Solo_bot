from aiogram import Router
from aiogram.types import CallbackQuery

from core.settings.tariffs_config import TARIFFS_CONFIG

from . import config_mode, pack_mode


def is_pack_mode_enabled() -> bool:
    return bool(TARIFFS_CONFIG.get("KEY_ADDONS_PACK_MODE"))


def _main_mode_filter(callback: CallbackQuery, *args, **kwargs) -> bool:
    return not is_pack_mode_enabled()


def _pack_mode_filter(callback: CallbackQuery, *args, **kwargs) -> bool:
    return is_pack_mode_enabled()


config_mode.router.callback_query.filter(_main_mode_filter)
pack_mode.router.callback_query.filter(_pack_mode_filter)

router = Router()
router.include_router(config_mode.router)
router.include_router(pack_mode.router)

from aiogram import Router
from aiogram.types import CallbackQuery

from core.settings.tariffs_config import TARIFFS_CONFIG

from . import key_addons_main, key_addons_pack
from .utils import (
    UNLIMITED_DEVICES_LABEL,
    UNLIMITED_TRAFFIC_LABEL,
    KeyAddonConfigState,
    build_addons_screen_text,
    format_devices_label,
    format_traffic_label,
    is_not_downgrade,
)


def is_pack_mode_enabled() -> bool:
    return bool(TARIFFS_CONFIG.get("KEY_ADDONS_PACK_MODE"))


def _main_mode_filter(callback: CallbackQuery, *args, **kwargs) -> bool:
    return not is_pack_mode_enabled()


def _pack_mode_filter(callback: CallbackQuery, *args, **kwargs) -> bool:
    return is_pack_mode_enabled()


key_addons_main.router.callback_query.filter(_main_mode_filter)
key_addons_pack.router.callback_query.filter(_pack_mode_filter)

router = Router()
router.include_router(key_addons_main.router)
router.include_router(key_addons_pack.router)

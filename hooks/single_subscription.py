from typing import Any

from aiogram.types import InlineKeyboardButton

from core.bootstrap import MODES_CONFIG
from logger import logger
from settings.buttons import MAIN_MENU


def is_single_sub_enabled() -> bool:
    return bool(MODES_CONFIG.get("SINGLE_SUBSCRIPTION_MODE", False))


def single_sub_back_to_profile() -> list[dict]:
    return [
        {"remove_prefix": "view_key|"},
        {"remove": ["view_keys", "profile"]},
        {"button": InlineKeyboardButton(text=MAIN_MENU, callback_data="profile")},
    ]


async def open_single_sub_profile(target_message: Any, session: Any, admin: bool = False) -> bool:
    if target_message is None:
        return False
    try:
        from handlers.profile import process_callback_view_profile

        await process_callback_view_profile(target_message, None, admin=admin, session=session)
        return True
    except Exception as e:
        logger.error(f"[single_sub] Ошибка открытия профиля: {e}")
        return False

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from core.bootstrap import MODES_CONFIG


def webapp_only_markup() -> InlineKeyboardMarkup | None:
    """Кнопка кабинета вместо инлайн-кнопок: None, если веб-режим выключен или сайт недоступен."""
    if not MODES_CONFIG.get("WEBAPP_ONLY_MODE", False):
        return None

    from core.settings.web_config import get_site_url, is_web_enabled, is_web_open_in_browser
    from settings.buttons import WEB_CABINET

    if not is_web_enabled():
        return None
    site_url = get_site_url()
    if not site_url:
        return None

    if is_web_open_in_browser():
        button = InlineKeyboardButton(text=WEB_CABINET, url=f"{site_url}/dashboard")
    else:
        button = InlineKeyboardButton(text=WEB_CABINET, web_app=WebAppInfo(url=f"{site_url}/dashboard?webapp=1"))
    return InlineKeyboardMarkup(inline_keyboard=[[button]])

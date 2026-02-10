from __future__ import annotations

import aiogram.types

_OriginalInlineKeyboardButton = aiogram.types.InlineKeyboardButton

_button_icon_config: dict[str, dict[str, str]] = {}


def apply_button_icons_patch(config: dict[str, dict[str, str]] | None = None) -> None:
    """
    Патчит InlineKeyboardButton, добавляя поддержку глобального конфига для иконок и стилей кнопок по callback_data или url.
    """
    if config is not None:
        _button_icon_config.clear()
        _button_icon_config.update(config)

    class _PatchedInlineKeyboardButton(_OriginalInlineKeyboardButton):
        def __init__(self, **kwargs: object):
            key = kwargs.get("callback_data") or kwargs.get("url")
            if key is not None and isinstance(key, str) and key in _button_icon_config:
                kwargs = {**kwargs, **_button_icon_config[key]}
            super().__init__(**kwargs)

    aiogram.types.InlineKeyboardButton = _PatchedInlineKeyboardButton


def set_button_icon_config(config: dict[str, dict[str, str]]) -> None:
    """Подставить конфиг кнопок (вызвать после загрузки handlers, из handlers.buttons.BUTTON_ICON_CONFIG)."""
    _button_icon_config.clear()
    _button_icon_config.update(config)


def inline_button(
    text: str,
    callback_data: str | None = None,
    url: str | None = None,
    web_app: object | None = None,
    *,
    icon_custom_emoji_id: str | None = None,
    style: str | None = None,
) -> _OriginalInlineKeyboardButton:
    """Собирает InlineKeyboardButton с опциональной иконкой (custom emoji) и стилем."""
    kwargs: dict = {"text": text}
    if callback_data is not None:
        kwargs["callback_data"] = callback_data
    if url is not None:
        kwargs["url"] = url
    if web_app is not None:
        kwargs["web_app"] = web_app
    if icon_custom_emoji_id is not None:
        kwargs["icon_custom_emoji_id"] = icon_custom_emoji_id
    if style is not None:
        kwargs["style"] = style
    return aiogram.types.InlineKeyboardButton(**kwargs)

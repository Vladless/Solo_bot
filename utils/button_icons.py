from __future__ import annotations

import aiogram.types

from pydantic import ConfigDict


_UniqueGiftColors = getattr(aiogram.types, "UniqueGiftColors", None)
if _UniqueGiftColors is not None:
    _cfg = getattr(_UniqueGiftColors, "model_config", None)
    _base = dict(_cfg) if _cfg is not None else {}
    _base.pop("protected_namespaces", None)
    _UniqueGiftColors.model_config = ConfigDict(**_base, protected_namespaces=())

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
        def __init__(self, **kwargs: object) -> None:
            key = kwargs.get("callback_data") or kwargs.get("url")
            if key is None:
                web_app = kwargs.get("web_app")
                key = getattr(web_app, "url", None)
                if key is None and isinstance(web_app, dict):
                    key = web_app.get("url")
            if key is not None and isinstance(key, str):
                config = _button_icon_config.get(key)
                if config is None and "|" in key:
                    config = _button_icon_config.get(key.split("|", 1)[0])
                if config is None:
                    for cfg_key, cfg_val in _button_icon_config.items():
                        if cfg_key.startswith("https://") and key.startswith(cfg_key):
                            config = cfg_val
                            break
                if config is not None:
                    kwargs = {**kwargs, **config}
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

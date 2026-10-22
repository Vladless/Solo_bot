from __future__ import annotations

import re

from functools import wraps
from html.parser import HTMLParser
from typing import Any

from aiogram import Bot
from aiogram.client.default import Default
from aiogram.enums import MessageEntityType, ParseMode
from aiogram.types import MessageEntity

from logger import logger


_PLACEHOLDER_CACHE: dict[str, str] = {}
_BOT: Bot | None = None
_MARKER_RE = re.compile(r"\{\{emoji:([0-9]+)\}\}|\{emoji:([0-9]+)\}|\[emoji:([0-9]+)\]")
_HTML_ENTITY_TYPES = {
    "b": MessageEntityType.BOLD,
    "strong": MessageEntityType.BOLD,
    "i": MessageEntityType.ITALIC,
    "em": MessageEntityType.ITALIC,
    "u": MessageEntityType.UNDERLINE,
    "ins": MessageEntityType.UNDERLINE,
    "s": MessageEntityType.STRIKETHROUGH,
    "strike": MessageEntityType.STRIKETHROUGH,
    "del": MessageEntityType.STRIKETHROUGH,
    "code": MessageEntityType.CODE,
    "pre": MessageEntityType.PRE,
    "blockquote": MessageEntityType.BLOCKQUOTE,
    "tg-spoiler": MessageEntityType.SPOILER,
}


def _utf16_len(text: str) -> int:
    """Возвращает длину строки в кодовых единицах UTF-16."""
    return len(text.encode("utf-16-le")) // 2


class _TelegramHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.entities: list[MessageEntity] = []
        self.stack: list[dict[str, Any]] = []
        self.utf16_offset = 0

    def handle_data(self, data: str) -> None:
        self.parts.append(data)
        self.utf16_offset += _utf16_len(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        entity_type = _HTML_ENTITY_TYPES.get(tag)
        extra: dict[str, Any] = {}
        if tag == "a" and attributes.get("href"):
            entity_type = MessageEntityType.TEXT_LINK
            extra["url"] = attributes["href"]
        elif tag == "span" and attributes.get("class") == "tg-spoiler":
            entity_type = MessageEntityType.SPOILER
        elif tag == "tg-emoji" and attributes.get("emoji-id"):
            entity_type = MessageEntityType.CUSTOM_EMOJI
            extra["custom_emoji_id"] = attributes["emoji-id"]
        elif tag == "blockquote" and "expandable" in attributes:
            entity_type = MessageEntityType.EXPANDABLE_BLOCKQUOTE
        elif tag == "code":
            pre = next((entry for entry in reversed(self.stack) if entry["tag"] == "pre"), None)
            if pre is not None:
                entity_type = None
                language = attributes.get("class") or ""
                if language.startswith("language-"):
                    pre["extra"]["language"] = language.removeprefix("language-")
        self.stack.append({"tag": tag, "type": entity_type, "offset": self.utf16_offset, "extra": extra})

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                entry = self.stack.pop(index)
                length = self.utf16_offset - entry["offset"]
                if entry["type"] is not None and length:
                    self.entities.append(
                        MessageEntity(type=entry["type"], offset=entry["offset"], length=length, **entry["extra"])
                    )
                return


async def _fetch_placeholder(emoji_id: str, bot: Bot | None = None) -> str:
    """Находит обычный эмодзи для отображения вместо пользовательского."""
    if emoji_id in _PLACEHOLDER_CACHE:
        return _PLACEHOLDER_CACHE[emoji_id]
    active_bot = bot if bot is not None else _BOT
    if active_bot is None:
        return "😀"
    try:
        stickers = await active_bot.get_custom_emoji_stickers(custom_emoji_ids=[emoji_id])
        if stickers:
            placeholder = getattr(stickers[0], "emoji", None) or getattr(stickers[0], "alt", None)
            if placeholder:
                _PLACEHOLDER_CACHE[emoji_id] = placeholder
                return placeholder
    except Exception as error:
        logger.debug("[CustomEmojis] Не удалось получить обычный эмодзи: {}", error)
    return "😀"


async def _replace_markers(
    text: str, entities: list[MessageEntity] | None = None, *, bot: Bot | None = None
) -> tuple[str, list[MessageEntity]]:
    """Заменяет маркеры и пересчитывает смещения всей разметки."""
    protected = [
        (entity.offset, entity.offset + entity.length)
        for entity in entities or []
        if entity.type in {MessageEntityType.CODE, MessageEntityType.PRE}
    ]
    replacements = []
    for match in _MARKER_RE.finditer(text):
        start = _utf16_len(text[: match.start()])
        end = start + _utf16_len(match.group())
        if any(start < right and end > left for left, right in protected):
            continue
        emoji_id = next(value for value in match.groups() if value is not None)
        placeholder = await _fetch_placeholder(emoji_id, bot=bot)
        replacements.append((match.start(), match.end(), start, end, emoji_id, placeholder))
    if not replacements:
        return text, []

    def remap(position: int, *, end: bool = False) -> int:
        delta = 0
        for _, _, left, right, _, replacement in replacements:
            if position <= left:
                break
            length = _utf16_len(replacement)
            if position < right:
                return left + delta + (length if end else 0)
            delta += length - (right - left)
        return position + delta

    parts = []
    position = 0
    merged = []
    for left, right, start, _, emoji_id, placeholder in replacements:
        parts.extend((text[position:left], placeholder))
        position = right
        merged.append(
            MessageEntity(
                type=MessageEntityType.CUSTOM_EMOJI,
                offset=remap(start),
                length=_utf16_len(placeholder),
                custom_emoji_id=emoji_id,
            )
        )
    parts.append(text[position:])
    for entity in entities or []:
        start = remap(entity.offset)
        length = remap(entity.offset + entity.length, end=True) - start
        if length:
            merged.append(entity.model_copy(update={"offset": start, "length": length}))
    return "".join(parts), sorted(merged, key=lambda entity: entity.offset)


async def _process_text(
    text: str,
    entities: list[MessageEntity] | None = None,
    *,
    parse_mode: str | None = ParseMode.HTML,
    bot: Bot | None = None,
) -> tuple[str, list[MessageEntity] | None]:
    """Обрабатывает маркеры с учётом исходного формата сообщения."""
    if not text or not _MARKER_RE.search(text):
        return text, entities
    mode = parse_mode.value if isinstance(parse_mode, ParseMode) else parse_mode
    if entities is None and mode == ParseMode.HTML:
        parser = _TelegramHTMLParser()
        parser.feed(text)
        parser.close()
        processed, merged = await _replace_markers("".join(parser.parts), parser.entities, bot=bot)
        return (processed, merged) if merged else (text, entities)
    return_value = await _replace_markers(text, entities, bot=bot)
    return return_value if return_value[1] else (text, entities)


async def _prepare_outgoing(value: Any, bot: Bot) -> Any:
    """Готовит текст и подписи, сохраняя исходные объекты запросов."""
    updates = {}
    fields = type(value).model_fields
    for text_field, entities_field in (("text", "entities"), ("caption", "caption_entities")):
        if text_field not in fields or entities_field not in fields:
            continue
        text = getattr(value, text_field, None)
        if not isinstance(text, str) or not _MARKER_RE.search(text):
            continue
        parse_mode = getattr(value, "parse_mode", None)
        if isinstance(parse_mode, Default):
            parse_mode = bot.default[parse_mode.name]
        entities = getattr(value, entities_field, None)
        processed, merged = await _process_text(text, entities, parse_mode=parse_mode, bot=bot)
        if processed != text:
            updates.update({text_field: processed, entities_field: merged})
            if merged and "parse_mode" in fields:
                updates["parse_mode"] = None
    if "media" in fields:
        media = getattr(value, "media", None)
        if isinstance(media, list):
            prepared = [await _prepare_outgoing(item, bot) for item in media]
            if any(left is not right for left, right in zip(prepared, media, strict=True)):
                updates["media"] = prepared
        elif hasattr(type(media), "model_fields"):
            prepared = await _prepare_outgoing(media, bot)
            if prepared is not media:
                updates["media"] = prepared
    return value.model_copy(update=updates) if updates else value


def patch_bot_methods() -> bool:
    """Подключает обработку эмодзи ко всем исходящим запросам ботов."""
    global _BOT
    try:
        from bot import bot

        _BOT = bot
        if getattr(Bot, "_custom_emojis_patched", False):
            return True
        original_call = Bot.__call__

        @wraps(original_call)
        async def patched_call(self, method, request_timeout=None):
            prepared = await _prepare_outgoing(method, self)
            return await original_call(self, prepared, request_timeout=request_timeout)

        Bot._custom_emojis_original_call = original_call
        Bot.__call__ = patched_call
        Bot._custom_emojis_patched = True
        return True
    except Exception as error:
        logger.error(f"[CustomEmojis] Patch failed: {error}", exc_info=True)
        return False


def initialize_custom_emojis() -> bool:
    """Инициализирует поддержку пользовательских эмодзи."""
    return patch_bot_methods()

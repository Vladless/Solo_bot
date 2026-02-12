from __future__ import annotations

import re
from typing import Any

from aiogram import Bot
from aiogram.enums import MessageEntityType
from aiogram.types import MessageEntity

from logger import logger

_PLACEHOLDER_CACHE: dict[str, str] = {}
_BOT: Bot | None = None

_MARKER_RE = re.compile(r"\{emoji:(\d+)\}|\[emoji:(\d+)\]")
_CODE_BLOCK_RE = re.compile(r"<code[^>]*>(.*?)</code>", re.IGNORECASE | re.DOTALL)
_PRE_BLOCK_RE = re.compile(r"<pre[^>]*>(.*?)</pre>", re.IGNORECASE | re.DOTALL)


def _set_parse_mode_none(kwargs: dict[str, Any]) -> None:
    kwargs["parse_mode"] = None


def _get_protected_ranges(text: str) -> list[tuple[int, int]]:
    """Return ranges inside <code> and <pre> tags to skip replacements."""
    ranges: list[tuple[int, int]] = []
    for match in _CODE_BLOCK_RE.finditer(text):
        ranges.append((match.start(1), match.end(1)))
    for match in _PRE_BLOCK_RE.finditer(text):
        ranges.append((match.start(1), match.end(1)))
    return ranges


def _is_in_ranges(pos: int, ranges: list[tuple[int, int]]) -> bool:
    for start, end in ranges:
        if start <= pos < end:
            return True
    return False


def _utf16_len(text: str) -> int:
    """Length of string in UTF-16 code units."""
    return len(text.encode("utf-16-le")) // 2


async def _fetch_placeholder(emoji_id: str) -> str:
    """Resolve a custom emoji id to a visible placeholder emoji."""
    if emoji_id in _PLACEHOLDER_CACHE:
        return _PLACEHOLDER_CACHE[emoji_id]

    if _BOT is None:
        return "😀"

    try:
        stickers = await _BOT.get_custom_emoji_stickers(custom_emoji_ids=[emoji_id])
        if stickers:
            sticker = stickers[0]
            placeholder = None
            if getattr(sticker, "emoji", None):
                placeholder = sticker.emoji
            elif getattr(sticker, "alt", None):
                placeholder = sticker.alt

            if placeholder:
                _PLACEHOLDER_CACHE[emoji_id] = placeholder
                return placeholder
    except Exception:
        pass

    return "😀"


async def _replace_markers(text: str) -> tuple[str, list[MessageEntity]]:
    """Replace markers with placeholders and build custom emoji entities."""
    if not text:
        return text, []

    entities: list[MessageEntity] = []
    protected_ranges = _get_protected_ranges(text)
    matches = [m for m in _MARKER_RE.finditer(text) if not _is_in_ranges(m.start(), protected_ranges)]
    if not matches:
        return text, []

    replacements: list[tuple[int, int, str, str]] = []
    for match in matches:
        emoji_id = match.group(1) or match.group(2)
        start, end = match.start(), match.end()
        placeholder = await _fetch_placeholder(emoji_id)
        replacements.append((start, end, str(emoji_id), placeholder))

    parts: list[str] = []
    pos = 0
    for start, end, _emoji_id, placeholder in replacements:
        parts.append(text[pos:start])
        parts.append(placeholder)
        pos = end
    parts.append(text[pos:])
    result = "".join(parts)

    offset_utf16 = 0
    pos = 0
    for start, end, emoji_id, placeholder in replacements:
        offset_utf16 += _utf16_len(text[pos:start])
        length_utf16 = _utf16_len(placeholder)
        entities.append(
            MessageEntity(
                type=MessageEntityType.CUSTOM_EMOJI,
                offset=offset_utf16,
                length=length_utf16,
                custom_emoji_id=emoji_id,
            ),
        )
        offset_utf16 += length_utf16
        pos = end

    return result, entities


def _parse_html_entities(text: str) -> list[MessageEntity]:
    """Parse simple HTML tags to entities (bold/italic/etc, links)."""
    entities: list[MessageEntity] = []

    link_open = re.compile(r'<a href="([^"]+)"\s*>|<a href=\'([^\']+)\'\s*>')
    link_close = re.compile(r"</a>")

    link_stack: list[dict[str, Any]] = []
    for match in link_open.finditer(text):
        url = match.group(1) or match.group(2)
        link_stack.append({"open_end": match.end(), "url": url})

    for close in link_close.finditer(text):
        if not link_stack:
            continue
        open_tag = link_stack.pop()
        open_pos = open_tag["open_end"]
        close_pos = close.start()
        content = text[open_pos:close_pos]
        entities.append(
            MessageEntity(
                type=MessageEntityType.TEXT_LINK,
                offset=_utf16_len(text[:open_pos]),
                length=_utf16_len(content),
                url=open_tag["url"],
            )
        )

    tag_map: list[tuple[str, str, MessageEntityType]] = [
        (r"<b\s*>", r"</b>", MessageEntityType.BOLD),
        (r"<strong\s*>", r"</strong>", MessageEntityType.BOLD),
        (r"<i\s*>", r"</i>", MessageEntityType.ITALIC),
        (r"<em\s*>", r"</em>", MessageEntityType.ITALIC),
        (r"<u\s*>", r"</u>", MessageEntityType.UNDERLINE),
        (r"<ins\s*>", r"</ins>", MessageEntityType.UNDERLINE),
        (r"<s\s*>", r"</s>", MessageEntityType.STRIKETHROUGH),
        (r"<strike\s*>", r"</strike>", MessageEntityType.STRIKETHROUGH),
        (r"<del\s*>", r"</del>", MessageEntityType.STRIKETHROUGH),
        (r"<code\s*>", r"</code>", MessageEntityType.CODE),
        (r"<pre\s*>", r"</pre>", MessageEntityType.PRE),
        (r"<blockquote\s*>", r"</blockquote>", MessageEntityType.BLOCKQUOTE),
    ]

    for open_pat, close_pat, entity_type in tag_map:
        for open in re.finditer(open_pat, text):
            close = re.search(close_pat, text[open.end() :])
            if not close:
                continue
            close_pos = open.end() + close.start()
            content = text[open.end() : close_pos]
            entities.append(
                MessageEntity(
                    type=entity_type,
                    offset=_utf16_len(text[: open.end()]),
                    length=_utf16_len(content),
                )
            )

    return entities


async def _process_text(
    text: str, entities: list[MessageEntity] | None = None
) -> tuple[str, list[MessageEntity] | None]:
    """Apply custom emoji markers and merge entities."""
    processed, custom_entities = await _replace_markers(text)
    if not custom_entities:
        return text, entities

    html_entities: list[MessageEntity] = []
    if "<" in processed and ">" in processed:
        html_entities = _parse_html_entities(processed)

    if not html_entities and not custom_entities and not entities:
        return text, None

    if html_entities:
        plain = []
        html_pos = 0
        utf16_map: dict[int, int] = {}
        plain_utf16 = 0
        html_utf16 = 0

        while html_pos < len(processed):
            ch = processed[html_pos]
            if ch == "<":
                while html_pos < len(processed) and processed[html_pos] != ">":
                    ch = processed[html_pos]
                    ch_len = _utf16_len(ch)
                    for i in range(ch_len):
                        utf16_map[html_utf16 + i] = plain_utf16
                    html_utf16 += ch_len
                    html_pos += 1
                if html_pos < len(processed):
                    ch = processed[html_pos]
                    ch_len = _utf16_len(ch)
                    for i in range(ch_len):
                        utf16_map[html_utf16 + i] = plain_utf16
                    html_utf16 += ch_len
                    html_pos += 1
                continue

            plain.append(ch)
            ch_len = _utf16_len(ch)
            for i in range(ch_len):
                utf16_map[html_utf16 + i] = plain_utf16 + i
            plain_utf16 += ch_len
            html_utf16 += ch_len
            html_pos += 1

        def remap(offset: int) -> int:
            if offset in utf16_map:
                return utf16_map[offset]
            keys = sorted(utf16_map.keys())
            best = None
            for key in keys:
                if key <= offset:
                    best = key
                else:
                    break
            return utf16_map[best] if best is not None else offset

        remapped_html: list[MessageEntity] = []
        for ent in html_entities:
            new_offset = remap(ent.offset)
            end_offset = remap(ent.offset + ent.length)
            data = ent.model_dump()
            data["offset"] = new_offset
            data["length"] = end_offset - new_offset
            remapped_html.append(MessageEntity(**data))

        remapped_custom: list[MessageEntity] = []
        for ent in custom_entities:
            data = ent.model_dump()
            data["offset"] = remap(ent.offset)
            remapped_custom.append(MessageEntity(**data))

        merged: list[MessageEntity] = []
        merged.extend(remapped_html)
        merged.extend(remapped_custom)
        if entities:
            merged.extend(entities)
        merged.sort(key=lambda e: e.offset)
        return "".join(plain), merged

    merged: list[MessageEntity] = []
    merged.extend(custom_entities)
    if entities:
        merged.extend(entities)
    merged.sort(key=lambda e: e.offset)
    return processed, merged


def patch_bot_methods() -> bool:
    """Patch Message methods to auto-handle custom emojis."""
    global _BOT
    try:
        from aiogram.types import Message
        from bot import bot

        _BOT = bot

        if not hasattr(Message, "_custom_emojis_patched"):
            Message._custom_emojis_patched = True
            Message._original_answer = Message.answer
            Message._original_edit_text = Message.edit_text
            Message._original_edit_caption = Message.edit_caption
            Message._original_answer_photo = Message.answer_photo
            Message._original_answer_video = Message.answer_video
            Message._original_answer_animation = Message.answer_animation
            Message._original_edit_media = Message.edit_media

        async def patched_answer(self, text: str, entities: list[MessageEntity] | None = None, **kwargs):
            processed, merged = await _process_text(text, entities)
            if merged:
                _set_parse_mode_none(kwargs)
            return await self._original_answer(text=processed, entities=merged, **kwargs)

        async def patched_edit_text(self, text: str, entities: list[MessageEntity] | None = None, **kwargs):
            processed, merged = await _process_text(text, entities)
            if merged:
                _set_parse_mode_none(kwargs)
            return await self._original_edit_text(text=processed, entities=merged, **kwargs)

        async def patched_edit_caption(
            self,
            caption: str | None = None,
            caption_entities: list[MessageEntity] | None = None,
            **kwargs,
        ):
            if not caption:
                return await self._original_edit_caption(caption=caption, caption_entities=caption_entities, **kwargs)
            processed, merged = await _process_text(caption, caption_entities)
            if merged:
                _set_parse_mode_none(kwargs)
            return await self._original_edit_caption(caption=processed, caption_entities=merged, **kwargs)

        async def patched_answer_photo(
            self,
            photo: Any,
            caption: str | None = None,
            caption_entities: list[MessageEntity] | None = None,
            **kwargs,
        ):
            if not caption:
                return await self._original_answer_photo(
                    photo=photo, caption=caption, caption_entities=caption_entities, **kwargs
                )
            processed, merged = await _process_text(caption, caption_entities)
            if merged:
                _set_parse_mode_none(kwargs)
            return await self._original_answer_photo(photo=photo, caption=processed, caption_entities=merged, **kwargs)

        async def patched_answer_video(
            self,
            video: Any,
            caption: str | None = None,
            caption_entities: list[MessageEntity] | None = None,
            **kwargs,
        ):
            if not caption:
                return await self._original_answer_video(
                    video=video, caption=caption, caption_entities=caption_entities, **kwargs
                )
            processed, merged = await _process_text(caption, caption_entities)
            if merged:
                _set_parse_mode_none(kwargs)
            return await self._original_answer_video(video=video, caption=processed, caption_entities=merged, **kwargs)

        async def patched_answer_animation(
            self,
            animation: Any,
            caption: str | None = None,
            caption_entities: list[MessageEntity] | None = None,
            **kwargs,
        ):
            if not caption:
                return await self._original_answer_animation(
                    animation=animation,
                    caption=caption,
                    caption_entities=caption_entities,
                    **kwargs,
                )
            processed, merged = await _process_text(caption, caption_entities)
            if merged:
                _set_parse_mode_none(kwargs)
            return await self._original_answer_animation(
                animation=animation,
                caption=processed,
                caption_entities=merged,
                **kwargs,
            )

        async def patched_edit_media(self, media: Any, **kwargs):
            if hasattr(media, "caption") and media.caption:
                processed, merged = await _process_text(media.caption, getattr(media, "caption_entities", None))
                media.caption = processed
                if merged:
                    if hasattr(media, "parse_mode"):
                        media.parse_mode = None
                    kwargs["parse_mode"] = None
                    media.caption_entities = merged
            return await self._original_edit_media(media=media, **kwargs)

        Message.answer = patched_answer
        Message.edit_text = patched_edit_text
        Message.edit_caption = patched_edit_caption
        Message.answer_photo = patched_answer_photo
        Message.answer_video = patched_answer_video
        Message.answer_animation = patched_answer_animation
        Message.edit_media = patched_edit_media

        return True
    except Exception as e:
        logger.error(f"[CustomEmojis] Patch failed: {e}", exc_info=True)
        return False


def initialize_custom_emojis() -> bool:
    """Initialize custom emoji support."""
    try:
        return patch_bot_methods()
    except Exception as e:
        logger.error(f"[CustomEmojis] Init failed: {e}", exc_info=True)
        return False

from __future__ import annotations

import re
from typing import Any, Iterable

from aiogram import Bot
from aiogram.enums import MessageEntityType
from aiogram.types import MessageEntity

from logger import logger

_PLACEHOLDER_CACHE: dict[str, str] = {}
_BOT: Bot | None = None

_MARKER_RE = re.compile(r"\{emoji:(\d+)\}|\[emoji:(\d+)\]")
_CODE_BLOCK_RE = re.compile(r"<code[^>]*>(.*?)</code>", re.IGNORECASE | re.DOTALL)
_PRE_BLOCK_RE = re.compile(r"<pre[^>]*>(.*?)</pre>", re.IGNORECASE | re.DOTALL)


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
    result = text

    protected_ranges = _get_protected_ranges(text)
    matches = [m for m in _MARKER_RE.finditer(text) if not _is_in_ranges(m.start(), protected_ranges)]
    for match in reversed(matches):
        emoji_id = match.group(1) or match.group(2)
        marker = match.group(0)
        marker_pos = match.start()

        placeholder = await _fetch_placeholder(emoji_id)
        result = result[:marker_pos] + placeholder + result[marker_pos + len(marker) :]

        offset = _utf16_len(result[:marker_pos])
        length = _utf16_len(placeholder)

        entities.insert(
            0,
            MessageEntity(
                type=MessageEntityType.CUSTOM_EMOJI,
                offset=offset,
                length=length,
                custom_emoji_id=str(emoji_id),
            ),
        )

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


def _set_parse_mode_none(kwargs: dict[str, Any]) -> None:
    if kwargs.get("parse_mode") is not None:
        kwargs["parse_mode"] = None


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


"""
Support custom Telegram emojis in bot texts.
Allows writing custom emoji IDs in texts via a special syntax.
"""

import re
from typing import Any

from aiogram import Bot
from aiogram.enums import MessageEntityType
from aiogram.types import MessageEntity

from logger import logger

_emoji_placeholder_cache: dict[str, str] = {}
_bot_instance: Bot | None = None


async def _get_emoji_placeholder(emoji_id: str) -> str:
    """Fetch placeholder emoji for custom_emoji_id via API, with cache."""
    if emoji_id in _emoji_placeholder_cache:
        return _emoji_placeholder_cache[emoji_id]

    if _bot_instance is None:
        return "😀"

    try:
        stickers = await _bot_instance.get_custom_emoji_stickers(custom_emoji_ids=[emoji_id])
        if stickers:
            sticker = stickers[0]
            placeholder = None
            if hasattr(sticker, "emoji") and sticker.emoji:
                placeholder = sticker.emoji
            elif hasattr(sticker, "alt") and sticker.alt:
                placeholder = sticker.alt

            if placeholder:
                _emoji_placeholder_cache[emoji_id] = placeholder
                return placeholder
    except Exception:
        pass

    return "😀"


def _get_utf16_length(text: str) -> int:
    """Calculate length in UTF-16 code units."""
    return len(text.encode("utf-16-le")) // 2


async def parse_custom_emoji_markers(text: str) -> tuple[str, list[MessageEntity]]:
    """Parse text with custom emoji markers to placeholders and entities."""
    if not text:
        return text, []

    entities: list[MessageEntity] = []
    result_text = text
    pattern = r"\{emoji:(\d+)\}|\[emoji:(\d+)\]"
    matches = list(re.finditer(pattern, text))

    for match in reversed(matches):
        emoji_id = match.group(1) or match.group(2)
        marker_text = match.group(0)
        marker_pos_in_result = match.start()

        emoji_placeholder = await _get_emoji_placeholder(emoji_id)
        result_text = (
            result_text[:marker_pos_in_result]
            + emoji_placeholder
            + result_text[marker_pos_in_result + len(marker_text) :]
        )

        text_before = result_text[:marker_pos_in_result]
        offset_utf16 = _get_utf16_length(text_before)
        placeholder_utf16_length = _get_utf16_length(emoji_placeholder)

        entity = MessageEntity(
            type=MessageEntityType.CUSTOM_EMOJI,
            offset=offset_utf16,
            length=placeholder_utf16_length,
            custom_emoji_id=str(emoji_id),
        )
        entities.insert(0, entity)

    return result_text, entities


def _parse_html_entities(text: str) -> list[MessageEntity]:
    """Parse HTML markup and build entities manually."""
    entities: list[MessageEntity] = []

    link_patterns = [
        (r'<a href="([^"]+)"\s*>', r"</a>"),
        (r"<a href='([^']+)'\s*>", r"</a>"),
    ]

    link_tags = []
    for open_pattern, close_pattern in link_patterns:
        for open_match in re.finditer(open_pattern, text):
            url = open_match.group(1)
            link_tags.append({
                "type": "open",
                "url": url,
                "open_start": open_match.start(),
                "open_end": open_match.end(),
                "close_pattern": close_pattern,
            })

    for close_match in re.finditer(r"</a>", text):
        link_tags.append({"type": "close", "pos": close_match.start()})

    link_tags.sort(key=lambda x: x.get("open_start", x.get("pos", 0)))

    link_stacks = []
    tag_positions = []

    for tag in link_tags:
        if tag["type"] == "open":
            link_stacks.append(tag)
        else:
            if link_stacks:
                matching_open = link_stacks.pop()
                open_pos = matching_open["open_end"]
                close_pos = tag["pos"]
                content = text[open_pos:close_pos]

                tag_positions.append({
                    "open_pos": open_pos,
                    "close_pos": close_pos,
                    "content": content,
                    "entity_type": "text_link",
                    "url": matching_open["url"],
                })

    other_patterns = [
        (r"<b\s*>", r"</b>", "bold"),
        (r"<strong\s*>", r"</strong>", "bold"),
        (r"<i\s*>", r"</i>", "italic"),
        (r"<em\s*>", r"</em>", "italic"),
        (r"<u\s*>", r"</u>", "underline"),
        (r"<ins\s*>", r"</ins>", "underline"),
        (r"<s\s*>", r"</s>", "strikethrough"),
        (r"<strike\s*>", r"</strike>", "strikethrough"),
        (r"<del\s*>", r"</del>", "strikethrough"),
        (r"<code\s*>", r"</code>", "code"),
        (r"<pre\s*>", r"</pre>", "pre"),
        (r"<blockquote\s*>", r"</blockquote>", "blockquote"),
    ]

    other_tags = []
    for open_pattern, close_pattern, entity_type in other_patterns:
        for open_match in re.finditer(open_pattern, text):
            other_tags.append({
                "type": "open",
                "entity_type": entity_type,
                "open_end": open_match.end(),
                "close_pattern": close_pattern,
            })

        for close_match in re.finditer(close_pattern, text):
            other_tags.append({"type": "close", "entity_type": entity_type, "pos": close_match.start()})

    other_tags.sort(key=lambda x: x.get("open_end", x.get("pos", 0)))

    other_stacks: dict[str, list[dict[str, Any]]] = {}

    for tag in other_tags:
        entity_type = tag["entity_type"]

        if tag["type"] == "open":
            if entity_type not in other_stacks:
                other_stacks[entity_type] = []
            other_stacks[entity_type].append(tag)
        else:
            if entity_type in other_stacks and other_stacks[entity_type]:
                matching_open = other_stacks[entity_type].pop()
                open_pos = matching_open["open_end"]
                close_pos = tag["pos"]
                content = text[open_pos:close_pos]

                tag_positions.append({
                    "open_pos": open_pos,
                    "close_pos": close_pos,
                    "content": content,
                    "entity_type": entity_type,
                    "url": None,
                })

    tag_positions.sort(key=lambda x: x["open_pos"])

    for tag_info in tag_positions:
        content = tag_info["content"]
        open_pos = tag_info["open_pos"]
        entity_type = tag_info["entity_type"]
        url = tag_info["url"]

        text_before_content = text[:open_pos]
        offset_utf16 = _get_utf16_length(text_before_content)

        length_utf16 = _get_utf16_length(content)

        entity_dict = {
            "type": MessageEntityType(entity_type),
            "offset": offset_utf16,
            "length": length_utf16,
        }
        if url:
            entity_dict["url"] = url

        entity = MessageEntity(**entity_dict)
        entities.append(entity)

    return entities


async def process_text_with_custom_emojis(text: str) -> tuple[str, list[MessageEntity] | None]:
    """Process text with custom emoji markers."""
    if not text or not isinstance(text, str):
        return text, None

    processed_text, entities = await parse_custom_emoji_markers(text)
    return (processed_text, entities) if entities else (text, None)


def patch_bot_methods() -> bool:
    """Patch Message methods to handle custom emojis."""
    global _bot_instance
    try:
        from aiogram.types import Message
        from bot import bot

        _bot_instance = bot

        if not hasattr(Message, "_original_answer"):
            Message._original_answer = Message.answer
            Message._original_edit_text = Message.edit_text
            Message._original_edit_caption = Message.edit_caption
            Message._original_answer_photo = Message.answer_photo
            Message._original_answer_video = Message.answer_video
            Message._original_answer_animation = Message.answer_animation
            Message._original_edit_media = Message.edit_media

        async def process_text_and_entities(
            text: str,
            entities: list[MessageEntity] | None = None,
            parse_mode: str | None = None,
        ):
            """Process text and entities for custom emojis."""
            if not text:
                return text, entities

            processed_text_with_html, custom_entities = await process_text_with_custom_emojis(text)

            html_entities_with_html = []
            if custom_entities and "<" in processed_text_with_html and ">" in processed_text_with_html:
                html_entities_with_html = _parse_html_entities(processed_text_with_html)

            if html_entities_with_html or custom_entities:
                text_without_html = ""
                utf16_offset_map: dict[int, int] = {}
                html_pos = 0
                plain_utf16 = 0
                html_utf16 = 0

                while html_pos < len(processed_text_with_html):
                    if processed_text_with_html[html_pos] == "<":
                        while html_pos < len(processed_text_with_html) and processed_text_with_html[html_pos] != ">":
                            char = processed_text_with_html[html_pos]
                            char_utf16_len = _get_utf16_length(char)
                            for i in range(char_utf16_len):
                                utf16_offset_map[html_utf16 + i] = plain_utf16
                            html_utf16 += char_utf16_len
                            html_pos += 1
                        if html_pos < len(processed_text_with_html):
                            char = processed_text_with_html[html_pos]
                            char_utf16_len = _get_utf16_length(char)
                            for i in range(char_utf16_len):
                                utf16_offset_map[html_utf16 + i] = plain_utf16
                            html_utf16 += char_utf16_len
                            html_pos += 1
                    else:
                        char = processed_text_with_html[html_pos]
                        text_without_html += char
                        char_utf16_len = _get_utf16_length(char)
                        for i in range(char_utf16_len):
                            utf16_offset_map[html_utf16 + i] = plain_utf16 + i
                        plain_utf16 += char_utf16_len
                        html_utf16 += char_utf16_len
                        html_pos += 1

                def recalculate_offset(entity_offset_utf16: int) -> int:
                    """Recalculate UTF-16 offset from HTML to plain text."""
                    if entity_offset_utf16 in utf16_offset_map:
                        return utf16_offset_map[entity_offset_utf16]

                    sorted_keys = sorted(utf16_offset_map.keys())
                    best_match = None
                    for key in sorted_keys:
                        if key <= entity_offset_utf16:
                            best_match = key
                        else:
                            break

                    return utf16_offset_map[best_match] if best_match is not None else entity_offset_utf16

                html_entities = []
                for entity in html_entities_with_html:
                    new_offset = recalculate_offset(entity.offset)
                    entity_end_offset = entity.offset + entity.length
                    new_end_offset = recalculate_offset(entity_end_offset)
                    new_length = new_end_offset - new_offset
                    entity_dict = entity.model_dump()
                    entity_dict["offset"] = new_offset
                    entity_dict["length"] = new_length
                    html_entities.append(MessageEntity(**entity_dict))

                corrected_custom_entities = []
                for entity in custom_entities:
                    new_offset = recalculate_offset(entity.offset)
                    entity_dict = entity.model_dump()
                    entity_dict["offset"] = new_offset
                    corrected_custom_entities.append(MessageEntity(**entity_dict))

                final_entities: list[MessageEntity] = []
                if html_entities:
                    final_entities.extend(html_entities)
                if corrected_custom_entities:
                    final_entities.extend(corrected_custom_entities)
                if entities:
                    final_entities.extend(entities)

                if final_entities:
                    final_entities = sorted(final_entities, key=lambda e: e.offset)

                return text_without_html, final_entities if final_entities else None

            return text, entities

        async def patched_message_answer(self, text: str, entities: list[MessageEntity] | None = None, **kwargs):
            """Patched Message.answer."""
            processed_text, final_entities = await process_text_and_entities(text, entities, kwargs.get("parse_mode"))
            if final_entities:
                kwargs["parse_mode"] = None
            return await self._original_answer(
                text=processed_text, entities=final_entities if final_entities else None, **kwargs
            )

        async def patched_message_edit_text(self, text: str, entities: list[MessageEntity] | None = None, **kwargs):
            """Patched Message.edit_text."""
            processed_text, final_entities = await process_text_and_entities(text, entities, kwargs.get("parse_mode"))
            if final_entities:
                kwargs["parse_mode"] = None
            return await self._original_edit_text(text=processed_text, entities=final_entities, **kwargs)

        async def patched_message_edit_caption(
            self,
            caption: str | None = None,
            caption_entities: list[MessageEntity] | None = None,
            **kwargs,
        ):
            """Patched Message.edit_caption."""
            if not caption:
                return await self._original_edit_caption(caption=caption, caption_entities=caption_entities, **kwargs)
            processed_caption, final_entities = await process_text_and_entities(
                caption, caption_entities, kwargs.get("parse_mode")
            )
            if final_entities:
                kwargs["parse_mode"] = None
            return await self._original_edit_caption(
                caption=processed_caption, caption_entities=final_entities, **kwargs
            )

        async def patched_message_answer_photo(
            self,
            photo: Any,
            caption: str | None = None,
            caption_entities: list[MessageEntity] | None = None,
            **kwargs,
        ):
            """Patched Message.answer_photo."""
            if not caption:
                return await self._original_answer_photo(
                    photo=photo, caption=caption, caption_entities=caption_entities, **kwargs
                )
            processed_caption, final_entities = await process_text_and_entities(
                caption, caption_entities, kwargs.get("parse_mode")
            )
            if final_entities:
                kwargs["parse_mode"] = None
            return await self._original_answer_photo(
                photo=photo, caption=processed_caption, caption_entities=final_entities, **kwargs
            )

        async def patched_message_answer_video(
            self,
            video: Any,
            caption: str | None = None,
            caption_entities: list[MessageEntity] | None = None,
            **kwargs,
        ):
            """Patched Message.answer_video."""
            if not caption:
                return await self._original_answer_video(
                    video=video, caption=caption, caption_entities=caption_entities, **kwargs
                )
            processed_caption, final_entities = await process_text_and_entities(
                caption, caption_entities, kwargs.get("parse_mode")
            )
            if final_entities:
                kwargs["parse_mode"] = None
            return await self._original_answer_video(
                video=video, caption=processed_caption, caption_entities=final_entities, **kwargs
            )

        async def patched_message_answer_animation(
            self,
            animation: Any,
            caption: str | None = None,
            caption_entities: list[MessageEntity] | None = None,
            **kwargs,
        ):
            """Patched Message.answer_animation."""
            if not caption:
                return await self._original_answer_animation(
                    animation=animation,
                    caption=caption,
                    caption_entities=caption_entities,
                    **kwargs,
                )
            processed_caption, final_entities = await process_text_and_entities(
                caption, caption_entities, kwargs.get("parse_mode")
            )
            if final_entities:
                kwargs["parse_mode"] = None
            return await self._original_answer_animation(
                animation=animation,
                caption=processed_caption,
                caption_entities=final_entities,
                **kwargs,
            )

        async def patched_message_edit_media(self, media: Any, **kwargs):
            """Patched Message.edit_media."""
            if hasattr(media, "caption") and media.caption:
                processed_caption, final_entities = await process_text_and_entities(
                    media.caption, getattr(media, "caption_entities", None), kwargs.get("parse_mode")
                )
                media.caption = processed_caption
                if final_entities:
                    if hasattr(media, "parse_mode"):
                        media.parse_mode = None
                    kwargs["parse_mode"] = None
                    media.caption_entities = final_entities
            return await self._original_edit_media(media=media, **kwargs)

        Message.answer = patched_message_answer
        Message.edit_text = patched_message_edit_text
        Message.edit_caption = patched_message_edit_caption
        Message.answer_photo = patched_message_answer_photo
        Message.answer_video = patched_message_answer_video
        Message.answer_animation = patched_message_answer_animation
        Message.edit_media = patched_message_edit_media

        return True

    except Exception as e:
        logger.error(f"[CustomEmojis] Error while patching bot methods: {e}", exc_info=True)
        return False


def initialize_custom_emojis() -> bool:
    """Initialize custom emoji support."""
    try:
        return patch_bot_methods()
    except Exception as e:
        logger.error(f"[CustomEmojis] Error during initialization: {e}", exc_info=True)
        return False

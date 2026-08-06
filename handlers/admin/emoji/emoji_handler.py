from __future__ import annotations

from collections.abc import Iterable

from aiogram import F, Router
from aiogram.enums import MessageEntityType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, MessageEntity
from aiogram.utils.keyboard import InlineKeyboardBuilder

from filters.admin import HasPermission, IsAdminFilter
from filters.permissions import PERM_EMOJI
from settings.buttons import BACK

from ..panel.headers import content_width, menu_text, menu_title, quote, strip_tags, wrap_text
from ..panel.keyboard import AdminPanelCallback, build_admin_back_kb


class AdminEmojiState(StatesGroup):
    waiting_for_custom_emoji = State()


router = Router()
router.callback_query.filter(HasPermission(PERM_EMOJI))
router.message.filter(HasPermission(PERM_EMOJI))


def _build_back_to_emoji_menu() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.button(text=BACK, callback_data=AdminPanelCallback(action="emoji").pack())
    builder.adjust(1)
    return builder


def _extract_custom_emoji_ids(entities: Iterable[MessageEntity]) -> list[str]:
    ids: list[str] = []
    for ent in entities:
        if ent.type == MessageEntityType.CUSTOM_EMOJI and ent.custom_emoji_id:
            ids.append(ent.custom_emoji_id)
    return ids


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


@router.callback_query(AdminPanelCallback.filter(F.action == "emoji"), IsAdminFilter())
async def show_emoji_menu(callback_query: CallbackQuery, state: FSMContext):
    await state.set_state(AdminEmojiState.waiting_for_custom_emoji)
    example_id = "5201769509345588200"
    marker = f"{{{{emoji:{example_id}}}}}"
    preview_placeholder = "😀"
    hint = wrap_text("Пришлите кастомное эмоджи — верну его ID и пример для файла текстов.")
    text = strip_tags(menu_title("Эмоджи", content_width(hint))) + "\n\n" + hint + "\n\nПример:\n" + preview_placeholder

    entities: list[MessageEntity] = []
    start = 0
    while True:
        marker_pos = text.find(marker, start)
        if marker_pos == -1:
            break
        entities.append(
            MessageEntity(
                type=MessageEntityType.CODE,
                offset=_utf16_len(text[:marker_pos]),
                length=_utf16_len(marker),
            )
        )
        start = marker_pos + len(marker)
    preview_pos = text.find(preview_placeholder)
    if preview_pos != -1:
        entities.append(
            MessageEntity(
                type=MessageEntityType.CUSTOM_EMOJI,
                offset=_utf16_len(text[:preview_pos]),
                length=_utf16_len(preview_placeholder),
                custom_emoji_id=example_id,
            )
        )

    if hasattr(callback_query.message, "_original_edit_text"):
        await callback_query.message._original_edit_text(
            text=text,
            entities=entities,
            reply_markup=build_admin_back_kb("admin"),
            parse_mode=None,
        )
    else:
        await callback_query.message.edit_text(
            text=text,
            entities=entities,
            reply_markup=build_admin_back_kb("admin"),
            parse_mode=None,
        )


@router.message(AdminEmojiState.waiting_for_custom_emoji, IsAdminFilter())
async def handle_custom_emoji_id(message: Message, state: FSMContext):
    entities = list(message.entities or []) + list(message.caption_entities or [])
    emoji_ids = _extract_custom_emoji_ids(entities)

    if not emoji_ids:
        await message.answer(
            menu_text(
                "Эмоджи",
                "❌ Не вижу кастомных эмоджи.",
                quote("Отправьте именно <b>кастомный эмоджи</b> из набора."),
                markup=_build_back_to_emoji_menu().as_markup(),
            ),
            reply_markup=_build_back_to_emoji_menu().as_markup(),
        )
        return

    unique_ids: list[str] = []
    for emoji_id in emoji_ids:
        if emoji_id not in unique_ids:
            unique_ids.append(emoji_id)

    placeholder = "😀"
    back_builder = _build_back_to_emoji_menu()

    if len(unique_ids) == 1:
        emoji_id = unique_ids[0]
        marker = f"{{{{emoji:{emoji_id}}}}}"
        example_send = f"Ты отправил: Привет, {marker} !"
        example_recv = f"А получил: Привет, {placeholder} !"
        instruction_text = (
            "✅ ID кастомного эмоджи\n"
            f"{emoji_id}\n\n"
            "Вставляйте в файл текстов так:\n"
            f"{marker}\n\n"
            "Пример:\n"
            f"{example_send}\n"
            f"{example_recv}\n\n"
            + wrap_text("⚠️ Условие: кастомные эмоджи видны, только если у владельца бота есть Telegram Premium.")
        )
        preview_text = example_recv
        preview_ids = [emoji_id]
        code_markers = [marker]
    else:
        ids_text = "\n".join(f"• {emoji_id}" for emoji_id in unique_ids)
        markers_text = "\n".join(f"• {{{{emoji:{emoji_id}}}}}" for emoji_id in unique_ids)
        markers_inline = " ".join(f"{{{{emoji:{emoji_id}}}}}" for emoji_id in unique_ids)
        example_send = f"Ты отправил: Привет, {markers_inline} !"
        example_recv = "А получил: Привет, " + " ".join(placeholder for _ in unique_ids) + " !"
        instruction_text = (
            "✅ ID кастомных эмоджи\n"
            f"{ids_text}\n\n"
            "Вставляйте в файл текстов так:\n"
            f"{markers_text}\n\n"
            "Пример:\n"
            f"{example_send}\n"
            f"{example_recv}\n\n"
            + wrap_text("⚠️ Условие: кастомные эмоджи видны, только если у владельца бота есть Telegram Premium.")
        )
        preview_text = example_recv
        preview_ids = unique_ids
        code_markers = [f"{{{{emoji:{emoji_id}}}}}" for emoji_id in unique_ids]

    full_text = instruction_text

    entities: list[MessageEntity] = []
    for marker in code_markers:
        start = 0
        while True:
            pos = full_text.find(marker, start)
            if pos == -1:
                break
            entities.append(
                MessageEntity(
                    type=MessageEntityType.CODE,
                    offset=_utf16_len(full_text[:pos]),
                    length=_utf16_len(marker),
                )
            )
            start = pos + len(marker)

    for emoji_id in unique_ids:
        start = 0
        while True:
            pos = full_text.find(emoji_id, start)
            if pos == -1:
                break
            entities.append(
                MessageEntity(
                    type=MessageEntityType.CODE,
                    offset=_utf16_len(full_text[:pos]),
                    length=_utf16_len(emoji_id),
                )
            )
            start = pos + len(emoji_id)
    preview_offset_base = _utf16_len(full_text[: full_text.index(preview_text)])
    running_utf16 = 0
    idx = 0
    for ch in preview_text:
        if ch == placeholder and idx < len(preview_ids):
            entities.append(
                MessageEntity(
                    type=MessageEntityType.CUSTOM_EMOJI,
                    offset=preview_offset_base + running_utf16,
                    length=_utf16_len(placeholder),
                    custom_emoji_id=str(preview_ids[idx]),
                )
            )
            idx += 1
        running_utf16 += _utf16_len(ch)

    if hasattr(message, "_original_answer"):
        await message._original_answer(
            text=full_text,
            entities=entities,
            reply_markup=back_builder.as_markup(),
            parse_mode=None,
        )
    else:
        await message.answer(text=full_text, reply_markup=back_builder.as_markup(), parse_mode=None)
    await state.clear()

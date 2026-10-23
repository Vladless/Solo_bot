from datetime import UTC, datetime
from pathlib import Path

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaAnimation,
    InputMediaDocument,
    InputMediaPhoto,
)
from sqlalchemy import select

from database import async_session_maker
from database.models import Identity, Ticket, TicketMessage, TicketTelegramMessage
from database.tickets import get_ticket_delivery_identity


def source_from_message(message) -> dict:
    """Сохраняет координаты исходного сообщения без его содержимого."""
    media = message.animation or ((message.photo or [])[-1] if message.photo else message.document)
    kind = (
        "animation" if message.animation else "photo" if message.photo else "document" if message.document else "text"
    )
    return {
        "bot_id": message.bot.id,
        "chat_id": message.chat.id,
        "topic_id": getattr(message, "message_thread_id", None),
        "telegram_message_id": message.message_id,
        "actor_tg_id": message.from_user.id if message.from_user else None,
        "media_kind": kind,
        "file_unique_id": media.file_unique_id if media else None,
        "source_edited_at": message.edit_date,
    }


async def _source_ref(session, source: dict):
    """Находит исходную ссылку в конкретном чате и боте."""
    return await session.scalar(
        select(TicketTelegramMessage).where(
            TicketTelegramMessage.bot_id == source["bot_id"],
            TicketTelegramMessage.chat_id == source["chat_id"],
            TicketTelegramMessage.telegram_message_id == source["telegram_message_id"],
            TicketTelegramMessage.source.is_(True),
        )
    )


async def find_source_message(session, source: dict):
    """Предотвращает повторную запись одного Telegram-события."""
    ref = await _source_ref(session, source)
    return await session.get(TicketMessage, ref.ticket_message_id) if ref else None


async def register_source(session, msg: TicketMessage, source: dict):
    """Привязывает исходное Telegram-сообщение к общей переписке."""
    existing = await _source_ref(session, source)
    if existing:
        if existing.ticket_message_id != msg.id:
            raise ValueError("Сообщение Telegram уже зарегистрировано")
        return existing
    ref = TicketTelegramMessage(ticket_message_id=msg.id, source=True, **source)
    session.add(ref)
    await session.flush()
    return ref


def _local_upload(url: str) -> Path | None:
    """Разрешает только сохранённое вложение без перехода по каталогам."""
    prefix = "/api/web/uploads/"
    if not isinstance(url, str) or not url.startswith(prefix):
        return None
    name = url[len(prefix) :]
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    return Path("static/web_uploads") / name


def _media_kind(path: Path) -> str:
    """Определяет способ отправки сохранённого вложения."""
    if path.suffix.lower() == ".gif" or path.name.lower().endswith("_animation.mp4"):
        return "animation"
    return "photo" if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} else "document"


async def _delete_ref(bot, ref) -> None:
    """Удаляет известную копию и учитывает уже выполненное удаление."""
    if ref.deleted:
        return
    try:
        await bot.delete_message(ref.chat_id, ref.telegram_message_id)
    except TelegramBadRequest as error:
        if "message to delete not found" not in str(error).lower():
            raise
    ref.deleted = True
    ref.last_error = None


async def send_message_parts(
    session,
    msg: TicketMessage,
    *,
    chat_id: int,
    topic_id: int | None = None,
    include_attachments: bool = True,
    one_part: bool = False,
) -> bool:
    """Доставляет и обновляет известные части сообщения без повторной отправки."""
    from .delivery import support_bot_instance

    bot = support_bot_instance()
    if bot is None:
        raise RuntimeError("Бот поддержки недоступен")
    refs = list(
        (
            await session.scalars(
                select(TicketTelegramMessage).where(
                    TicketTelegramMessage.ticket_message_id == msg.id,
                    TicketTelegramMessage.bot_id == bot.id,
                    TicketTelegramMessage.chat_id == chat_id,
                    TicketTelegramMessage.topic_id == topic_id,
                    TicketTelegramMessage.source.is_(False),
                    TicketTelegramMessage.deleted.is_(False),
                )
            )
        ).all()
    )
    body = msg.body or ""
    if topic_id and msg.author in {"agent", "note"} and body:
        body = ("🔒 Заметка:\n" if msg.author == "note" else "🛟 Оператор:\n") + body
    parts = {-1: ("text", body)} if body else {}
    if include_attachments:
        for index, url in enumerate(msg.attachments or []):
            path = _local_upload(url)
            if path is None or not path.is_file():
                raise ValueError("Файл вложения поддержки недоступен")
            parts[index] = (_media_kind(path), path)
    if msg.deleted_at is not None:
        parts = {}
    for ref in refs:
        if ref.part_index not in parts and (include_attachments or ref.part_index == -1):
            await _delete_ref(bot, ref)
            await session.flush()
            if one_part:
                return True
    for index, (kind, value) in parts.items():
        ref = next((item for item in refs if item.part_index == index and not item.deleted), None)
        if ref is not None and ref.applied_revision == msg.revision:
            continue
        kwargs = {"chat_id": chat_id, "parse_mode": None}
        if ref is not None:
            try:
                if kind == "text":
                    await bot.edit_message_text(text=value, message_id=ref.telegram_message_id, **kwargs)
                else:
                    cls = {"photo": InputMediaPhoto, "animation": InputMediaAnimation, "document": InputMediaDocument}[
                        kind
                    ]
                    await bot.edit_message_media(
                        chat_id=chat_id,
                        message_id=ref.telegram_message_id,
                        media=cls(media=FSInputFile(value), parse_mode=None),
                    )
            except TelegramBadRequest as error:
                text = str(error).lower()
                if "message is not modified" not in text:
                    if "message to edit not found" not in text:
                        raise
                    ref.deleted = True
                    ref = None
        if ref is None:
            if topic_id:
                kwargs["message_thread_id"] = topic_id
            if kind == "text":
                sent = await bot.send_message(text=value, **kwargs)
            else:
                send = {"photo": bot.send_photo, "animation": bot.send_animation, "document": bot.send_document}[kind]
                sent = await send(**{kind: FSInputFile(value)}, **kwargs)
            ref = TicketTelegramMessage(
                ticket_message_id=msg.id,
                bot_id=bot.id,
                chat_id=chat_id,
                topic_id=topic_id,
                telegram_message_id=sent.message_id,
                source=False,
                media_kind=kind,
                part_index=index,
            )
            session.add(ref)
        ref.applied_revision = msg.revision
        ref.media_kind = kind
        ref.last_error = None
        await session.flush()
        if one_part:
            return True
    return False


async def sync_message(session, msg: TicketMessage) -> None:
    """Сохраняет синхронизацию копий в транзакции изменения сообщения."""
    from .queue import queue_sync

    await queue_sync(session, msg)


async def _actor_allowed(session, ticket, msg, actor: int, *, forum: bool, chat_id: int) -> bool:
    """Проверяет автора перед изменением Telegram-сообщения."""
    if forum:
        from .forum import forum_chat_id

        return chat_id == forum_chat_id() and msg.author in {"agent", "note"} and msg.agent_tg_id == actor
    client = await session.get(Identity, ticket.identity_id)
    return msg.author == "client" and client is not None and client.tg_id == actor and chat_id == actor


def _utc(value):
    """Нормализует время события Telegram для сравнения."""
    return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value


async def apply_telegram_edit(message, *, forum: bool) -> bool:
    """Применяет правку известного оригинала и обновляет копии."""
    from handlers.support_bot.media import collect_attachment

    from .messages import edit_message

    source = source_from_message(message)
    async with async_session_maker() as session:
        ref = await _source_ref(session, source)
        if ref is None or ref.deleted or ref.actor_tg_id != source["actor_tg_id"]:
            return False
        msg = await session.get(TicketMessage, ref.ticket_message_id)
        ticket = (
            await session.scalar(
                select(Ticket)
                .where(Ticket.id == msg.ticket_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if msg
            else None
        )
        if ticket is not None:
            msg = await session.scalar(
                select(TicketMessage)
                .where(TicketMessage.id == ref.ticket_message_id)
                .execution_options(populate_existing=True)
            )
        if msg is None or msg.deleted_at is not None:
            return False
        if ticket is None or not await _actor_allowed(
            session, ticket, msg, source["actor_tg_id"], forum=forum, chat_id=source["chat_id"]
        ):
            return False
        ref = await session.scalar(
            select(TicketTelegramMessage)
            .where(TicketTelegramMessage.id == ref.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            ref.source_edited_at
            and source["source_edited_at"]
            and _utc(source["source_edited_at"]) <= _utc(ref.source_edited_at)
        ):
            return False
        attachments = None
        if source["file_unique_id"] != ref.file_unique_id:
            _, attachments = await collect_attachment(message)
            if source["file_unique_id"] and not attachments:
                return False
            attachments = attachments or []
        msg = await edit_message(
            session,
            ticket_id=ticket.id,
            message_id=msg.id,
            body=(message.text or message.caption or "")[:4000],
            attachments=attachments,
        )
        ref.source_edited_at = source["source_edited_at"] or datetime.now(UTC)
        ref.file_unique_id = source["file_unique_id"]
        ref.media_kind = source["media_kind"]
        ref.applied_revision = msg.revision
        await sync_message(session, msg)
        await session.commit()
        return True


async def request_delete(message, *, forum: bool) -> None:
    """Запрашивает подтверждение удаления сообщения из переписки."""
    from settings.buttons import SUPPORT_MESSAGE_DELETE_BUTTON, SUPPORT_MESSAGE_DELETE_CANCEL
    from settings.texts import (
        SUPPORT_MESSAGE_DELETE_CONFIRM,
        SUPPORT_MESSAGE_DELETE_FORBIDDEN,
        SUPPORT_MESSAGE_NOT_FOUND,
    )

    replied = message.reply_to_message
    if replied is None:
        await message.answer(SUPPORT_MESSAGE_NOT_FOUND)
        return
    source = {"bot_id": message.bot.id, "chat_id": message.chat.id, "telegram_message_id": replied.message_id}
    async with async_session_maker() as session:
        ref = await session.scalar(
            select(TicketTelegramMessage).where(
                TicketTelegramMessage.bot_id == source["bot_id"],
                TicketTelegramMessage.chat_id == source["chat_id"],
                TicketTelegramMessage.telegram_message_id == source["telegram_message_id"],
            )
        )
        msg = await session.get(TicketMessage, ref.ticket_message_id) if ref else None
        ticket = await session.get(Ticket, msg.ticket_id) if msg else None
        if ticket is None or msg.deleted_at is not None:
            await message.answer(SUPPORT_MESSAGE_NOT_FOUND)
            return
        if not await _actor_allowed(session, ticket, msg, message.from_user.id, forum=forum, chat_id=message.chat.id):
            await message.answer(SUPPORT_MESSAGE_DELETE_FORBIDDEN)
            return
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=SUPPORT_MESSAGE_DELETE_BUTTON, callback_data=f"tm:delete:{msg.id}:{msg.revision}"
                    ),
                    InlineKeyboardButton(text=SUPPORT_MESSAGE_DELETE_CANCEL, callback_data="tm:cancel"),
                ]
            ]
        )
        await message.answer(SUPPORT_MESSAGE_DELETE_CONFIRM, reply_markup=kb)


async def confirm_delete(callback, *, forum: bool) -> None:
    """Удаляет подтверждённое сообщение с повторной проверкой владельца."""
    from settings.texts import SUPPORT_MESSAGE_DELETE_DONE, SUPPORT_MESSAGE_DELETE_FORBIDDEN, SUPPORT_MESSAGE_NOT_FOUND

    from .messages import delete_message

    if callback.data == "tm:cancel":
        await callback.message.delete()
        await callback.answer()
        return
    fields = (callback.data or "").split(":")
    if len(fields) != 4 or not fields[3].isdigit():
        await callback.answer(SUPPORT_MESSAGE_NOT_FOUND, show_alert=True)
        return
    async with async_session_maker() as session:
        msg = await session.get(TicketMessage, fields[2])
        ticket = (
            await session.scalar(
                select(Ticket)
                .where(Ticket.id == msg.ticket_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if msg
            else None
        )
        if ticket is None or not await _actor_allowed(
            session, ticket, msg, callback.from_user.id, forum=forum, chat_id=callback.message.chat.id
        ):
            await callback.answer(SUPPORT_MESSAGE_DELETE_FORBIDDEN, show_alert=True)
            return
        try:
            msg = await delete_message(
                session, ticket_id=ticket.id, message_id=msg.id, expected_revision=int(fields[3])
            )
        except ValueError:
            await callback.answer(SUPPORT_MESSAGE_NOT_FOUND, show_alert=True)
            return
        await sync_message(session, msg)
        await session.commit()
    await callback.message.edit_text(SUPPORT_MESSAGE_DELETE_DONE, reply_markup=None)
    await callback.answer()

from pathlib import Path

from aiogram.types import Message

from utils.web_media import MAX_UPLOAD_BYTES, host_telegram_animation, host_telegram_document, host_telegram_photo


class AttachmentUnavailableError(Exception):
    pass


async def collect_required_attachment(message: Message) -> tuple[str, list[str] | None]:
    """Отклоняет сообщение, если его вложение не удалось сохранить."""
    body, attachments = await collect_attachment(message)
    if not attachments and any(getattr(message, kind, None) for kind in ("animation", "photo", "document")):
        raise AttachmentUnavailableError
    return body, attachments


async def collect_attachment(message: Message) -> tuple[str, list[str] | None]:
    body = (message.text or message.caption or "").strip()

    animation = message.animation
    if animation is not None:
        if animation.file_size and int(animation.file_size) > MAX_UPLOAD_BYTES:
            return body, None
        url = await host_telegram_animation(message.bot, animation.file_id)
        return body, ([url] if url else None)

    if message.photo:
        photo = message.photo[-1]
        if photo.file_size and int(photo.file_size) > MAX_UPLOAD_BYTES:
            return body, None
        url = await host_telegram_photo(message.bot, photo.file_id)
        return body, ([url] if url else None)

    doc = message.document
    if doc is not None:
        if doc.file_size and int(doc.file_size) > MAX_UPLOAD_BYTES:
            return body, None
        mime = (doc.mime_type or "").lower()
        if mime == "image/gif" or Path(doc.file_name or "").suffix.lower() == ".gif":
            url = await host_telegram_animation(message.bot, doc.file_id)
        elif mime.startswith("image/"):
            url = await host_telegram_photo(message.bot, doc.file_id)
        else:
            url = await host_telegram_document(message.bot, doc.file_id, doc.file_name)
        return body, ([url] if url else None)

    return body, None

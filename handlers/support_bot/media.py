from aiogram.types import Message

from utils.web_media import MAX_UPLOAD_BYTES, host_telegram_document, host_telegram_photo


async def collect_attachment(message: Message) -> tuple[str, list[str] | None]:
    body = (message.text or message.caption or "").strip()

    if message.photo:
        photo = message.photo[-1]
        if photo.file_size and int(photo.file_size) > MAX_UPLOAD_BYTES:
            return body, None
        url = await host_telegram_photo(message.bot, photo.file_id)
        return body, ([url] if url else None)

    doc = message.document
    if doc is not None:
        mime = (doc.mime_type or "").lower()
        if mime.startswith("image/"):
            url = await host_telegram_photo(message.bot, doc.file_id)
        else:
            url = await host_telegram_document(message.bot, doc.file_id, doc.file_name)
        return body, ([url] if url else None)

    return body, None

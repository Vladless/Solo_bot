import asyncio
import io
import uuid

from pathlib import Path

from core.executor import run_io
from logger import logger


WEB_UPLOAD_DIR = Path("static/web_uploads")
_ALLOWED_IMAGE_EXT = frozenset({".jpg", ".jpeg", ".png", ".webp"})
_ALLOWED_DOC_EXT = frozenset({
    ".pdf",
    ".txt",
    ".log",
    ".json",
    ".csv",
    ".zip",
    ".doc",
    ".docx",
    ".xlsx",
    ".conf",
    ".yml",
    ".yaml",
})
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class _BoundedUpload(io.BytesIO):
    def write(self, data):
        """Останавливает загрузку до превышения лимита памяти."""
        if self.tell() + len(data) > MAX_UPLOAD_BYTES:
            raise ValueError("Превышен размер вложения")
        return super().write(data)


async def host_telegram_animation(bot, file_id: str | None) -> str | None:
    """Сохраняет GIF или MP4-анимацию без преобразования кадров."""
    if not file_id:
        return None
    try:
        async with asyncio.timeout(30):
            tg_file = await bot.get_file(file_id)
            if tg_file.file_size and int(tg_file.file_size) > MAX_UPLOAD_BYTES:
                return None
            file_path = tg_file.file_path or ""
            if not file_path:
                return None
            with _BoundedUpload() as buffer:
                await bot.download_file(file_path, destination=buffer, timeout=30)
                data = buffer.getvalue()
        if len(data) >= 13 and data[:6] in (b"GIF87a", b"GIF89a"):
            suffix = ".gif"
        elif len(data) >= 12 and data[4:8] == b"ftyp":
            suffix = "_animation.mp4"
        else:
            return None
        WEB_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex}{suffix}"
        await run_io((WEB_UPLOAD_DIR / name).write_bytes, data)
        return f"/api/web/uploads/{name}"
    except Exception as error:
        logger.warning("[Site:Files] Не удалось сохранить анимацию поддержки: {}", type(error).__name__)
        return None


async def host_telegram_photo(bot, file_id: str | None) -> str | None:
    if not file_id:
        return None
    try:
        tg_file = await bot.get_file(file_id)
        file_path = tg_file.file_path or ""
        ext = Path(file_path).suffix.lower()
        if ext not in _ALLOWED_IMAGE_EXT:
            ext = ".jpg"

        buffer = io.BytesIO()
        await bot.download_file(file_path, destination=buffer)
        data = buffer.getvalue()
        if not data:
            return None

        try:
            from core.executor import run_cpu
            from utils.cpu_tasks import optimize_image_bytes

            data = await run_cpu(optimize_image_bytes, data, ext)
        except Exception:
            pass

        WEB_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex}{ext}"
        await run_io((WEB_UPLOAD_DIR / name).write_bytes, data)
        return f"/api/web/uploads/{name}"
    except Exception as e:
        logger.warning(f"[Site:Files] Не удалось разместить медиа рассылки на сайте: {e}")
        return None


async def host_telegram_document(bot, file_id: str | None, file_name: str | None = None) -> str | None:
    if not file_id:
        return None
    try:
        ext = Path(file_name or "").suffix.lower()
        if ext not in _ALLOWED_DOC_EXT:
            return None
        tg_file = await bot.get_file(file_id)
        if tg_file.file_size and int(tg_file.file_size) > MAX_UPLOAD_BYTES:
            return None
        buffer = io.BytesIO()
        await bot.download_file(tg_file.file_path or "", destination=buffer)
        data = buffer.getvalue()
        if not data or len(data) > MAX_UPLOAD_BYTES:
            return None
        WEB_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex}{ext}"
        await run_io((WEB_UPLOAD_DIR / name).write_bytes, data)
        return f"/api/web/uploads/{name}"
    except Exception as e:
        logger.warning(f"[Site:Files] Не удалось разместить документ на сайте: {e}")
        return None

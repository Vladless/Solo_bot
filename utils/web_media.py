import io
import uuid

from pathlib import Path

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
            from api.v2.routes.web import _optimize_image_bytes
            from core.executor import run_cpu

            data = await run_cpu(_optimize_image_bytes, data, ext)
        except Exception:
            pass

        WEB_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex}{ext}"
        with open(WEB_UPLOAD_DIR / name, "wb") as f:
            f.write(data)
        return f"/api/web/uploads/{name}"
    except Exception as e:
        logger.warning(f"[WebMedia] Не удалось разместить медиа рассылки на сайте: {e}")
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
        with open(WEB_UPLOAD_DIR / name, "wb") as f:
            f.write(data)
        return f"/api/web/uploads/{name}"
    except Exception as e:
        logger.warning(f"[WebMedia] Не удалось разместить документ на сайте: {e}")
        return None

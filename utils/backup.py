import os
import subprocess
import tarfile

from datetime import datetime, timedelta
from pathlib import Path

import aiofiles

from aiogram import Bot
from aiogram.types import BufferedInputFile

from bot import bot
from config import (
    ADMIN_ID,
    BACKUP_CAPTION,
    BACKUP_CHANNEL_ID,
    BACKUP_CHANNEL_THREAD_ID,
    BACKUP_OTHER_BOT_TOKEN,
    BACKUP_SEND_MODE,
    BACK_DIR,
    DB_NAME,
    DB_PASSWORD,
    DB_USER,
    PG_HOST,
    PG_PORT,
    BACKUP_CREATE_ARCHIVE,
    BACKUP_INCLUDE_DB,
    BACKUP_INCLUDE_CONFIG,
    BACKUP_INCLUDE_TEXTS,
    BACKUP_INCLUDE_IMG,
)
from logger import logger


async def backup_database() -> Exception | None:
    """
    Создает резервную копию базы данных (или полный архив) и отправляет его администраторам.

    Returns:
        Optional[Exception]: Исключение в случае ошибки или None при успешном выполнении
    """
    if BACKUP_CREATE_ARCHIVE:
        if not any([BACKUP_INCLUDE_DB, BACKUP_INCLUDE_CONFIG, BACKUP_INCLUDE_TEXTS, BACKUP_INCLUDE_IMG]):
            backup_file_path, exception = _create_database_backup()
        else:
            backup_file_path, exception = _create_backup_archive()
    else:
        backup_file_path, exception = _create_database_backup()

    if exception:
        logger.error(f"Ошибка при создании бэкапа: {exception}")
        return exception

    try:
        await _send_backup_to_admins(backup_file_path)
        exception = _cleanup_old_backups()

        if exception:
            logger.error(f"Ошибка при удалении старых бэкапов: {exception}")
            return exception

        return None
    except Exception as e:
        logger.error(f"Ошибка при отправке бэкапа: {e}")
        return e


def _create_database_backup() -> tuple[str | None, Exception | None]:
    """
    Создает резервную копию базы данных PostgreSQL.

    Returns:
        Tuple[Optional[str], Optional[Exception]]: Путь к файлу бэкапа и исключение (если произошла ошибка)
    """
    date_formatted = datetime.now().strftime("%Y-%m-%d-%H%M%S")

    backup_dir = Path(BACK_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True)

    filename = backup_dir / f"{DB_NAME}-backup-{date_formatted}.sql"

    try:
        os.environ["PGPASSWORD"] = DB_PASSWORD

        subprocess.run(
            [
                "pg_dump",
                "-U",
                DB_USER,
                "-h",
                PG_HOST,
                "-p",
                PG_PORT,
                "-F",
                "c",
                "-f",
                str(filename),
                DB_NAME,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        logger.info(f"Бэкап базы данных создан: {filename}")
        return str(filename), None
    except subprocess.CalledProcessError as e:
        logger.error(f"Ошибка при выполнении pg_dump: {e.stderr}")
        return None, e
    except Exception as e:
        logger.error(f"Непредвиденная ошибка при создании бэкапа: {e}")
        return None, e
    finally:
        if "PGPASSWORD" in os.environ:
            del os.environ["PGPASSWORD"]


def _create_backup_archive() -> tuple[str | None, Exception | None]:
    """
    Создает архив (.tar.gz) с выбранными компонентами бекапа.

    Returns:
        Tuple[Optional[str], Optional[Exception]]: Путь к файлу архива и исключение (если произошла ошибка)
    """
    date_formatted = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    backup_dir = Path(BACK_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True)

    archive_path = backup_dir / f"{DB_NAME}-full-backup-{date_formatted}.tar.gz"
    project_root = Path(__file__).parent.parent
    archive_folder = f"backup-{date_formatted}"

    db_backup_path = None
    try:
        with tarfile.open(archive_path, "w:gz") as tar:
            if BACKUP_INCLUDE_DB:
                db_backup_path, db_exception = _create_database_backup()
                if db_exception:
                    logger.warning(f"Не удалось создать бекап БД для архива: {db_exception}")
                elif db_backup_path and os.path.exists(db_backup_path):
                    tar.add(db_backup_path, arcname=f"{archive_folder}/database.sql")
                    logger.info("База данных добавлена в архив")

            if BACKUP_INCLUDE_CONFIG:
                config_path = project_root / "config.py"
                if config_path.exists():
                    tar.add(config_path, arcname=f"{archive_folder}/config.py")
                    logger.info("config.py добавлен в архив")
                else:
                    logger.warning("config.py не найден, пропущен")

            if BACKUP_INCLUDE_TEXTS:
                texts_path = project_root / "handlers" / "texts.py"
                if texts_path.exists():
                    tar.add(texts_path, arcname=f"{archive_folder}/texts.py")
                    logger.info("texts.py добавлен в архив")
                else:
                    logger.warning("handlers/texts.py не найден, пропущен")

            if BACKUP_INCLUDE_IMG:
                img_dir = project_root / "img"
                if img_dir.exists() and img_dir.is_dir():
                    img_files = [f for f in img_dir.iterdir() if f.is_file()]
                    for img_file in img_files:
                        tar.add(img_file, arcname=f"{archive_folder}/img/{img_file.name}")
                    logger.info(f"Папка img/ добавлена в архив ({len(img_files)} файлов)")
                else:
                    logger.warning("Папка img/ не найдена, пропущена")

        logger.info(f"Архив бекапа создан: {archive_path}")

        if db_backup_path and os.path.exists(db_backup_path) and db_backup_path != str(archive_path):
            try:
                os.unlink(db_backup_path)
                logger.info(f"Временный файл БД удален: {db_backup_path}")
            except Exception as e:
                logger.warning(f"Не удалось удалить временный файл БД: {e}")

        return str(archive_path), None

    except Exception as e:
        logger.error(f"Непредвиденная ошибка при создании архива бекапа: {e}")
        return None, e


def _cleanup_old_backups() -> Exception | None:
    """
    Удаляет бэкапы старше 3 дней (как .sql, так и .tar.gz файлы).

    Returns:
        Optional[Exception]: Исключение в случае ошибки или None при успешном выполнении
    """
    try:
        backup_dir = Path(BACK_DIR)
        if not backup_dir.exists():
            return None

        cutoff_date = datetime.now() - timedelta(days=3)

        for backup_file in backup_dir.glob("*.sql"):
            if backup_file.is_file():
                file_mtime = datetime.fromtimestamp(backup_file.stat().st_mtime)
                if file_mtime < cutoff_date:
                    backup_file.unlink()
                    logger.info(f"Удален старый бэкап: {backup_file}")

        for archive_file in backup_dir.glob("*.tar.gz"):
            if archive_file.is_file():
                file_mtime = datetime.fromtimestamp(archive_file.stat().st_mtime)
                if file_mtime < cutoff_date:
                    archive_file.unlink()
                    logger.info(f"Удален старый архив: {archive_file}")

        logger.info("Очистка старых бэкапов завершена")
        return None
    except Exception as e:
        logger.error(f"Ошибка при удалении старых бэкапов: {e}")
        return e


async def create_backup_and_send_to_admins(client) -> None:
    """
    Создает бэкап и отправляет администраторам через переданный клиент.

    Args:
        client: Клиент для работы с базой данных
    """
    await client.login()
    await client.database.export()


async def _send_backup_to_admins(backup_file_path: str) -> None:
    """
    Отправляет файл бэкапа всем администраторам через Telegram.

    Args:
        backup_file_path: Путь к файлу бэкапа

    Raises:
        Exception: При ошибке отправки файла
    """
    if not backup_file_path or not os.path.exists(backup_file_path):
        raise FileNotFoundError(f"Файл бэкапа не найден: {backup_file_path}")

    async def send_default():
        for admin_id in ADMIN_ID:
            try:
                await bot.send_document(chat_id=admin_id, document=backup_input_file)
                logger.info(f"Бэкап базы данных отправлен админу: {admin_id}")
            except Exception as e:
                logger.error(f"Не удалось отправить бэкап админу {admin_id}: {e}")

    try:
        async with aiofiles.open(backup_file_path, "rb") as backup_file:
            backup_data = await backup_file.read()
            filename = os.path.basename(backup_file_path)
            backup_input_file = BufferedInputFile(file=backup_data, filename=filename)

            if BACKUP_SEND_MODE == "default":
                await send_default()

            elif BACKUP_SEND_MODE == "channel":
                channel_id = BACKUP_CHANNEL_ID.strip()
                thread_id = BACKUP_CHANNEL_THREAD_ID.strip()
                if not channel_id:
                    logger.error("BACKUP_CHANNEL_ID не задан для режима 'channel', fallback на default")
                    await send_default()
                    return
                send_kwargs = {"chat_id": channel_id, "document": backup_input_file}
                if thread_id:
                    send_kwargs["message_thread_id"] = int(thread_id)
                if BACKUP_CAPTION:
                    send_kwargs["caption"] = BACKUP_CAPTION
                try:
                    await bot.send_document(**send_kwargs)
                    logger.info(f"Бэкап базы данных отправлен в канал: {channel_id} (топик: {thread_id})")
                except Exception as e:
                    logger.error(f"Не удалось отправить бэкап в канал {channel_id}: {e}, fallback на default")
                    await send_default()

            elif BACKUP_SEND_MODE == "bot":
                if not BACKUP_OTHER_BOT_TOKEN:
                    logger.error("BACKUP_OTHER_BOT_TOKEN не задан для режима 'bot', fallback на default")
                    await send_default()
                    return
                other_bot = Bot(token=BACKUP_OTHER_BOT_TOKEN)
                try:
                    for admin_id in ADMIN_ID:
                        try:
                            send_kwargs = {"chat_id": admin_id, "document": backup_input_file}
                            if BACKUP_CAPTION:
                                send_kwargs["caption"] = BACKUP_CAPTION
                            await other_bot.send_document(**send_kwargs)
                            logger.info(f"Бэкап базы данных отправлен админу через другого бота: {admin_id}")
                        except Exception as e:
                            logger.error(f"Не удалось отправить бэкап админу {admin_id} через другого бота: {e}")
                    await other_bot.session.close()
                except Exception as e:
                    logger.error(f"Ошибка при отправке через другого бота: {e}, fallback на default")
                    await send_default()
            else:
                logger.error(f"Неизвестный BACKUP_SEND_MODE: {BACKUP_SEND_MODE}, fallback на default")
                await send_default()
    except Exception as e:
        logger.error(f"Ошибка при отправке бэкапа: {e}")
        raise

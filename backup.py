import os
import subprocess

from datetime import datetime, timedelta
from pathlib import Path

import aiofiles

from aiogram.types import BufferedInputFile

from bot import bot
from config import ADMIN_ID, BACK_DIR, DB_NAME, DB_PASSWORD, DB_USER, PG_HOST, PG_PORT
from logger import logger


async def backup_database() -> Exception | None:
    """
    Создает резервную копию базы данных и отправляет ее администраторам.

    Returns:
        Optional[Exception]: Исключение в случае ошибки или None при успешном выполнении
    """
    backup_file_path, exception = _create_database_backup()

    if exception:
        logger.error(f"Ошибка при создании бэкапа базы данных: {exception}")
        return exception

    try:
        await _send_backup_to_admins(backup_file_path)
        exception = _cleanup_old_backups()

        if exception:
            logger.error(f"Ошибка при удалении старых бэкапов базы данных: {exception}")
            return exception

        return None
    except Exception as e:
        logger.error(f"Ошибка при отправке бэкапа базы данных: {e}")
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


def _cleanup_old_backups() -> Exception | None:
    """
    Удаляет бэкапы старше 3 дней.

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

    try:
        async with aiofiles.open(backup_file_path, "rb") as backup_file:
            backup_data = await backup_file.read()
            filename = os.path.basename(backup_file_path)
            backup_input_file = BufferedInputFile(file=backup_data, filename=filename)

            for admin_id in ADMIN_ID:
                try:
                    await bot.send_document(chat_id=admin_id, document=backup_input_file)
                    logger.info(f"Бэкап базы данных отправлен админу: {admin_id}")
                except Exception as e:
                    logger.error(f"Не удалось отправить бэкап админу {admin_id}: {e}")
    except Exception as e:
        logger.error(f"Ошибка при отправке бэкапа в Telegram: {e}")
        raise

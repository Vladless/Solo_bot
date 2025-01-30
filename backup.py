import os
import subprocess
from datetime import datetime

from aiogram.types import BufferedInputFile

from config import ADMIN_ID, BACK_DIR, DB_NAME, DB_PASSWORD, DB_USER, PG_HOST, PG_PORT
from logger import logger


async def backup_database() -> Exception | None:
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
    date_formatted = datetime.now().strftime("%Y-%m-%d-%H%M%S")

    if not os.path.exists(BACK_DIR):
        os.makedirs(BACK_DIR)

    filename = os.path.join(BACK_DIR, f"{DB_NAME}-backup-{date_formatted}.sql")

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
                filename,
                DB_NAME,
            ],
            check=True,
        )
        logger.info(f"Бэкап базы данных создан: {filename}")
        return filename, None
    except subprocess.CalledProcessError as e:
        return None, e
    finally:
        del os.environ["PGPASSWORD"]


def _cleanup_old_backups() -> None | Exception:
    try:
        subprocess.run(
            [
                "find",
                BACK_DIR,
                "-type",
                "f",
                "-name",
                "*.sql",
                "-mtime",
                "+3",
                "-exec",
                "rm",
                "{}",
                ";",
            ],
            check=True,
        )
        logger.info("Старые бэкапы удалены.")
        return None
    except subprocess.CalledProcessError as e:
        return e


async def create_backup_and_send_to_admins(xui) -> None:
    await xui.login()
    await xui.database.export()


async def _send_backup_to_admins(backup_file_path: str) -> None:
    try:
        import aiofiles

        from bot import bot

        async with aiofiles.open(backup_file_path, "rb") as backup_file:
            backup_data = await backup_file.read()
            backup_input_file = BufferedInputFile(file=backup_data, filename=os.path.basename(backup_file_path))
            admin_ids = ADMIN_ID if isinstance(ADMIN_ID, list) else [ADMIN_ID]
            for admin_id in admin_ids:
                await bot.send_document(chat_id=admin_id, document=backup_input_file)
            logger.info(f"Бэкап базы данных отправлен админу: {admin_id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке бэкапа в Telegram: {e}")

import os
import subprocess
from datetime import datetime

from aiogram.types import BufferedInputFile
from config import ADMIN_ID, BACK_DIR, DB_NAME, DB_PASSWORD, DB_USER

from logger import logger


async def backup_database():
    from bot import bot

    try:
        if backup_file_path := _create_database_backup():
            await _send_backup_to_admin(bot, backup_file_path)
            _cleanup_old_backups()
    except Exception as e:
        logger.error(f"Ошибка при создании или отправке бэкапа: {e}")


def _create_database_backup():
    USER = DB_USER
    HOST = "localhost"
    BACKUP_DIR = BACK_DIR
    DATE = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    BACKUP_FILE = f"{BACKUP_DIR}/{DB_NAME}-backup-{DATE}.sql"

    os.environ["PGPASSWORD"] = DB_PASSWORD

    try:
        subprocess.run(
            [
                "pg_dump",
                "-U",
                USER,
                "-h",
                HOST,
                "-F",
                "c",
                "-f",
                BACKUP_FILE,
                DB_NAME,
            ],
            check=True,
        )
        logger.info(f"Бэкап базы данных создан: {BACKUP_FILE}")
        return BACKUP_FILE
    except subprocess.CalledProcessError as e:
        logger.error(f"Ошибка при создании бэкапа базы данных: {e}")
        return None
    finally:
        del os.environ["PGPASSWORD"]


async def _send_backup_to_admin(bot, backup_file_path):
    try:
        with open(backup_file_path, "rb") as backup_file:
            backup_input_file = BufferedInputFile(
                backup_file.read(), filename=os.path.basename(backup_file_path)
            )
            admin_ids: int | list[int] = ADMIN_ID
            if isinstance(admin_ids, list):
                for id in admin_ids:
                    await bot.send_document(id, backup_input_file)
                    logger.info(f"Бэкап базы данных отправлен админу: {id}")
            else:
                await bot.send_document(admin_ids, backup_input_file)
                logger.info(f"Бэкап базы данных отправлен админу: {ADMIN_ID}")
    except Exception as e:
        logger.error(f"Ошибка при отправке бэкапа в Telegram: {e}")


def _cleanup_old_backups():
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
    except subprocess.CalledProcessError as e:
        logger.error(f"Ошибка при удалении старых бэкапов: {e}")


async def create_backup_and_send_to_admins(xui):
    await xui.login()
    await xui.database.export()

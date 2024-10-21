import subprocess
import os
import logging
from datetime import datetime
from aiogram.types import BufferedInputFile
from bot import bot
from config import ADMIN_ID, DB_USER, DB_NAME

async def backup_database():
    # Параметры бэкапа
    USER = DB_USER
    HOST = "localhost"
    BACKUP_DIR = "/home/vlad"
    DATE = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    BACKUP_FILE = f"{BACKUP_DIR}/{DB_NAME}-backup-{DATE}.sql"

    # Команда для резервного копирования
    try:
        subprocess.run(
            ['pg_dump', '-U', USER, '-h', HOST, '-F', 'c', '-f', BACKUP_FILE, DB_NAME],
            check=True
        )
        logging.info(f"Бэкап базы данных создан: {BACKUP_FILE}")
    except subprocess.CalledProcessError as e:
        logging.error(f"Ошибка при создании бэкапа базы данных: {e}")
        return

    # Отправка файла бэкапа в чат админа
    try:
        with open(BACKUP_FILE, 'rb') as backup_file:
            backup_input_file = BufferedInputFile(backup_file.read(), filename=os.path.basename(BACKUP_FILE))
            await bot.send_document(ADMIN_ID, backup_input_file)
        logging.info(f"Бэкап базы данных отправлен админу: {ADMIN_ID}")
    except Exception as e:
        logging.error(f"Ошибка при отправке бэкапа в Telegram: {e}")

    # Удаление старых копий (старше 7 дней)
    try:
        subprocess.run(
            ['find', BACKUP_DIR, '-type', 'f', '-name', '*.sql', '-mtime', '+7', '-exec', 'rm', '{}', ';'],
            check=True
        )
        logging.info("Старые бэкапы удалены.")
    except subprocess.CalledProcessError as e:
        logging.error(f"Ошибка при удалении старых бэкапов: {e}")

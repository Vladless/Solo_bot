import os
import shutil
import subprocess
import tarfile

from datetime import datetime, timedelta
from pathlib import Path

import aiofiles

from aiogram import Bot
from aiogram.types import BufferedInputFile

from logger import logger
from settings.config import (
    ADMIN_ID,
    BACKUP_CAPTION,
    BACKUP_CREATE_ARCHIVE,
    BACKUP_DESTINATION,
    BACKUP_INCLUDE_CONFIG,
    BACKUP_INCLUDE_DB,
    BACKUP_INCLUDE_IMG,
    BACKUP_INCLUDE_TEXTS,
    BACKUP_OTHER_BOT_TOKEN,
    BACKUP_S3_ACCESS_KEY,
    BACKUP_S3_BUCKET,
    BACKUP_S3_ENDPOINT,
    BACKUP_S3_KEEP,
    BACKUP_S3_PATH,
    BACKUP_S3_REGION,
    BACKUP_S3_SECRET_KEY,
    BACK_DIR,
    DB_NAME,
    DB_PASSWORD,
    DB_USER,
    PG_HOST,
    PG_IN_DOCKER,
    PG_PORT,
)


DOCKER_POSTGRES_CONTAINER = "solobot-postgres"


def _s3_configured() -> bool:
    return bool(BACKUP_S3_ENDPOINT and BACKUP_S3_ACCESS_KEY and BACKUP_S3_SECRET_KEY and BACKUP_S3_BUCKET)


def _parse_destination() -> tuple[str | None, int | None]:
    """Парсит BACKUP_DESTINATION в (chat_id, thread_id)."""
    raw = BACKUP_DESTINATION.strip()
    if not raw:
        return None, None
    parts = raw.split(":", 1)
    chat_id = parts[0]
    thread_id = int(parts[1]) if len(parts) > 1 and parts[1].strip() else None
    return chat_id, thread_id


def _find_docker_postgres_container() -> str | None:
    if shutil.which("docker") is None:
        return None
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", DOCKER_POSTGRES_CONTAINER],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip().lower() == "true":
        return DOCKER_POSTGRES_CONTAINER
    return None


def _get_postgres_execution_target() -> tuple[str, str | None]:
    if PG_IN_DOCKER:
        container = _find_docker_postgres_container()
        if container:
            return "docker", container
        raise FileNotFoundError(
            f"PostgreSQL настроен на Docker, но контейнер '{DOCKER_POSTGRES_CONTAINER}' не найден или не запущен"
        )
    return "host", None


def _create_database_backup_via_docker(filename: Path, container: str) -> None:
    with open(filename, "wb") as dump_file:
        result = subprocess.run(
            [
                "docker",
                "exec",
                "-e",
                f"PGPASSWORD={DB_PASSWORD}",
                container,
                "pg_dump",
                "-U",
                DB_USER,
                "-h",
                "127.0.0.1",
                "-p",
                "5432",
                "-F",
                "c",
                DB_NAME,
            ],
            stdout=dump_file,
            stderr=subprocess.PIPE,
        )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, result.args, stderr=result.stderr)


async def backup_database(bot_instance: Bot | None = None) -> Exception | None:
    """
    Создает резервную копию и отправляет в S3 или Telegram.
    Блокирующие операции выполняются в пуле потоков/процессов.
    """
    from core.executor import run_io

    if BACKUP_CREATE_ARCHIVE:
        if not any([BACKUP_INCLUDE_DB, BACKUP_INCLUDE_CONFIG, BACKUP_INCLUDE_TEXTS, BACKUP_INCLUDE_IMG]):
            backup_file_path, exception = await run_io(_create_database_backup)
        else:
            backup_file_path, exception = await run_io(_create_backup_archive)
    else:
        backup_file_path, exception = await run_io(_create_database_backup)

    if exception:
        logger.error("[Backup] Ошибка при создании: {}", exception)
        return exception

    logger.info("[Backup] Файл создан: {}", backup_file_path)
    try:
        if _s3_configured():
            s3_err = await run_io(_upload_to_s3, backup_file_path)
            if s3_err:
                logger.error("[Backup] Ошибка S3: {}", s3_err)
                return s3_err
        else:
            await _send_backup_telegram(backup_file_path, bot_instance=bot_instance)

        exception = await run_io(_cleanup_old_backups)
        if exception:
            logger.error("[Backup] Ошибка при очистке старых: {}", exception)
            return exception

        return None
    except Exception as e:
        logger.error("[Backup] Ошибка при отправке: {}", e)
        return e


def _create_database_backup() -> tuple[str | None, Exception | None]:
    date_formatted = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    pid_suffix = os.getpid()

    backup_dir = Path(BACK_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True)

    filename = backup_dir / f"{DB_NAME}-backup-{date_formatted}-{pid_suffix}.sql"

    try:
        target, container = _get_postgres_execution_target()

        if target == "docker" and container:
            _create_database_backup_via_docker(filename, container)
            logger.info("[Backup] БД создана через Docker-контейнер {}: {}", container, filename)
        elif shutil.which("pg_dump") is not None:
            env = os.environ.copy()
            env["PGPASSWORD"] = DB_PASSWORD
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
                env=env,
            )
            logger.info("[Backup] БД создана через host pg_dump: {}", filename)
        else:
            raise FileNotFoundError("PostgreSQL недоступен: не найден контейнер и отсутствует host pg_dump")
        return str(filename), None
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode("utf-8", errors="replace") if isinstance(e.stderr, bytes) else e.stderr
        logger.error("[Backup] pg_dump: {}", stderr)
        return None, e
    except Exception as e:
        logger.error("[Backup] Непредвиденная ошибка: {}", e)
        return None, e


def _create_backup_archive() -> tuple[str | None, Exception | None]:
    date_formatted = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    pid_suffix = os.getpid()
    backup_dir = Path(BACK_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True)

    archive_path = backup_dir / f"{DB_NAME}-full-backup-{date_formatted}-{pid_suffix}.tar.gz"
    project_root = Path(__file__).parent.parent
    archive_folder = f"backup-{date_formatted}"

    db_backup_path = None
    try:
        with tarfile.open(archive_path, "w:gz") as tar:
            if BACKUP_INCLUDE_DB:
                db_backup_path, db_exception = _create_database_backup()
                if db_exception:
                    logger.warning("[Backup] БД для архива не создана: {}", db_exception)
                elif db_backup_path and os.path.exists(db_backup_path):
                    tar.add(db_backup_path, arcname=f"{archive_folder}/database.sql")
                    logger.info("[Backup] БД добавлена в архив")

            if BACKUP_INCLUDE_CONFIG:
                config_path = project_root / "settings" / "config.py"
                if config_path.exists():
                    tar.add(config_path, arcname=f"{archive_folder}/config.py")
                    logger.info("[Backup] config.py в архив")
                else:
                    logger.warning("[Backup] settings/config.py не найден")

            if BACKUP_INCLUDE_TEXTS:
                texts_path = project_root / "settings" / "texts.py"
                if texts_path.exists():
                    tar.add(texts_path, arcname=f"{archive_folder}/texts.py")
                    logger.info("[Backup] texts.py в архив")
                else:
                    logger.warning("[Backup] settings/texts.py не найден")

            if BACKUP_INCLUDE_IMG:
                img_dir = project_root / "img"
                if img_dir.exists() and img_dir.is_dir():
                    img_files = [f for f in img_dir.iterdir() if f.is_file()]
                    for img_file in img_files:
                        tar.add(img_file, arcname=f"{archive_folder}/img/{img_file.name}")
                    logger.info("[Backup] img/ в архив ({} файлов)", len(img_files))
                else:
                    logger.warning("[Backup] img/ не найдена")

                uploads_dir = project_root / "static" / "web_uploads"
                if uploads_dir.exists() and uploads_dir.is_dir():
                    upload_files = [f for f in uploads_dir.iterdir() if f.is_file()]
                    for upload_file in upload_files:
                        tar.add(upload_file, arcname=f"{archive_folder}/web_uploads/{upload_file.name}")
                    logger.info("[Backup] web_uploads/ в архив ({} файлов)", len(upload_files))
                else:
                    logger.info("[Backup] web_uploads/ пуста или не найдена")

        logger.info("[Backup] Архив создан: {}", archive_path)

        if db_backup_path and os.path.exists(db_backup_path) and db_backup_path != str(archive_path):
            try:
                os.unlink(db_backup_path)
                logger.info("[Backup] Временный файл БД удалён: {}", db_backup_path)
            except Exception as e:
                logger.warning("[Backup] Не удалось удалить временный файл БД: {}", e)

        return str(archive_path), None

    except Exception as e:
        logger.error("[Backup] Ошибка создания архива: {}", e)
        return None, e


def _cleanup_old_backups() -> Exception | None:
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
                    logger.info("[Backup] Удалён старый: {}", backup_file)

        for archive_file in backup_dir.glob("*.tar.gz"):
            if archive_file.is_file():
                file_mtime = datetime.fromtimestamp(archive_file.stat().st_mtime)
                if file_mtime < cutoff_date:
                    archive_file.unlink()
                    logger.info("[Backup] Удалён старый архив: {}", archive_file)

        logger.info("[Backup] Очистка старых завершена")
        return None
    except Exception as e:
        logger.error("[Backup] Ошибка при очистке: {}", e)
        return e


def _create_s3_client():
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=BACKUP_S3_ENDPOINT,
        aws_access_key_id=BACKUP_S3_ACCESS_KEY,
        aws_secret_access_key=BACKUP_S3_SECRET_KEY,
        region_name=BACKUP_S3_REGION or "us-east-1",
    )


def _upload_to_s3(backup_file_path: str) -> Exception | None:
    """Загружает бекап в S3 и чистит старые (синхронно, вызывается через run_io)."""
    try:
        s3 = _create_s3_client()
        prefix = BACKUP_S3_PATH.strip("/")
        object_key = f"{prefix}/{os.path.basename(backup_file_path)}"

        s3.upload_file(backup_file_path, BACKUP_S3_BUCKET, object_key)
        logger.info("[Backup S3] Загружен: {}", object_key)

        _cleanup_s3_backups(s3, prefix)
        return None
    except Exception as e:
        logger.error("[Backup S3] Ошибка: {}", e)
        return e


def _cleanup_s3_backups(s3, prefix: str) -> None:
    """Удаляет старые бекапы в S3, оставляя BACKUP_S3_KEEP последних."""
    if BACKUP_S3_KEEP <= 0:
        return

    all_objects = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BACKUP_S3_BUCKET, Prefix=f"{prefix}/"):
        all_objects.extend(page.get("Contents", []))

    all_objects.sort(key=lambda x: x["LastModified"])

    if len(all_objects) <= BACKUP_S3_KEEP:
        return

    to_delete = all_objects[:-BACKUP_S3_KEEP]
    for i in range(0, len(to_delete), 1000):
        batch = [{"Key": obj["Key"]} for obj in to_delete[i : i + 1000]]
        s3.delete_objects(Bucket=BACKUP_S3_BUCKET, Delete={"Objects": batch, "Quiet": True})

    logger.info("[Backup S3] Удалено старых бекапов: {}", len(to_delete))


async def create_backup_and_send_to_admins(client) -> None:
    await client.login()
    await client.database.export()


MAX_DUMP_PARTS = 12


async def _send_dump_in_parts(active_bot, targets, kw_base: dict, db_path: str, limit: int) -> int:
    """Режет дамп на куски под лимит Telegram и отправляет их по очереди.

    pg_dump -Fc уже сжат внутри, поэтому упаковать его меньше не выйдет —
    остаётся резать. Собирается обратно обычным `cat part* > dump.sql`.
    """
    total = os.path.getsize(db_path)
    parts = (total + limit - 1) // limit
    if parts > MAX_DUMP_PARTS:
        return 0

    base = os.path.basename(db_path)
    sent = 0
    async with aiofiles.open(db_path, "rb") as source:
        for index in range(1, parts + 1):
            chunk = await source.read(limit)
            if not chunk:
                break
            doc = BufferedInputFile(file=chunk, filename=f"{base}.part{index:02d}")
            caption = f"Часть {index} из {parts} · собрать: cat {base}.part* &gt; {base}"
            for target in targets:
                try:
                    await active_bot.send_document(document=doc, caption=caption, chat_id=target, **kw_base)
                except Exception as e:
                    logger.error("[Backup] Часть {} дампа не отправлена в {}: {}", index, target, e)
            sent += 1
    return sent


async def _send_backup_telegram(backup_file_path: str, bot_instance: Bot | None = None) -> None:
    if not backup_file_path or not os.path.exists(backup_file_path):
        raise FileNotFoundError(f"Файл бэкапа не найден: {backup_file_path}")

    active_bot = bot_instance
    own_session = False

    if BACKUP_OTHER_BOT_TOKEN:
        active_bot = Bot(token=BACKUP_OTHER_BOT_TOKEN)
        own_session = True
    elif active_bot is None:
        from bot import bot as active_bot

    TELEGRAM_SEND_LIMIT = 49 * 1024 * 1024

    try:
        filename = os.path.basename(backup_file_path)
        chat_id, thread_id = _parse_destination()

        file_size = os.path.getsize(backup_file_path)
        if file_size > TELEGRAM_SEND_LIMIT:
            size_mb = file_size / (1024 * 1024)
            targets = [chat_id] if chat_id else list(ADMIN_ID)

            # Когда бэкап и есть дамп, второй раз его снимать незачем — режем этот.
            already_dump = backup_file_path.endswith(".sql")
            if already_dump:
                db_path, db_err = backup_file_path, None
            else:
                db_path, db_err = _create_database_backup()
            db_doc = None
            db_note = ""
            if db_err or not db_path:
                db_note = f"Дамп базы не создан: {db_err}" if db_err else "Дамп базы не создан"
                logger.error("[Backup] Дамп для отправки не создан: {}", db_err)
            elif os.path.getsize(db_path) > TELEGRAM_SEND_LIMIT:
                db_size_mb = os.path.getsize(db_path) / (1024 * 1024)
                kw_parts: dict = {"parse_mode": "HTML"}
                if chat_id and thread_id:
                    kw_parts["message_thread_id"] = thread_id
                parts_sent = await _send_dump_in_parts(
                    active_bot, targets, kw_parts, db_path, TELEGRAM_SEND_LIMIT
                )
                if parts_sent:
                    db_note = (
                        f"Дамп {db_size_mb:.0f} МБ ушёл {parts_sent} частями — "
                        f"собрать: cat {os.path.basename(db_path)}.part* &gt; {os.path.basename(db_path)}"
                    )
                    logger.info("[Backup] Дамп {} МБ отправлен {} частями", int(db_size_mb), parts_sent)
                else:
                    db_note = f"Дамп базы тоже не влез ({db_size_mb:.0f} МБ), лежит рядом: {db_path}"
                    logger.warning(
                        "[Backup] Дамп {} МБ больше лимита и слишком велик для дробления, оставлен: {}",
                        int(db_size_mb),
                        db_path,
                    )
            else:
                try:
                    async with aiofiles.open(db_path, "rb") as f:
                        db_bytes = await f.read()
                    db_doc = BufferedInputFile(file=db_bytes, filename=os.path.basename(db_path))
                except Exception as e:
                    db_note = f"Дамп базы прочитать не удалось: {e}"
                    logger.error("[Backup] Не удалось прочитать дамп БД: {}", e)
                finally:
                    if not already_dump:
                        try:
                            os.unlink(db_path)
                        except Exception:
                            pass

            from handlers.admin.panel.headers import card, menu_text, section

            blocks = [
                section("📦 Дамп" if already_dump else "📦 Архив", f"Размер: {size_mb:.0f} МБ", "Лимит: 50 МБ"),
                section("🖥 Лежит на сервере", f"<code>{backup_file_path}</code>"),
            ]
            if db_note:
                blocks.append(section("🗄 База", db_note))
            blocks.append(section("♻️ Вернуть", "Бот → Управление БД"))
            headline = "💾 В чат ушёл дамп базы."
            if not db_doc:
                headline = "⚠️ Дамп в чат не влез." if already_dump else "⚠️ Архив в чат не влез."
            caption = menu_text(
                "Бэкап",
                headline,
                card(*blocks),
            )

            for target in targets:
                try:
                    kw: dict = {"chat_id": target, "parse_mode": "HTML"}
                    if chat_id and thread_id:
                        kw["message_thread_id"] = thread_id
                    if db_doc is not None:
                        await active_bot.send_document(document=db_doc, caption=caption, **kw)
                    else:
                        await active_bot.send_message(text=caption, **kw)
                except Exception as e:
                    logger.error("[Backup] Не отправлено уведомление о большом бэкапе {}: {}", target, e)
            logger.warning(
                "[Backup] Полный архив {:.1f} МБ > лимита Telegram; дамп БД отправлен={}", size_mb, db_doc is not None
            )
            return

        async with aiofiles.open(backup_file_path, "rb") as f:
            backup_data = await f.read()
        backup_input_file = BufferedInputFile(file=backup_data, filename=filename)

        if chat_id:
            send_kwargs: dict = {"chat_id": chat_id, "document": backup_input_file}
            if thread_id:
                send_kwargs["message_thread_id"] = thread_id
            if BACKUP_CAPTION:
                send_kwargs["caption"] = BACKUP_CAPTION
            await active_bot.send_document(**send_kwargs)
            logger.info("[Backup] Отправлено в {}{}", chat_id, f" (тред {thread_id})" if thread_id else "")
        else:
            for admin_id in ADMIN_ID:
                try:
                    send_kwargs = {"chat_id": admin_id, "document": backup_input_file}
                    if BACKUP_CAPTION:
                        send_kwargs["caption"] = BACKUP_CAPTION
                    await active_bot.send_document(**send_kwargs)
                    logger.info("[Backup] Отправлено админу: {}", admin_id)
                except Exception as e:
                    logger.error("[Backup] Не отправлено админу {}: {}", admin_id, e)
    finally:
        if own_session:
            await active_bot.session.close()

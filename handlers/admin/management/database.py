import gzip
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import traceback

from pathlib import Path
from tempfile import NamedTemporaryFile

from aiogram import Bot, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from core.executor import run_io
from filters.admin import HasPermission
from filters.permissions import PERM_MANAGEMENT
from logger import logger
from settings.buttons import BACK
from settings.config import BACK_DIR, DB_NAME, DB_PASSWORD, DB_USER, PG_HOST, PG_IN_DOCKER, PG_PORT
from utils.backup import _find_docker_postgres_container

from ..panel.headers import menu_text, quote, section, wrap_text


_PG_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_pg_identifier(value: str, label: str) -> str:
    if not _PG_IDENT_RE.match(value):
        raise ValueError(f"Недопустимый PostgreSQL-идентификатор ({label}): {value!r}")
    return value


from . import router
from .keyboard import AdminPanelCallback, build_back_to_db_menu, build_database_kb, build_export_db_sources_kb


DOCKER_POSTGRES_CONTAINER = "solobot-postgres"

TELEGRAM_DOWNLOAD_LIMIT = 20 * 1024 * 1024


def sync_restore_database(
    tmp_path: str,
    db_name: str,
    db_user: str,
    db_password: str,
    pg_host: str,
    pg_port: str,
) -> tuple[bool, str]:
    """Восстановление БД из файла. Вызывать через run_io()."""
    is_custom_dump = False
    with open(tmp_path, "rb") as f:
        if f.read(5) == b"PGDMP":
            is_custom_dump = True

    use_docker = PG_IN_DOCKER
    docker_container = _find_docker_postgres_container() if use_docker else None

    if use_docker and not docker_container:
        return False, f"Контейнер PostgreSQL '{DOCKER_POSTGRES_CONTAINER}' не найден или не запущен"

    def _run_admin_psql(sql: str) -> None:
        if use_docker:
            subprocess.run(
                [
                    "docker",
                    "exec",
                    "-e",
                    f"PGPASSWORD={db_password}",
                    docker_container,
                    "psql",
                    "-U",
                    db_user,
                    "-h",
                    "127.0.0.1",
                    "-p",
                    "5432",
                    "-d",
                    "postgres",
                    "-c",
                    sql,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            return

        if shutil.which("psql") is None:
            raise FileNotFoundError("psql не найден на хосте и контейнер PostgreSQL не обнаружен")

        env = os.environ.copy()
        env["PGPASSWORD"] = db_password
        subprocess.run(
            [
                "psql",
                "-U",
                db_user,
                "-h",
                pg_host,
                "-p",
                pg_port,
                "-d",
                "postgres",
                "-c",
                sql,
            ],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )

    try:
        safe_name = _safe_pg_identifier(db_name, "db_name")
        safe_user = _safe_pg_identifier(db_user, "db_user")
        _run_admin_psql(
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{safe_name}' AND pid <> pg_backend_pid();"
        )
        _run_admin_psql(f"DROP DATABASE IF EXISTS {safe_name};")
        _run_admin_psql(f"CREATE DATABASE {safe_name} OWNER {safe_user};")
    except ValueError as e:
        return False, str(e)
    except subprocess.CalledProcessError as e:
        return False, (e.stderr or e.stdout or str(e))

    try:
        if use_docker:
            with open(tmp_path, "rb") as dump_file:
                if is_custom_dump:
                    result = subprocess.run(
                        [
                            "docker",
                            "exec",
                            "-i",
                            "-e",
                            f"PGPASSWORD={db_password}",
                            docker_container,
                            "pg_restore",
                            f"--dbname={db_name}",
                            "-U",
                            db_user,
                            "-h",
                            "127.0.0.1",
                            "-p",
                            "5432",
                            "--no-owner",
                            "--exit-on-error",
                        ],
                        stdin=dump_file,
                        capture_output=True,
                    )
                else:
                    result = subprocess.run(
                        [
                            "docker",
                            "exec",
                            "-i",
                            "-e",
                            f"PGPASSWORD={db_password}",
                            docker_container,
                            "psql",
                            "-U",
                            db_user,
                            "-h",
                            "127.0.0.1",
                            "-p",
                            "5432",
                            "-d",
                            db_name,
                        ],
                        stdin=dump_file,
                        capture_output=True,
                    )
        else:
            env = os.environ.copy()
            env["PGPASSWORD"] = db_password
            if is_custom_dump:
                if shutil.which("pg_restore") is None:
                    return False, "pg_restore не найден на хосте и контейнер PostgreSQL не обнаружен"
                result = subprocess.run(
                    [
                        "pg_restore",
                        f"--dbname={db_name}",
                        "-U",
                        db_user,
                        "-h",
                        pg_host,
                        "-p",
                        pg_port,
                        "--no-owner",
                        "--exit-on-error",
                        tmp_path,
                    ],
                    capture_output=True,
                    text=True,
                    env=env,
                )
            else:
                if shutil.which("psql") is None:
                    return False, "psql не найден на хосте и контейнер PostgreSQL не обнаружен"
                result = subprocess.run(
                    ["psql", "-U", db_user, "-h", pg_host, "-p", pg_port, "-d", db_name, "-f", tmp_path],
                    capture_output=True,
                    text=True,
                    env=env,
                )
        stderr = result.stderr.decode("utf-8", errors="replace") if isinstance(result.stderr, bytes) else result.stderr
        return result.returncode == 0, stderr or ""
    except Exception as e:
        return False, str(e)


_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def list_local_backups(limit: int = 20) -> list[Path]:
    backup_dir = Path(BACK_DIR)
    if not backup_dir.exists():
        return []
    files: list[Path] = []
    for pattern in ("*.tar.gz", "*.sql", "*.sql.gz", "*.dump"):
        files.extend(p for p in backup_dir.glob(pattern) if p.is_file())
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:limit]


def _restore_media_from_dir(extracted_root: Path) -> int:
    restored = 0
    mapping = {
        "web_uploads": _PROJECT_ROOT / "static" / "web_uploads",
        "img": _PROJECT_ROOT / "img",
    }
    for src_name, dest_dir in mapping.items():
        src_dir = extracted_root / src_name
        if not src_dir.is_dir():
            continue
        dest_dir.mkdir(parents=True, exist_ok=True)
        for item in src_dir.iterdir():
            if item.is_file():
                shutil.copy2(item, dest_dir / item.name)
                restored += 1
    return restored


def sync_restore_from_path(
    source_path: str,
    db_name: str,
    db_user: str,
    db_password: str,
    pg_host: str,
    pg_port: str,
) -> tuple[bool, str]:
    """Восстановление из локального файла бэкапа (.tar.gz / .sql / .sql.gz / .dump). Без лимита Telegram."""
    src = Path(source_path)
    if not src.is_file():
        return False, f"Файл не найден: {source_path}"

    name = src.name.lower()
    with tempfile.TemporaryDirectory() as tmpdir:
        dump_path: str | None = None
        media_note = ""

        if name.endswith((".tar.gz", ".tgz")):
            try:
                with tarfile.open(src, "r:gz") as tar:
                    tar.extractall(tmpdir, filter="data")
            except Exception as e:
                return False, f"Не удалось распаковать архив: {e}"
            extracted_root = Path(tmpdir)
            inner = [p for p in extracted_root.iterdir() if p.is_dir()]
            base = inner[0] if len(inner) == 1 else extracted_root
            db_file = base / "database.sql"
            if not db_file.is_file():
                found = list(extracted_root.rglob("database.sql"))
                db_file = found[0] if found else None
                if db_file is not None:
                    base = db_file.parent
            if db_file is None or not db_file.is_file():
                return False, "В архиве не найден database.sql"
            dump_path = str(db_file)
            media_count = _restore_media_from_dir(base)
            if media_count:
                media_note = f" Восстановлено медиа-файлов: {media_count}."
        elif name.endswith((".sql.gz", ".gz")):
            dump_path = os.path.join(tmpdir, "database.sql")
            try:
                with gzip.open(src, "rb") as gz, open(dump_path, "wb") as out:
                    shutil.copyfileobj(gz, out)
            except Exception as e:
                return False, f"Не удалось распаковать .gz: {e}"
        else:
            dump_path = str(src)

        success, err = sync_restore_database(dump_path, db_name, db_user, db_password, pg_host, pg_port)
        if not success:
            return False, err
        return True, media_note


class DatabaseState(StatesGroup):
    waiting_for_backup_file = State()


@router.callback_query(AdminPanelCallback.filter(F.action == "database"), HasPermission(PERM_MANAGEMENT))
async def handle_database_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        text=menu_text(
            "База данных",
            "Копии, восстановление и импорт.",
            quote("Бэкап можно забрать в чат, а восстановить — из файла или с сервера."),
            markup=build_database_kb(),
        ),
        reply_markup=build_database_kb(),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "restore_db"), HasPermission(PERM_MANAGEMENT))
async def prompt_restore_db(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        menu_text(
            "Восстановление из файла",
            "Пришлите файл резервной копии (.sql).",
            quote("⚠️ Все текущие данные будут перезаписаны."),
            markup=build_back_to_db_menu(),
        ),
        reply_markup=build_back_to_db_menu(),
    )
    await state.set_state(DatabaseState.waiting_for_backup_file)


@router.message(DatabaseState.waiting_for_backup_file, HasPermission(PERM_MANAGEMENT))
async def restore_database(message: Message, state: FSMContext, bot: Bot):
    document = message.document

    if not document or not document.file_name.endswith(".sql"):
        await message.answer(menu_text("База данных", "❌ отправьте файл с расширением .sql."))
        return

    if document.file_size and document.file_size > TELEGRAM_DOWNLOAD_LIMIT:
        size_mb = document.file_size / (1024 * 1024)
        await message.answer(
            menu_text(
                "База данных",
                f"❌ Файл {size_mb:.1f} МБ, а Telegram отдаёт ботам не больше 20 МБ.",
                quote(
                    f"Копии из папки {BACK_DIR} восстанавливаются без лимита — кнопка «Восстановить с сервера».",
                    "Залейте бэкап в эту папку любым способом или выгрузите дамп в сжатом виде.",
                ),
            ),
        )
        return

    try:
        with NamedTemporaryFile(delete=False, suffix=".sql") as tmp_file:
            tmp_path = tmp_file.name

        await bot.download(document, destination=tmp_path)
        logger.info("[Restore] Файл получен: {}", tmp_path)

        success, err_msg = await run_io(
            sync_restore_database,
            tmp_path,
            DB_NAME,
            DB_USER,
            DB_PASSWORD,
            PG_HOST,
            PG_PORT,
        )

        if not success:
            logger.error("[Restore] Ошибка: {}", err_msg)
            await message.answer(
                menu_text("База данных", "❌ Не удалось восстановить.") + f"\n<pre>{err_msg}</pre>",
            )
            return

        logger.info("[Restore] База восстановлена")
        await message.answer(
            menu_text("База данных", "✅ База данных восстановлена.", markup=build_back_to_db_menu()),
            reply_markup=build_back_to_db_menu(),
        )
        logger.info("[Restore] Завершение для перезапуска")
        await state.clear()
        sys.exit(0)

    except Exception as e:
        if "file is too big" in str(e).lower():
            logger.error("[Restore] Файл превышает лимит Telegram 20 МБ")
            await message.answer(
                menu_text(
                    "База данных",
                    "❌ Telegram не отдаёт боту файлы больше 20 МБ. Выгрузите дамп в сжатом формате или восстановите базу на сервере напрямую.",
                ),
            )
            return
        logger.exception(f"[Restore] Непредвиденная ошибка: {e}")
        await message.answer(
            menu_text("База данных", "❌ Ошибка восстановления.") + f"\n<pre>{traceback.format_exc()}</pre>",
        )
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


@router.callback_query(AdminPanelCallback.filter(F.action == "restore_db_local"), HasPermission(PERM_MANAGEMENT))
async def prompt_restore_db_local(callback: CallbackQuery):
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    backups = list_local_backups()
    if not backups:
        await callback.message.edit_text(
            menu_text(
                "База данных",
                "На сервере нет резервных копий.",
                section("📂 Папка копий", BACK_DIR),
                markup=build_back_to_db_menu(),
            ),
            reply_markup=build_back_to_db_menu(),
        )
        return

    builder = InlineKeyboardBuilder()
    for idx, path in enumerate(backups):
        try:
            size_mb = path.stat().st_size / (1024 * 1024)
        except Exception:
            size_mb = 0.0
        builder.button(
            text=f"📦 {path.stem[:24]} · {size_mb:.1f} МБ",
            callback_data=AdminPanelCallback(action=f"restore_local|{idx}").pack(),
        )
    builder.button(text=BACK, callback_data=AdminPanelCallback(action="back_to_db_menu").pack())
    builder.adjust(1)
    await callback.message.edit_text(
        menu_text(
            "Восстановление с сервера",
            "Копии, которые уже лежат на сервере.",
            quote(
                "Лимит Telegram в 20 МБ здесь не действует.",
                "Форматы: <code>.tar.gz</code> (БД и медиа), <code>.sql</code>, <code>.sql.gz</code>.",
            ),
            quote("⚠️ Данные будут перезаписаны, бот перезапустится."),
            markup=builder.as_markup(),
        ),
        reply_markup=builder.as_markup(),
    )


@router.callback_query(
    AdminPanelCallback.filter(F.action.startswith("restore_local|")),
    HasPermission(PERM_MANAGEMENT),
    flags={"popup": True},
)
async def restore_db_local(callback: CallbackQuery):
    try:
        idx = int(callback.data.split("|", 1)[1].split(":")[-1])
    except (ValueError, IndexError):
        await callback.answer("Некорректный выбор", show_alert=True)
        return

    backups = list_local_backups()
    if idx < 0 or idx >= len(backups):
        await callback.answer("Файл не найден, обновите список", show_alert=True)
        return

    source = backups[idx]
    await callback.message.edit_text(menu_text("База данных", f"⏳ Восстановление из <code>{source.name}</code>…"))

    success, note = await run_io(
        sync_restore_from_path,
        str(source),
        DB_NAME,
        DB_USER,
        DB_PASSWORD,
        PG_HOST,
        PG_PORT,
    )

    if not success:
        logger.error("[Restore] Локальное восстановление не удалось: {}", note)
        await callback.message.edit_text(
            menu_text("База данных", "❌ Восстановить не удалось.", section("⚠️ Причина", note)),
            reply_markup=build_back_to_db_menu(),
        )
        return

    logger.info("[Restore] База восстановлена из локального файла {}", source.name)
    await callback.message.edit_text(
        menu_text(
            "База данных",
            "✅ База восстановлена, перезапускаюсь…",
            section("📦 Файл", source.name, note.strip() or "медиа не восстанавливались"),
        )
    )
    sys.exit(0)


@router.callback_query(AdminPanelCallback.filter(F.action == "export_db"), HasPermission(PERM_MANAGEMENT))
async def handle_export_db(callback: CallbackQuery):
    await callback.message.edit_text(
        menu_text(
            "Импорт с панели",
            "Выберите панель.",
            quote("Бот подтянет с неё подписки и сохранит их в свою базу."),
            markup=build_export_db_sources_kb(),
        ),
        reply_markup=build_export_db_sources_kb(),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "back_to_db_menu"), HasPermission(PERM_MANAGEMENT))
async def back_to_database_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        menu_text("База данных", "📦 Управление базой данных:", markup=build_database_kb()),
        reply_markup=build_database_kb(),
    )

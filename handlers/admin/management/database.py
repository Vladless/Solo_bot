import gzip
import os
import re
import secrets
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
from settings.config import BACK_DIR, DB_NAME, DB_PASSWORD, DB_USER, PG_HOST, PG_PORT
from utils.backup import _get_postgres_execution_target

from ..panel.headers import menu_text, quote, section


_PG_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_pg_identifier(value: str, label: str) -> str:
    if not _PG_IDENT_RE.match(value):
        raise ValueError(f"Недопустимый PostgreSQL-идентификатор ({label}): {value!r}")
    return value


from . import router
from .keyboard import AdminPanelCallback, build_back_to_db_menu, build_database_kb, build_export_db_sources_kb


TELEGRAM_DOWNLOAD_LIMIT = 20 * 1024 * 1024


def sync_restore_database(
    tmp_path: str,
    db_name: str,
    db_user: str,
    db_password: str,
    pg_host: str,
    pg_port: str,
) -> tuple[bool, str]:
    """Восстанавливает БД из обычного или бинарного дампа."""
    is_custom_dump = False
    try:
        with open(tmp_path, "rb") as f:
            header = f.read(5)
            if not header:
                return False, "Дамп пуст"
            is_custom_dump = header == b"PGDMP"
            if not is_custom_dump:
                f.seek(0)
                if any(re.match(rb"\s*\\(?:connect|c)(?:\s|$)", line) for line in f):
                    return False, "SQL-дамп переключает базу. Используйте дамп без \\connect или бинарный pg_dump."
    except OSError as exc:
        return False, str(exc)

    try:
        safe_name = _safe_pg_identifier(db_name, "db_name")
        safe_user = _safe_pg_identifier(db_user, "db_user")
    except ValueError as error:
        return False, str(error)

    try:
        target, docker_container = _get_postgres_execution_target(client="pg_restore" if is_custom_dump else "psql")
    except OSError as error:
        return False, str(error)
    use_docker = target == "docker"
    if not use_docker:
        required = ("psql", "pg_restore") if is_custom_dump else ("psql",)
        missing = [client for client in required if shutil.which(client) is None]
        if missing:
            return False, "Не найдены PostgreSQL client утилиты: " + ", ".join(missing)
    if is_custom_dump:
        try:
            if use_docker:
                with open(tmp_path, "rb") as dump_file:
                    checked = subprocess.run(
                        ["docker", "exec", "-i", docker_container, "pg_restore", "--list"],
                        stdin=dump_file,
                        capture_output=True,
                    )
            else:
                checked = subprocess.run(["pg_restore", "--list", tmp_path], capture_output=True, text=True)
            if checked.returncode != 0:
                error = (
                    checked.stderr.decode("utf-8", errors="replace")
                    if isinstance(checked.stderr, bytes)
                    else checked.stderr
                )
                return False, error or "Дамп повреждён или несовместим с установленным pg_restore"
        except OSError as error:
            return False, str(error)
    pg_port = str(pg_port)

    def _run_admin_psql(sql: str) -> str:
        if use_docker:
            result = subprocess.run(
                [
                    "docker",
                    "exec",
                    "-e",
                    f"PGPASSWORD={db_password}",
                    docker_container,
                    "psql",
                    "--no-psqlrc",
                    "--set=ON_ERROR_STOP=1",
                    "-At",
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
            return result.stdout.strip()

        if shutil.which("psql") is None:
            raise FileNotFoundError("psql не найден на хосте и контейнер PostgreSQL не обнаружен")

        env = os.environ.copy()
        env["PGPASSWORD"] = db_password
        result = subprocess.run(
            [
                "psql",
                "--no-psqlrc",
                "--set=ON_ERROR_STOP=1",
                "-At",
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
        return result.stdout.strip()

    token = secrets.token_hex(6)
    stage_name = f"solo_restore_{token}"
    previous_name = f"solo_previous_{token}"
    quoted_name = f'"{safe_name}"'
    quoted_user = f'"{safe_user}"'
    stage_created = False
    try:
        _run_admin_psql(f"CREATE DATABASE {stage_name} OWNER {quoted_user} TEMPLATE template0;")
        stage_created = True
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
                            f"--dbname={stage_name}",
                            "-U",
                            db_user,
                            "-h",
                            "127.0.0.1",
                            "-p",
                            "5432",
                            "--no-owner",
                            "--clean",
                            "--if-exists",
                            "--exit-on-error",
                            "--single-transaction",
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
                            "--no-psqlrc",
                            "--set=ON_ERROR_STOP=1",
                            "--single-transaction",
                            "-U",
                            db_user,
                            "-h",
                            "127.0.0.1",
                            "-p",
                            "5432",
                            "-d",
                            stage_name,
                            "-f",
                            "-",
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
                        f"--dbname={stage_name}",
                        "-U",
                        db_user,
                        "-h",
                        pg_host,
                        "-p",
                        pg_port,
                        "--no-owner",
                        "--clean",
                        "--if-exists",
                        "--exit-on-error",
                        "--single-transaction",
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
                    [
                        "psql",
                        "--no-psqlrc",
                        "--set=ON_ERROR_STOP=1",
                        "--single-transaction",
                        "-U",
                        db_user,
                        "-h",
                        pg_host,
                        "-p",
                        pg_port,
                        "-d",
                        stage_name,
                        "-f",
                        tmp_path,
                    ],
                    capture_output=True,
                    text=True,
                    env=env,
                )
        stderr = result.stderr.decode("utf-8", errors="replace") if isinstance(result.stderr, bytes) else result.stderr
        if result.returncode != 0:
            return False, stderr or "Ошибка восстановления дампа"
        target_exists = (
            _run_admin_psql(f"SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname = '{safe_name}');") == "t"
        )
        swap = []
        if target_exists:
            swap.extend([
                f"ALTER DATABASE {quoted_name} ALLOW_CONNECTIONS false;",
                f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{safe_name}' AND pid <> pg_backend_pid();",
                f"ALTER DATABASE {quoted_name} RENAME TO {previous_name};",
            ])
        swap.append(f"ALTER DATABASE {stage_name} RENAME TO {quoted_name};")
        _run_admin_psql("BEGIN; " + " ".join(swap) + " COMMIT;")
        stage_created = False
        if target_exists:
            try:
                _run_admin_psql(f"DROP DATABASE {previous_name};")
            except Exception as exc:
                logger.warning("[Restore] Прежняя база сохранена как {}: {}", previous_name, type(exc).__name__)
        return True, stderr or ""
    except subprocess.CalledProcessError as exc:
        error = exc.stderr or exc.stdout or f"PostgreSQL завершился с кодом {exc.returncode}"
        if isinstance(error, bytes):
            error = error.decode("utf-8", errors="replace")
        return False, error
    except Exception as e:
        return False, str(e)
    finally:
        if stage_created:
            try:
                _run_admin_psql(f"DROP DATABASE IF EXISTS {stage_name};")
            except Exception as exc:
                logger.warning("[Restore] Не удалось удалить временную базу {}: {}", stage_name, type(exc).__name__)


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
    """Восстанавливает изображения и установленные наборы сайта."""
    restored = 0
    mapping = {
        "web_uploads": _PROJECT_ROOT / "static" / "web_uploads",
        "web_packs": _PROJECT_ROOT / "static" / "web_packs",
        "img": _PROJECT_ROOT / "img",
    }
    for src_name, dest_dir in mapping.items():
        src_dir = extracted_root / src_name
        if not src_dir.is_dir() or src_dir.is_symlink():
            continue
        dest_dir.mkdir(parents=True, exist_ok=True)
        destination_root = dest_dir.resolve()
        for item in src_dir.rglob("*"):
            if item.is_file() and not item.is_symlink():
                target = dest_dir / item.relative_to(src_dir)
                if target.is_symlink() or not target.resolve().is_relative_to(destination_root):
                    raise ValueError("Путь восстановления выходит за пределы каталога файлов сайта")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
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
    """Восстанавливает базу и файлы из локального архива или дампа."""
    src = Path(source_path)
    if not src.is_file():
        return False, f"Файл не найден: {source_path}"

    name = src.name.lower()
    with tempfile.TemporaryDirectory() as tmpdir:
        dump_path: str | None = None
        media_root: Path | None = None
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
            media_root = base
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
        if media_root is not None:
            try:
                media_count = _restore_media_from_dir(media_root)
            except Exception as exc:
                return False, f"БД восстановлена, но не удалось восстановить файлы сайта: {exc}"
            if media_count:
                media_note = f" Восстановлено медиа-файлов: {media_count}."
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
            menu_text("База данных", "✅ База данных восстановлена."),
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
            menu_text("База данных", "На сервере нет резервных копий.", section("📂 Папка копий", BACK_DIR)),
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
            "Импорт с панели", "Выберите панель.", quote("Бот подтянет с неё подписки и сохранит их в свою базу.")
        ),
        reply_markup=build_export_db_sources_kb(),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "back_to_db_menu"), HasPermission(PERM_MANAGEMENT))
async def back_to_database_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        menu_text("База данных", "📦 Управление базой данных:"),
        reply_markup=build_database_kb(),
    )

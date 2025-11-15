import os
import subprocess
import sys
import traceback

from tempfile import NamedTemporaryFile

from aiogram import Bot, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import DB_NAME, DB_PASSWORD, DB_USER, PG_HOST, PG_PORT
from logger import logger

from . import router
from .keyboard import AdminPanelCallback, build_back_to_db_menu, build_database_kb, build_export_db_sources_kb


class DatabaseState(StatesGroup):
    waiting_for_backup_file = State()


@router.callback_query(AdminPanelCallback.filter(F.action == "database"))
async def handle_database_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        text="🗄 <b>Управление базой данных</b>",
        reply_markup=build_database_kb(),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "restore_db"))
async def prompt_restore_db(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "📂 Отправьте файл резервной копии (.sql), чтобы восстановить базу данных.\n"
        "⚠️ Все текущие данные будут перезаписаны.",
        reply_markup=build_back_to_db_menu(),
    )
    await state.set_state(DatabaseState.waiting_for_backup_file)


@router.message(DatabaseState.waiting_for_backup_file)
async def restore_database(message: Message, state: FSMContext, bot: Bot):
    document = message.document

    if not document or not document.file_name.endswith(".sql"):
        await message.answer("❌ Пожалуйста, отправьте файл с расширением .sql.")
        return

    try:
        with NamedTemporaryFile(delete=False, suffix=".sql") as tmp_file:
            tmp_path = tmp_file.name

        await bot.download(document, destination=tmp_path)
        logger.info(f"[Restore] Файл получен и сохранён: {tmp_path}")

        is_custom_dump = False
        with open(tmp_path, "rb") as f:
            signature = f.read(5)
            if signature == b"PGDMP":
                is_custom_dump = True

        subprocess.run(
            [
                "sudo",
                "-u",
                "postgres",
                "psql",
                "-d",
                "postgres",
                "-c",
                f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{DB_NAME}' AND pid <> pg_backend_pid();",
            ],
            check=True,
        )

        subprocess.run(
            ["sudo", "-u", "postgres", "psql", "-d", "postgres", "-c", f"DROP DATABASE IF EXISTS {DB_NAME};"],
            check=True,
        )

        subprocess.run(
            ["sudo", "-u", "postgres", "psql", "-d", "postgres", "-c", f"CREATE DATABASE {DB_NAME} OWNER {DB_USER};"],
            check=True,
        )

        logger.info("[Restore] База данных пересоздана")

        os.environ["PGPASSWORD"] = DB_PASSWORD

        if is_custom_dump:
            result = subprocess.run(
                [
                    "pg_restore",
                    f"--dbname={DB_NAME}",
                    "-U",
                    DB_USER,
                    "-h",
                    PG_HOST,
                    "-p",
                    PG_PORT,
                    "--no-owner",
                    "--exit-on-error",
                    tmp_path,
                ],
                capture_output=True,
                text=True,
            )
        else:
            result = subprocess.run(
                [
                    "psql",
                    "-U",
                    DB_USER,
                    "-h",
                    PG_HOST,
                    "-p",
                    PG_PORT,
                    "-d",
                    DB_NAME,
                    "-f",
                    tmp_path,
                ],
                capture_output=True,
                text=True,
            )

        del os.environ["PGPASSWORD"]

        if result.returncode != 0:
            logger.error(f"[Restore] Ошибка восстановления: {result.stderr}")
            await message.answer(
                f"❌ Ошибка при восстановлении базы данных:\n<pre>{result.stderr}</pre>",
            )
            return

        await message.answer(
            "✅ База данных восстановлена.",
            reply_markup=build_back_to_db_menu(),
        )
        logger.info("[Restore] Успешно восстановлено. Завершаем процесс для перезапуска.")
        await state.clear()
        sys.exit(0)

    except Exception as e:
        logger.exception(f"[Restore] Непредвиденная ошибка: {e}")
        await message.answer(
            f"❌ Произошла ошибка:\n<pre>{traceback.format_exc()}</pre>",
        )
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


@router.callback_query(AdminPanelCallback.filter(F.action == "export_db"))
async def handle_export_db(callback: CallbackQuery):
    await callback.message.edit_text(
        "📤 Выберите панель, с которой требуется получить данные:\n\n"
        "<i>Подтянутся подписки с панели и будут сохранены в базу данных бота.</i>",
        reply_markup=build_export_db_sources_kb(),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "back_to_db_menu"))
async def back_to_database_menu(callback: CallbackQuery):
    await callback.message.edit_text("📦 Управление базой данных:", reply_markup=build_database_kb())

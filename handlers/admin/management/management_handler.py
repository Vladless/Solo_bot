import hashlib
import json
import os
import re
import subprocess
import sys
import time
import traceback

from asyncio import sleep
from datetime import datetime
from tempfile import NamedTemporaryFile

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from dateutil import parser
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from config import DB_NAME, DB_PASSWORD, DB_USER, PG_HOST, PG_PORT, REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD
from database.models import Admin, Key, Server, User
from filters.admin import IsAdminFilter
from handlers.keys.operations import update_subscription
from logger import logger
from middlewares import maintenance
from panels.remnawave import RemnawaveAPI

from ..panel.keyboard import build_admin_back_kb
from .keyboard import (
    AdminPanelCallback,
    build_admin_back_kb_to_admins,
    build_admins_kb,
    build_back_to_db_menu,
    build_database_kb,
    build_export_db_sources_kb,
    build_management_kb,
    build_post_import_kb,
    build_role_selection_kb,
    build_single_admin_menu,
    build_token_result_kb,
)


router = Router()


class AdminManagementStates(StatesGroup):
    waiting_for_new_domain = State()


class Import3xuiStates(StatesGroup):
    waiting_for_file = State()


class FileUploadState(StatesGroup):
    waiting_for_file = State()


class DatabaseState(StatesGroup):
    waiting_for_backup_file = State()


class AdminState(StatesGroup):
    waiting_for_tg_id = State()


@router.callback_query(AdminPanelCallback.filter(F.action == "management"), IsAdminFilter())
async def handle_management(callback_query: CallbackQuery, session: AsyncSession):
    tg_id = callback_query.from_user.id

    result = await session.execute(select(Admin).where(Admin.tg_id == tg_id))
    admin = result.scalar_one_or_none()

    if not admin:
        await callback_query.message.edit_text("❌ Вы не зарегистрированы как администратор.")
        return

    await callback_query.message.edit_text(
        text="🤖 Управление ботом",
        reply_markup=build_management_kb(admin.role),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "change_domain"), IsAdminFilter())
async def request_new_domain(callback_query: CallbackQuery, state: FSMContext):
    """Запрашивает у администратора новый домен."""
    await state.set_state(AdminManagementStates.waiting_for_new_domain)
    await callback_query.message.edit_text(
        text="🌐 Введите новый домен (без https://):\nПример: solobotdomen.ru",
    )


@router.message(AdminManagementStates.waiting_for_new_domain)
async def process_new_domain(message: Message, state: FSMContext, session: AsyncSession):
    """Обновляет домен в таблице keys."""
    new_domain = message.text.strip()

    if not re.fullmatch(r"[a-zA-Z0-9.-]+", new_domain) or " " in new_domain:
        logger.warning("[DomainChange] Некорректный домен")
        await message.answer(
            "🚫 Некорректный домен! Введите домен без http:// и без пробелов.",
            reply_markup=build_admin_back_kb("admin"),
        )
        return

    new_domain_url = f"https://{new_domain}"

    try:
        stmt = (
            update(Key)
            .values(
                key=func.regexp_replace(Key.key, r"^https://[^/]+", new_domain_url),
                remnawave_link=func.regexp_replace(Key.remnawave_link, r"^https://[^/]+", new_domain_url),
            )
            .where(
                (Key.key.startswith("https://") & ~Key.key.startswith(new_domain_url))
                | (Key.remnawave_link.startswith("https://") & ~Key.remnawave_link.startswith(new_domain_url))
            )
        )
        await session.execute(stmt)
        await session.commit()
        logger.info("[DomainChange] Запрос на обновление домена выполнен успешно.")
    except Exception as e:
        logger.error(f"[DomainChange] Ошибка при выполнении запроса: {e}")
        await message.answer(
            f"❌ Ошибка при обновлении домена: {e}",
            reply_markup=build_admin_back_kb("admin"),
        )
        return

    try:
        sample = await session.execute(select(Key.key, Key.remnawave_link).limit(1))
        example = sample.fetchone()
        logger.info(f"[DomainChange] Пример обновленной записи: {example}")
    except Exception as e:
        logger.error(f"[DomainChange] Ошибка при выборке обновленной записи: {e}")

    await message.answer(
        f"✅ Домен успешно изменен на {new_domain}!",
        reply_markup=build_admin_back_kb("admin"),
    )
    await state.clear()


@router.callback_query(AdminPanelCallback.filter(F.action == "toggle_maintenance"))
async def toggle_maintenance_mode(callback: CallbackQuery, session: AsyncSession):
    tg_id = callback.from_user.id

    result = await session.execute(select(Admin).where(Admin.tg_id == tg_id))
    admin = result.scalar_one_or_none()

    if not admin:
        await callback.answer("❌ Админ не найден.", show_alert=True)
        return

    maintenance.maintenance_mode = not maintenance.maintenance_mode
    new_status = "включён" if maintenance.maintenance_mode else "выключен"
    await callback.answer(f"🛠️ Режим обслуживания {new_status}.", show_alert=True)

    await callback.message.edit_reply_markup(reply_markup=build_management_kb(admin.role))


@router.callback_query(AdminPanelCallback.filter(F.action == "admins"))
async def show_admins(callback: CallbackQuery, session: AsyncSession):
    result = await session.execute(select(Admin.tg_id, Admin.role))
    admins = result.all()
    await callback.message.edit_text("👑 <b>Список админов</b>", reply_markup=build_admins_kb(admins))


@router.callback_query(AdminPanelCallback.filter(F.action == "add_admin"))
async def prompt_new_admin(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "Введите <code>tg_id</code> нового админа:", reply_markup=build_admin_back_kb_to_admins()
    )
    await state.set_state(AdminState.waiting_for_tg_id)


@router.message(AdminState.waiting_for_tg_id)
async def save_new_admin(message: Message, session: AsyncSession, state: FSMContext):
    try:
        tg_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Неверный формат. Введите числовой <code>tg_id</code>.")
        return

    result = await session.execute(select(Admin).where(Admin.tg_id == tg_id))
    if result.scalar_one_or_none():
        await message.answer("⚠️ Такой админ уже существует.")
    else:
        session.add(Admin(tg_id=tg_id, role="moderator", description="Добавлен вручную"))
        await session.commit()
        await message.answer(f"✅ Админ <code>{tg_id}</code> добавлен.", reply_markup=build_admin_back_kb_to_admins())

    await state.clear()


@router.callback_query(AdminPanelCallback.filter(F.action.startswith("admin_menu|")))
async def open_admin_menu(callback: CallbackQuery, callback_data: AdminPanelCallback, session: AsyncSession):
    tg_id = int(callback_data.action.split("|")[1])

    result = await session.execute(select(Admin.role).where(Admin.tg_id == tg_id))
    admin = result.scalar_one_or_none()
    role = admin or "moderator"

    await callback.message.edit_text(
        f"👤 <b>Управление админом</b> <code>{tg_id}</code>", reply_markup=build_single_admin_menu(tg_id, role)
    )


@router.callback_query(AdminPanelCallback.filter(F.action.startswith("generate_token|")))
async def generate_token(callback: CallbackQuery, callback_data: AdminPanelCallback, session: AsyncSession):
    tg_id = int(callback_data.action.split("|")[1])

    result = await session.execute(select(Admin).where(Admin.tg_id == tg_id))
    admin = result.scalar_one_or_none()
    if not admin:
        await callback.message.edit_text("❌ Админ не найден.")
        return

    token = Admin.generate_token()
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    admin.token = token_hash
    await session.commit()

    msg = await callback.message.edit_text(
        f"🎟 <b>Новый токен для</b> <code>{tg_id}</code>:\n\n"
        f"<code>{token}</code>\n\n"
        f"⚠️ Это сообщение исчезнет через 5 минут.",
        reply_markup=build_token_result_kb(token),
    )

    await sleep(300)
    try:
        await msg.delete()
    except Exception:
        pass


@router.callback_query(AdminPanelCallback.filter(F.action.startswith("edit_role|")))
async def edit_admin_role(callback: CallbackQuery, callback_data: AdminPanelCallback):
    tg_id = int(callback_data.action.split("|")[1])
    await callback.message.edit_text(
        f"✏ <b>Выберите новую роль для</b> <code>{tg_id}</code>:", reply_markup=build_role_selection_kb(tg_id)
    )


@router.callback_query(AdminPanelCallback.filter(F.action.startswith("set_role|")))
async def set_admin_role(callback: CallbackQuery, callback_data: AdminPanelCallback, session: AsyncSession):
    try:
        _, tg_id_str, role = callback_data.action.split("|")
        tg_id = int(tg_id_str)
        if role not in ("superadmin", "moderator"):
            raise ValueError
    except Exception:
        await callback.message.edit_text("❌ Неверный формат.")
        return

    if tg_id == callback.from_user.id:
        await callback.message.edit_text(
            "🚫 <b>Нельзя изменить свою собственную роль!</b>", reply_markup=build_single_admin_menu(tg_id)
        )
        return

    result = await session.execute(select(Admin).where(Admin.tg_id == tg_id))
    admin = result.scalar_one_or_none()
    if not admin:
        await callback.message.edit_text("❌ Админ не найден.")
        return

    admin.role = role
    await session.commit()

    await callback.message.edit_text(
        f"✅ Роль админа <code>{tg_id}</code> изменена на <b>{role}</b>.", reply_markup=build_single_admin_menu(tg_id)
    )


@router.callback_query(AdminPanelCallback.filter(F.action.startswith("delete_admin|")))
async def delete_admin(callback: CallbackQuery, callback_data: AdminPanelCallback, session: AsyncSession):
    tg_id = int(callback_data.action.split("|")[1])

    await session.execute(delete(Admin).where(Admin.tg_id == tg_id))
    await session.commit()

    await callback.message.edit_text(
        f"🗑 Админ <code>{tg_id}</code> удалён.", reply_markup=build_admin_back_kb_to_admins()
    )


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


@router.callback_query(AdminPanelCallback.filter(F.action == "export_remnawave"))
async def show_remnawave_clients(callback: CallbackQuery, session: AsyncSession):
    result = await session.execute(select(Server).where(Server.panel_type == "remnawave", Server.enabled.is_(True)))
    servers = result.scalars().all()

    if not servers:
        await callback.message.edit_text(
            "❌ Нет доступных Remnawave-серверов.",
            reply_markup=build_back_to_db_menu(),
        )
        return

    server = servers[0]

    api = RemnawaveAPI(base_url=server.api_url)

    users = await api.get_all_users_time(
        username=REMNAWAVE_LOGIN,
        password=REMNAWAVE_PASSWORD,
    )

    if not users:
        await callback.message.edit_text(
            "📭 На панели нет клиентов.",
            reply_markup=build_back_to_db_menu(),
        )
        return

    logger.warning(f"[Remnawave Export] Пример ответа:\n{json.dumps(users[:3], indent=2, ensure_ascii=False)}")

    added_users = await import_remnawave_users(session, users)

    server_id = server.cluster_name or server.server_name

    added_keys = await import_remnawave_keys(session, users, server_id=server_id)

    preview = ""
    for i, user in enumerate(users[:3], 1):
        email = user.get("email") or user.get("username") or "-"
        expire = user.get("expireAt", "")[:10]
        preview += f"{i}. {email} — до {expire}\n"

    await callback.message.edit_text(
        f"📄 Найдено клиентов: <b>{len(users)}</b>\n"
        f"👤 Импортировано пользователей: <b>{added_users}</b>\n"
        f"🔐 Импортировано ключей: <b>{added_keys}</b>\n\n"
        f"<b>Первые 3:</b>\n{preview}",
        reply_markup=build_back_to_db_menu(),
    )


async def import_remnawave_users(session: AsyncSession, users: list[dict]) -> int:
    added = 0

    for user in users:
        tg_id = user.get("telegramId")
        if not tg_id:
            continue

        exists = await session.execute(select(User).where(User.tg_id == tg_id))
        if exists.scalar():
            continue

        try:
            new_user = User(
                tg_id=tg_id,
                username=None,
                first_name=None,
                last_name=None,
                language_code=None,
                is_bot=False,
                balance=0.0,
                trial=1,
                source_code=None,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            session.add(new_user)
            added += 1

        except SQLAlchemyError as e:
            logger.error(f"[Remnawave Import] Ошибка при добавлении пользователя {tg_id}: {e}")
            continue

    await session.commit()
    return added


async def import_remnawave_keys(session: AsyncSession, users: list[dict], server_id: str) -> int:
    added = 0

    for user in users:
        tg_id = user.get("telegramId")
        client_id = user.get("uuid")
        email = user.get("email") or user.get("username")
        remnawave_link = user.get("subscriptionUrl")
        expire_at = user.get("expireAt")
        created_at = user.get("createdAt")

        if not tg_id or not client_id:
            logger.warning(f"[SKIP] Пропущен клиент: tg_id={tg_id}, client_id={client_id}")
            continue

        exists_stmt = await session.execute(select(Key).where(Key.client_id == client_id))
        if exists_stmt.scalar():
            logger.info(f"[SKIP] Ключ уже существует: {client_id}")
            continue

        try:
            created_ts = int(parser.isoparse(created_at).timestamp() * 1000) if created_at else int(time.time() * 1000)
            expire_ts = int(parser.isoparse(expire_at).timestamp() * 1000) if expire_at else int(time.time() * 1000)

            new_key = Key(
                tg_id=tg_id,
                client_id=client_id,
                email=email,
                created_at=created_ts,
                expiry_time=expire_ts,
                key="",
                server_id=server_id,
                remnawave_link=remnawave_link,
                tariff_id=None,
                is_frozen=False,
                alias=None,
                notified=False,
                notified_24h=False,
            )
            session.add(new_key)
            added += 1

            logger.info(f"[ADD] Ключ добавлен: {client_id}, до {expire_at}, email={email}, server_id={server_id}")

        except Exception as e:
            logger.error(f"[ERROR] Ошибка при добавлении ключа {client_id}: {e}")

    await session.commit()
    logger.info(f"[IMPORT] Всего добавлено ключей: {added}")
    return added


@router.callback_query(AdminPanelCallback.filter(F.action == "request_3xui_file"))
async def prompt_for_3xui_file(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "📂 Пришлите файл базы данных <code>x-ui.db</code> для восстановления подписок и клиентов.\n\n"
        "Формат: SQLite-файл с таблицей <code>inbounds</code>.\n\n"
        "<b>⚠️ Важно!</b> Убедитесь, что у всех подписок в панели прописан <code>telegram_id</code>.\n"
        "После восстановления обязательно выполните <b>синхронизацию</b> с текущими серверами!",
        reply_markup=build_back_to_db_menu(),
    )
    await state.set_state(Import3xuiStates.waiting_for_file)


@router.message(Import3xuiStates.waiting_for_file, F.document)
async def handle_3xui_db_upload(message: Message, state: FSMContext, session: AsyncSession):
    file = message.document

    if not file.file_name.endswith(".db"):
        await message.reply("❌ Пожалуйста, пришли файл с расширением .db")
        return

    file_path = f"/tmp/{file.file_name}"
    await message.bot.download(file, destination=file_path)

    processing_message = await message.reply("📥 Файл получен. Начинаю восстановление...")

    try:
        from database.importer import import_keys_from_3xui_db

        imported, skipped = await import_keys_from_3xui_db(file_path, session)

        await processing_message.edit_text(
            f"✅ Восстановление завершено:\n"
            f"🔐 Импортировано подписок: <b>{imported}</b>\n"
            f"⏭ Пропущено (уже есть): <b>{skipped}</b>",
            reply_markup=build_post_import_kb(),
        )

    except Exception as e:
        logger.error(f"[Import 3x-ui] Ошибка: {e}")
        await processing_message.edit_text(
            "❌ Произошла ошибка при импорте. Убедись, что это валидный файл <code>x-ui.db</code>",
            reply_markup=build_back_to_db_menu(),
        )

    await state.clear()


@router.callback_query(AdminPanelCallback.filter(F.action == "resync_after_import"))
async def handle_resync_after_import(callback: CallbackQuery, session: AsyncSession):
    await callback.answer("🔁 Начинаю перевыпуск подписок...")

    result = await session.execute(select(Key.tg_id, Key.email))
    keys = result.all()

    success = 0
    failed = 0

    for tg_id, email in keys:
        try:
            await update_subscription(tg_id=tg_id, email=email, session=session)
            success += 1
        except Exception as e:
            logger.error(f"[Resync] Ошибка при перевыпуске {email}: {e}")
            failed += 1

    await callback.message.edit_text(
        f"🔁 Перевыпуск завершён:\n✅ Успешно: <b>{success}</b>\n❌ Ошибки: <b>{failed}</b>",
        reply_markup=build_back_to_db_menu(),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "upload_file"))
async def prompt_for_file_upload(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "📤 <b>Загрузка файла</b>\n\n"
        "Вы можете заменить файл в корневой директории бота.\n\n"
        "📁 <b>Отправьте файл с таким же именем и расширением</b>, "
        "как у уже существующего файла. Он будет автоматически заменён.",
        reply_markup=build_admin_back_kb("management"),
    )
    await state.set_state(FileUploadState.waiting_for_file)


@router.message(FileUploadState.waiting_for_file, F.document)
async def handle_admin_file_upload(message: Message, state: FSMContext):
    document = message.document
    file_name = document.file_name

    if not file_name or "." not in file_name:
        await message.answer("❌ У файла должно быть имя с расширением.")
        return

    dest_path = os.path.abspath(f"./{file_name}")

    try:
        await message.bot.download(document, destination=dest_path)
        await message.answer(
            f"✅ Файл <code>{file_name}</code> успешно загружен и заменён.\n\n"
            "🔄 <b>Перезагрузите бота, чтобы изменения вступили в силу.</b>",
            reply_markup=build_admin_back_kb("management"),
        )
    except Exception as e:
        logger.error(f"[Upload File] Ошибка при загрузке файла {file_name}: {e}")
        await message.answer(
            f"❌ Не удалось сохранить файл: {e}",
            reply_markup=build_admin_back_kb("management"),
        )
    await state.clear()

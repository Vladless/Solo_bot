import json
import re
import time

from datetime import datetime

from aiogram import F
from aiogram.types import CallbackQuery
from dateutil import parser
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from config import REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD
from database.models import Key, Server, User
from filters.admin import IsAdminFilter
from logger import logger
from panels.remnawave import RemnawaveAPI

from . import router
from .keyboard import AdminPanelCallback, build_back_to_db_menu


def extract_tg_id_from_username(value: str | None) -> int | None:
    if not value:
        return None

    value = value.strip()
    match = re.search(r"_(\d+)(?:\D|$)", value)
    if not match:
        return None

    tg_id = int(match.group(1))
    if tg_id <= 0:
        return None

    return tg_id


def extract_tg_id_from_user_payload(user: dict) -> int | None:
    tg_id = user.get("telegramId")

    if isinstance(tg_id, int):
        if tg_id > 0:
            return tg_id
        return None

    if isinstance(tg_id, str):
        tg_id = tg_id.strip()
        if tg_id.isdigit():
            tg_id_int = int(tg_id)
            return tg_id_int if tg_id_int > 0 else None

    tg_id = extract_tg_id_from_username(user.get("username")) or extract_tg_id_from_username(user.get("email"))
    return tg_id


@router.callback_query(AdminPanelCallback.filter(F.action == "export_remnawave"), IsAdminFilter())
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
        expire = (user.get("expireAt") or "")[:10]
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
        tg_id = extract_tg_id_from_user_payload(user)
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
        tg_id = extract_tg_id_from_user_payload(user)

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

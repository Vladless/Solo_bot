import json
import sqlite3
import time

from contextlib import closing
from datetime import datetime
from itertools import cycle
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import MODES_CONFIG
from database.models import Key, Server, User
from settings.config import USE_COUNTRY_SELECTION


async def import_keys_from_3xui_db(db_path: str, session: AsyncSession) -> tuple[int, int, list[str]]:
    imported = 0
    skipped = 0
    imported_emails: list[str] = []

    use_country_selection = bool(MODES_CONFIG.get("COUNTRY_SELECTION_ENABLED", USE_COUNTRY_SELECTION))

    if use_country_selection:
        result = await session.execute(
            select(Server.server_name).where(Server.enabled.is_(True), Server.panel_type == "3x-ui")
        )
    else:
        result = await session.execute(
            select(Server.cluster_name)
            .where(Server.enabled.is_(True), Server.panel_type == "3x-ui", Server.cluster_name.isnot(None))
            .distinct()
        )

    server_ids = [row[0] for row in result.fetchall()]
    if not server_ids:
        raise RuntimeError("❌ Не найдено доступных серверов или кластеров для 3x-ui")

    server_cycle = cycle(server_ids)

    parsed_clients = []
    try:
        with closing(sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "clients" in tables:
                parsed_clients = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT uuid AS id, email, tg_id AS tgId, expiry_time AS expiryTime, enable, "
                        "limit_ip AS limitIp, total_gb AS totalGB, created_at FROM clients "
                        "WHERE EXISTS (SELECT 1 FROM client_inbounds AS link "
                        "JOIN inbounds AS inbound ON inbound.id = link.inbound_id WHERE link.client_id = clients.id)"
                    )
                ]
            else:
                for (settings_raw,) in conn.execute("SELECT settings FROM inbounds"):
                    try:
                        settings = json.loads(settings_raw)
                        clients = settings.get("clients", [])
                        if not isinstance(clients, list):
                            raise ValueError("Некорректный список клиентов")
                        parsed_clients.extend(clients)
                    except (TypeError, ValueError, AttributeError):
                        skipped += 1
    except (sqlite3.Error, OSError) as e:
        raise RuntimeError(f"Не удалось прочитать SQLite: {e}") from e

    now_ts = int(time.time() * 1000)

    for c in parsed_clients:
        try:
            tg_id_raw = c.get("tgId")
            client_id = c.get("id")
            email = c.get("email")
            tg_id = int(tg_id_raw)
            if (
                isinstance(tg_id_raw, bool)
                or not 0 < tg_id <= 2**63 - 1
                or not isinstance(client_id, str)
                or not client_id.strip()
                or client_id.isdecimal()
                or not isinstance(email, str)
                or not email.strip()
            ):
                raise ValueError("Некорректные данные клиента")
            expiry_time = int(c.get("expiryTime") or 0)
            created_at = int(c.get("created_at") or now_ts)
            device_limit = int(c.get("limitIp") or 0)
            total_traffic = int(c.get("totalGB") or 0)
            if (
                c.get("enable", True) not in (True, 1)
                or expiry_time < 0
                or device_limit < 0
                or total_traffic < 0
                or total_traffic % (1024**3)
            ):
                raise ValueError("Лимиты клиента нельзя сохранить без изменения")
        except (TypeError, ValueError, AttributeError, OverflowError):
            skipped += 1
            continue

        key_exists = await session.execute(select(Key).where((Key.client_id == client_id) | (Key.email == email)))
        if key_exists.first():
            skipped += 1
            continue
        server_id = next(server_cycle)

        user_exists = await session.execute(select(User).where(User.tg_id == tg_id))
        if not user_exists.scalar():
            session.add(
                User(
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
            )

        await session.flush()
        user_row = await session.execute(select(User.id).where(User.tg_id == tg_id))
        bill_uid = user_row.scalar_one()

        session.add(
            Key(
                user_id=bill_uid,
                tg_id=tg_id,
                client_id=client_id,
                email=email,
                created_at=created_at,
                expiry_time=expiry_time,
                key="",
                server_id=server_id,
                remnawave_link=None,
                tariff_id=None,
                current_device_limit=device_limit,
                current_traffic_limit=total_traffic // (1024**3),
                is_frozen=False,
                alias=None,
                notified=False,
                notified_24h=False,
            )
        )
        imported += 1
        imported_emails.append(email)

    return imported, skipped, imported_emails

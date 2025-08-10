import json
import sqlite3
import time

from datetime import datetime
from itertools import cycle

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from config import USE_COUNTRY_SELECTION
from database.models import Key, Server, User


async def import_keys_from_3xui_db(db_path: str, session: AsyncSession) -> tuple[int, int]:
    imported = 0
    skipped = 0

    if USE_COUNTRY_SELECTION:
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

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id, remark, settings FROM inbounds")
        inbounds = cursor.fetchall()
    except Exception as e:
        raise RuntimeError(f"Не удалось прочитать SQLite: {e}")
    finally:
        conn.close()

    parsed_clients = []
    for inbound_id, _remark, settings_raw in inbounds:
        try:
            settings = json.loads(settings_raw)
            clients = settings.get("clients", [])
            for c in clients:
                expiry = c.get("expiryTime")
                c["expiryTime"] = int(float(expiry)) if expiry else 0
                c["limitIp"] = int(c.get("limitIp", 0) or 0)
                c["inbound_id"] = inbound_id
                parsed_clients.append(c)
        except Exception:
            continue

    now_ts = int(time.time() * 1000)

    for c in parsed_clients:
        tg_id = c.get("tgId")
        client_id = str(c.get("id"))
        email = c.get("email")
        expiry_time = int(c.get("expiryTime") or now_ts)
        created_at = now_ts
        server_id = next(server_cycle)

        if not tg_id or not client_id:
            continue

        user_exists = await session.execute(select(User).where(User.tg_id == tg_id))
        if not user_exists.scalar():
            try:
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
            except SQLAlchemyError:
                continue

        key_exists = await session.execute(select(Key).where(Key.client_id == client_id))
        if key_exists.scalar():
            skipped += 1
            continue

        try:
            session.add(
                Key(
                    tg_id=tg_id,
                    client_id=client_id,
                    email=email,
                    created_at=created_at,
                    expiry_time=expiry_time,
                    key="",
                    server_id=server_id,
                    remnawave_link=None,
                    tariff_id=None,
                    is_frozen=False,
                    alias=None,
                    notified=False,
                    notified_24h=False,
                )
            )
            imported += 1
        except SQLAlchemyError:
            continue

    await session.commit()
    return imported, skipped

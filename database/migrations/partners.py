from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection


PARTNER_OWNER_COLUMNS = {
    "partners": (("partner_user_id", "partner_tg_id"), ("joined_user_id", "joined_tg_id")),
    "payout_requests": (("user_id", "tg_id"),),
    "partner_tx": (("user_id", "tg_id"), ("actor_user_id", "actor_tg_id")),
}


async def migrate_partner_owners(conn: AsyncConnection) -> None:
    """Закрепляет владельцев сохранённых партнёрских данных по ID клиента."""
    if conn.dialect.name == "postgresql":
        await conn.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('solo:partners:owners:migration',0))"))
    tables = await conn.run_sync(lambda sync: set(inspect(sync).get_table_names()))
    if "users" not in tables:
        return
    for table, pairs in PARTNER_OWNER_COLUMNS.items():
        if table not in tables:
            continue
        columns = await conn.run_sync(lambda sync: {c["name"]: c for c in inspect(sync).get_columns(table)})
        if table == "payout_requests" and "reserved_amount" not in columns:
            await conn.execute(text('ALTER TABLE "payout_requests" ADD COLUMN reserved_amount DOUBLE PRECISION'))
        for owner, mirror in pairs:
            if mirror not in columns:
                continue
            if owner not in columns:
                await conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{owner}" BIGINT'))
            if not columns[mirror]["nullable"] and conn.dialect.name == "postgresql":
                await conn.execute(text(f'ALTER TABLE "{table}" ALTER COLUMN "{mirror}" DROP NOT NULL'))
            await conn.execute(
                text(
                    f'UPDATE "{table}" SET "{owner}" = '
                    f'(SELECT u.id FROM users u WHERE u.tg_id = "{table}"."{mirror}") '
                    f'WHERE "{owner}" IS NULL AND "{mirror}" IS NOT NULL '
                    f'AND EXISTS (SELECT 1 FROM users u WHERE u.tg_id = "{table}"."{mirror}")'
                )
            )
            await conn.execute(text(f'CREATE INDEX IF NOT EXISTS "ix_{table}_{owner}" ON "{table}" ("{owner}")'))

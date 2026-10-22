from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession


async def partner_transfer_schema_available(session: AsyncSession) -> bool:
    """Проверяет полноту сохранённых данных партнёрского дополнения."""
    required = {
        "users": {
            "partner_balance",
            "partner_code",
            "payout_method",
            "card_number",
            "partner_percent",
            "partner_percent_custom",
        },
        "partners": {"partner_tg_id", "joined_tg_id"},
        "payout_requests": {"tg_id"},
        "partner_tx": {"tg_id", "actor_tg_id"},
    }

    def inspect_schema(sync_session):
        inspector = inspect(sync_session.connection())
        tables = set(inspector.get_table_names())
        existing = {
            table: {column["name"] for column in inspector.get_columns(table)} if table in tables else set()
            for table in required
        }
        return existing, tables

    existing, tables = await session.run_sync(inspect_schema)
    if not any(
        (existing[table] & columns) if table == "users" else table in tables for table, columns in required.items()
    ):
        return False
    missing = {
        table: sorted(columns - existing[table])
        for table, columns in required.items()
        if not columns <= existing[table]
    }
    if missing:
        raise RuntimeError(f"Неполная схема партнёрского дополнения: {missing}")
    return True

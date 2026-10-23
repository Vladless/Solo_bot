from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def migrate_kassai_autopay(conn: AsyncConnection) -> None:
    """Защищает сохранённые условия автоплатежа от изменения."""
    if conn.dialect.name != "postgresql":
        return
    await conn.execute(
        text("""
        CREATE OR REPLACE FUNCTION kassai_autopay_frozen_attempt() RETURNS trigger AS $$
        BEGIN
            IF NEW.payload IS DISTINCT FROM OLD.payload OR NEW.intent IS DISTINCT FROM OLD.intent
               OR NEW.payment_id IS DISTINCT FROM OLD.payment_id
               OR (OLD.provider_order_id IS NOT NULL AND NEW.provider_order_id IS DISTINCT FROM OLD.provider_order_id)
               OR (OLD.method_id IS NOT NULL AND NEW.method_id IS DISTINCT FROM OLD.method_id) THEN
                RAISE EXCEPTION 'Recurring payment request and provider identity are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    )
    await conn.execute(
        text("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_trigger
                WHERE tgrelid='kassai_autopay_attempts'::regclass AND tgname='kassai_autopay_frozen_attempt') THEN
                CREATE TRIGGER kassai_autopay_frozen_attempt BEFORE UPDATE ON kassai_autopay_attempts
                FOR EACH ROW EXECUTE FUNCTION kassai_autopay_frozen_attempt();
            END IF;
        END $$
    """)
    )

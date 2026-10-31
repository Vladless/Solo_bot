from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def migrate_payhot_autopay(conn: AsyncConnection) -> None:
    """Защищает сохранённый запрос списания от изменения."""
    if conn.dialect.name != "postgresql":
        return
    await conn.execute(
        text("""
        CREATE OR REPLACE FUNCTION payhot_autopay_frozen_attempt() RETURNS trigger AS $$
        BEGIN
            IF NEW.payload IS DISTINCT FROM OLD.payload OR NEW.intent IS DISTINCT FROM OLD.intent
               OR NEW.payment_id IS DISTINCT FROM OLD.payment_id
               OR NEW.subscription_id IS DISTINCT FROM OLD.subscription_id
               OR NEW.method_id IS DISTINCT FROM OLD.method_id
               OR (OLD.provider_charge_id IS NOT NULL AND NEW.provider_charge_id IS DISTINCT FROM OLD.provider_charge_id) THEN
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
                WHERE tgrelid='payhot_autopay_attempts'::regclass AND tgname='payhot_autopay_frozen_attempt') THEN
                CREATE TRIGGER payhot_autopay_frozen_attempt BEFORE UPDATE ON payhot_autopay_attempts
                FOR EACH ROW EXECUTE FUNCTION payhot_autopay_frozen_attempt();
            END IF;
        END $$
    """)
    )

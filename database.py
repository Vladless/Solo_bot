from datetime import datetime

import asyncpg

from config import BONUS_PERCENT, DATABASE_URL
from logger import logger


async def init_db():
    conn = await asyncpg.connect(DATABASE_URL)

    # Таблица для хранения информации о пользователях
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS connections (
            tg_id BIGINT PRIMARY KEY NOT NULL,
            balance REAL NOT NULL DEFAULT 0.0,
            trial INTEGER NOT NULL DEFAULT 0
        )
        """
    )

    # Таблица для хранения ключей
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS keys (
            tg_id BIGINT NOT NULL,
            client_id TEXT NOT NULL,
            email TEXT NOT NULL,
            created_at BIGINT NOT NULL,
            expiry_time BIGINT NOT NULL,
            key TEXT NOT NULL,
            server_id TEXT NOT NULL DEFAULT 'cluster1',
            notified BOOLEAN NOT NULL DEFAULT FALSE,
            notified_24h BOOLEAN NOT NULL DEFAULT FALSE,
            PRIMARY KEY (tg_id, client_id)
        )
        """
    )

    # Таблица для хранения рефералов
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS referrals (
            referred_tg_id BIGINT PRIMARY KEY NOT NULL,
            referrer_tg_id BIGINT NOT NULL,
            reward_issued BOOLEAN DEFAULT FALSE
        )
        """
    )

    # Таблица для хранения купонов
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS coupons (
            id SERIAL PRIMARY KEY,
            code TEXT UNIQUE NOT NULL,
            amount INTEGER NOT NULL,
            usage_limit INTEGER NOT NULL DEFAULT 1,  
            usage_count INTEGER NOT NULL DEFAULT 0,  
            is_used BOOLEAN NOT NULL DEFAULT FALSE  
        )
        """
    )

    # Таблица для отслеживания использований купонов пользователями
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS coupon_usages (
            coupon_id INTEGER NOT NULL REFERENCES coupons(id) ON DELETE CASCADE,
            user_id BIGINT NOT NULL,
            used_at TIMESTAMP NOT NULL DEFAULT NOW(),
            PRIMARY KEY (coupon_id, user_id)
        )
        """
    )

    await conn.close()


async def create_coupon(coupon_code: str, amount: float, usage_limit: int):
    conn = await asyncpg.connect(DATABASE_URL)

    try:
        await conn.execute(
            """
            INSERT INTO coupons (code, amount, usage_limit, usage_count, is_used)
            VALUES ($1, $2, $3, 0, FALSE)
        """,
            coupon_code,
            amount,
            usage_limit,
        )
    except Exception as e:
        print(f"Ошибка при создании купона: {e}")
        raise
    finally:
        await conn.close()


async def get_all_coupons():
    """Получить список всех купонов"""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        coupons = await conn.fetch(
            """
            SELECT code, amount, usage_limit, usage_count
            FROM coupons
        """
        )
        return coupons
    except Exception as e:
        print(f"Ошибка при получении купонов: {e}")
        return []
    finally:
        await conn.close()


async def delete_coupon_from_db(coupon_code: str):
    """Удалить купон из базы данных по его коду"""
    conn = await asyncpg.connect(DATABASE_URL)
    try:

        coupon_record = await conn.fetchrow(
            """
            SELECT id FROM coupons WHERE code = $1
        """,
            coupon_code,
        )

        if not coupon_record:
            return False

        await conn.execute(
            """
            DELETE FROM coupons WHERE code = $1
        """,
            coupon_code,
        )

        return True

    except Exception as e:
        print(f"Ошибка при удалении купона: {e}")
        return False
    finally:
        await conn.close()


async def restore_trial(tg_id: int):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute("UPDATE connections SET trial = 0 WHERE tg_id = $1", tg_id)
    except Exception as e:

        logger.error(f"Ошибка при установке значения триала: {e}")
    finally:
        await conn.close()


async def add_connection(tg_id: int, balance: float = 0.0, trial: int = 0):
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute(
        """
        INSERT INTO connections (tg_id, balance, trial)
        VALUES ($1, $2, $3)
    """,
        tg_id,
        balance,
        trial,
    )
    await conn.close()


async def check_connection_exists(tg_id: int):
    conn = await asyncpg.connect(DATABASE_URL)
    exists = await conn.fetchval(
        """
        SELECT EXISTS(SELECT 1 FROM connections WHERE tg_id = $1)
    """,
        tg_id,
    )
    await conn.close()
    return exists


async def store_key(
    tg_id: int, client_id: str, email: str, expiry_time: int, key: str, server_id: str
):
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute(
        """
        INSERT INTO keys (tg_id, client_id, email, created_at, expiry_time, key, server_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
    """,
        tg_id,
        client_id,
        email,
        int(datetime.utcnow().timestamp() * 1000),
        expiry_time,
        key,
        server_id,
    )
    await conn.close()


async def get_keys(tg_id: int):
    conn = await asyncpg.connect(DATABASE_URL)
    records = await conn.fetch(
        """
        SELECT client_id, email, created_at, key
        FROM keys
        WHERE tg_id = $1
    """,
        tg_id,
    )
    await conn.close()
    return records


async def get_keys_by_server(tg_id: int, server_id: str):
    conn = await asyncpg.connect(DATABASE_URL)
    records = await conn.fetch(
        """
        SELECT client_id, email, created_at, key
        FROM keys
        WHERE tg_id = $1 AND server_id = $2
    """,
        tg_id,
        server_id,
    )
    await conn.close()
    return records


async def has_active_key(tg_id: int) -> bool:
    conn = await asyncpg.connect(DATABASE_URL)
    count = await conn.fetchval("SELECT COUNT(*) FROM keys WHERE tg_id = $1", tg_id)
    await conn.close()
    return count > 0


async def get_balance(tg_id: int) -> float:
    conn = await asyncpg.connect(DATABASE_URL)
    balance = await conn.fetchval(
        "SELECT balance FROM connections WHERE tg_id = $1", tg_id
    )
    await conn.close()
    return balance if balance is not None else 0.0


async def update_balance(tg_id: int, amount: float):
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute(
        """
        UPDATE connections
        SET balance = balance + $1
        WHERE tg_id = $2
    """,
        amount,
        tg_id,
    )

    await handle_referral_on_balance_update(tg_id, amount)

    await conn.close()


async def get_trial(tg_id: int) -> int:
    conn = await asyncpg.connect(DATABASE_URL)
    trial = await conn.fetchval("SELECT trial FROM connections WHERE tg_id = $1", tg_id)
    await conn.close()
    return trial if trial is not None else 0


async def get_key_count(tg_id: int) -> int:
    conn = await asyncpg.connect(DATABASE_URL)
    count = await conn.fetchval("SELECT COUNT(*) FROM keys WHERE tg_id = $1", tg_id)
    await conn.close()
    return count if count is not None else 0


async def get_all_users(conn):
    return await conn.fetch("SELECT tg_id FROM connections")


async def add_referral(referred_tg_id: int, referrer_tg_id: int):
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute(
        """
        INSERT INTO referrals (referred_tg_id, referrer_tg_id)
        VALUES ($1, $2)
    """,
        referred_tg_id,
        referrer_tg_id,
    )
    await conn.close()


async def handle_referral_on_balance_update(tg_id: int, amount: float):
    conn = await asyncpg.connect(DATABASE_URL)

    referral = await conn.fetchrow(
        """
        SELECT referrer_tg_id FROM referrals WHERE referred_tg_id = $1
    """,
        tg_id,
    )

    if referral:
        referrer_tg_id = referral["referrer_tg_id"]

        bonus = amount * BONUS_PERCENT

        if bonus < 0:
            bonus = 0

        await update_balance(referrer_tg_id, bonus)

        await conn.execute(
            """
            UPDATE referrals SET reward_issued = TRUE
            WHERE referrer_tg_id = $1 AND referred_tg_id = $2
        """,
            referrer_tg_id,
            tg_id,
        )

    await conn.close()


async def get_referral_stats(referrer_tg_id: int):
    conn = await asyncpg.connect(DATABASE_URL)
    total_referrals = await conn.fetchval(
        """
        SELECT COUNT(*) FROM referrals WHERE referrer_tg_id = $1
    """,
        referrer_tg_id,
    )

    active_referrals = await conn.fetchval(
        """
        SELECT COUNT(*) FROM referrals WHERE referrer_tg_id = $1 AND reward_issued = TRUE
    """,
        referrer_tg_id,
    )

    await conn.close()

    return {"total_referrals": total_referrals, "active_referrals": active_referrals}


async def update_key_expiry(client_id: str, new_expiry_time: int):
    """
    Обновление времени истечения ключа на новое значение.
    """
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute(
        """
        UPDATE keys
        SET expiry_time = $1, notified = FALSE, notified_24h = FALSE
        WHERE client_id = $2
    """,
        new_expiry_time,
        client_id,
    )
    await conn.close()


async def delete_key(client_id: str):
    """
    Удаление ключа из базы данных.
    """
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute(
        """
        DELETE FROM keys
        WHERE client_id = $1
    """,
        client_id,
    )
    await conn.close()


async def add_balance_to_client(client_id: str, amount: float):
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute(
        """
        UPDATE connections
        SET balance = balance + $1
        WHERE tg_id = $2
    """,
        amount,
        client_id,
    )
    await conn.close()


async def get_client_id_by_email(email: str):
    """
    Получение client_id по email.
    """
    conn = await asyncpg.connect(DATABASE_URL)
    client_id = await conn.fetchval(
        """
        SELECT client_id FROM keys WHERE email = $1
    """,
        email,
    )
    await conn.close()
    return client_id


async def get_tg_id_by_client_id(client_id: str):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        result = await conn.fetchrow(
            "SELECT tg_id FROM keys WHERE client_id = $1", client_id
        )
        return result["tg_id"] if result else None
    finally:
        await conn.close()

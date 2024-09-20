import aiosqlite
from datetime import datetime
from config import DATABASE_PATH

async def init_db():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS connections (
                tg_id INTEGER PRIMARY KEY NOT NULL,
                balance REAL NOT NULL DEFAULT 0.0,
                trial INTEGER NOT NULL DEFAULT 0
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS keys (
                tg_id INTEGER NOT NULL,
                client_id TEXT NOT NULL,
                email TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                expiry_time INTEGER NOT NULL,         
                key TEXT NOT NULL,
                PRIMARY KEY (tg_id, client_id)
            )
        ''')
        await db.commit()

async def add_connection(tg_id: int, balance: float = 0.0, trial: int = 0):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute('''
            INSERT INTO connections (tg_id, balance, trial)
            VALUES (?, ?, ?)
        ''', (tg_id, balance, trial))
        await db.commit()

async def store_key(tg_id: int, client_id: str, email: str, expiry_time: int, key: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute('''
            INSERT INTO keys (tg_id, client_id, email, created_at, expiry_time, key)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (tg_id, client_id, email, int(datetime.utcnow().timestamp() * 1000), expiry_time, key))
        await db.commit()


async def get_keys(tg_id: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute('''
            SELECT client_id, email, created_at, key
            FROM keys
            WHERE tg_id = ?
        ''', (tg_id,)) as cursor:
            return await cursor.fetchall()

async def has_active_key(tg_id: int) -> bool:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM keys WHERE tg_id = ?", (tg_id,)) as cursor:
            count = await cursor.fetchone()
            return count[0] > 0

async def get_balance(tg_id: int) -> float:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT balance FROM connections WHERE tg_id = ?", (tg_id,)) as cursor:
            record = await cursor.fetchone()
            return record[0] if record else 0.0

async def update_balance(tg_id: int, amount: float):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute('''
            UPDATE connections 
            SET balance = balance + ? 
            WHERE tg_id = ?
        ''', (amount, tg_id))
        await db.commit()

async def get_trial(tg_id: int) -> int:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT trial FROM connections WHERE tg_id = ?", (tg_id,)) as cursor:
            record = await cursor.fetchone()
            return record[0] if record else 0

async def get_key_count(tg_id: int) -> int:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute('SELECT COUNT(*) FROM keys WHERE tg_id = ?', (tg_id,)) as cursor:
            count = await cursor.fetchone()
            return count[0] if count else 0

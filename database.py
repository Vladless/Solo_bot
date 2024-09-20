import aiosqlite
from datetime import datetime
from config import DATABASE_PATH
import aiosqlite

async def init_db():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS connections (
                tg_id INTEGER NOT NULL,
                client_id TEXT NOT NULL,
                email TEXT NOT NULL,
                expiry_time INTEGER NOT NULL,
                balance REAL NOT NULL DEFAULT 0.0,  -- Добавлено поле для баланса
                PRIMARY KEY (tg_id, client_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS keys (
                client_id TEXT NOT NULL,
                email TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                key TEXT NOT NULL,
                PRIMARY KEY (client_id)
            )
        ''')
        await db.commit()

async def add_connection(tg_id: int, client_id: str, email: str, expiry_time: int, balance: float = 0.0):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute('''
            INSERT INTO connections (tg_id, client_id, email, expiry_time, balance)
            VALUES (?, ?, ?, ?, ?)
        ''', (tg_id, client_id, email, expiry_time, balance))
        await db.commit()

async def store_key(client_id: str, email: str, key: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute('''
            INSERT INTO keys (client_id, email, created_at, key)
            VALUES (?, ?, ?, ?)
        ''', (client_id, email, int(datetime.utcnow().timestamp() * 1000), key))
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
        async with db.execute("SELECT COUNT(*) FROM connections WHERE tg_id = ? AND expiry_time > ?", 
                              (tg_id, int(datetime.utcnow().timestamp() * 1000))) as cursor:
            count = await cursor.fetchone()
            return count[0] > 0

async def get_active_key_email(tg_id: int) -> str:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT email FROM connections WHERE tg_id = ? AND expiry_time > ?", 
                              (tg_id, int(datetime.utcnow().timestamp() * 1000))) as cursor:
            record = await cursor.fetchone()
            return record[0] if record else None

async def get_key_expiry_time(tg_id: int) -> datetime:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT expiry_time FROM connections WHERE tg_id = ? AND expiry_time > ?", 
                              (tg_id, int(datetime.utcnow().timestamp() * 1000))) as cursor:
            record = await cursor.fetchone()
            if record:
                expiry_time = record[0]
                return datetime.utcfromtimestamp(expiry_time / 1000)
            return None

async def get_balance(tg_id: int) -> str:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT balance FROM connections WHERE tg_id = ?", (tg_id,)) as cursor:
            record = await cursor.fetchone()
            return record[0] if record else "Неизвестно"

async def update_balance(tg_id: int, amount: float):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute('''
            UPDATE connections 
            SET balance = balance + ? 
            WHERE tg_id = ?
        ''', (amount, tg_id))
        await db.commit()

async def get_key_count(tg_id: int) -> int:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute('SELECT COUNT(*) FROM keys k JOIN connections c ON k.client_id = c.client_id WHERE c.tg_id = ?', (tg_id,)) as cursor:
            count = await cursor.fetchone()
            return count[0] if count else 0

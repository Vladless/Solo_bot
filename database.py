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
                PRIMARY KEY (tg_id, client_id)
            )
        ''')
        await db.commit()

async def add_connection(tg_id: int, client_id: str, email: str, expiry_time: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute('''
            INSERT INTO connections (tg_id, client_id, email, expiry_time)
            VALUES (?, ?, ?, ?)
        ''', (tg_id, client_id, email, expiry_time))
        await db.commit()

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
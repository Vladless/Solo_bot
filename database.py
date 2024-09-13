import aiosqlite
from datetime import datetime

DATABASE_PATH = 'database.db'

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
        # Добавляем новый ключ
        await db.execute('''
            INSERT INTO connections (tg_id, client_id, email, expiry_time)
            VALUES (?, ?, ?, ?)
        ''', (tg_id, client_id, email, expiry_time))
        await db.commit()


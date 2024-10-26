import random
import asyncpg
import time
import uuid  # Импортируем модуль для генерации UUID
from config import DATABASE_URL, SERVERS, ADMIN_USERNAME, ADMIN_PASSWORD
from auth import login_with_credentials, link  # Импортируйте необходимые функции
from client import add_client
from database import store_key, add_connection
from handlers.texts import INSTRUCTIONS
from datetime import datetime, timedelta

def generate_random_email():
    """Генерирует случайный набор символов."""
    random_string = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=6))
    return random_string 

async def create_trial_key(tg_id: int):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        server_id = await get_least_loaded_server(conn)
        session = await login_with_credentials(server_id, ADMIN_USERNAME, ADMIN_PASSWORD)
        current_time = datetime.utcnow()

        expiry_time = current_time + timedelta(days=1, hours=3)
        expiry_timestamp = int(expiry_time.timestamp() * 1000)
        
        client_id = str(uuid.uuid4()) 
        email = generate_random_email() 
        response = await add_client(
            session, server_id, client_id, email, tg_id,
            limit_ip=1, total_gb=0, expiry_time=expiry_timestamp,
            enable=True, flow="xtls-rprx-vision"
        )
        if response.get("success"):  
            connection_link = await link(session, server_id, client_id, email)

            existing_connection = await conn.fetchrow('SELECT * FROM connections WHERE tg_id = $1', tg_id)

            if existing_connection:
                await conn.execute('UPDATE connections SET trial = 1 WHERE tg_id = $1', tg_id)
            else:
                await add_connection(tg_id, 0, 1)

            await store_key(tg_id, client_id, email, expiry_timestamp, connection_link, server_id)

            instructions = INSTRUCTIONS
            return {
                'key': connection_link,
                'instructions': instructions
            }
        else:
            return {'error': 'Не удалось добавить клиента на панель'}
    finally:
        await conn.close()

async def get_least_loaded_server(conn):
    least_loaded_server_id = None
    min_load_percentage = float('inf') 

    for server_id, server in SERVERS.items():
        count = await conn.fetchval('SELECT COUNT(*) FROM keys WHERE server_id = $1', server_id)
        percent_full = (count / 60) * 100 if count <= 60 else 100 
        if percent_full < min_load_percentage:
            min_load_percentage = percent_full
            least_loaded_server_id = server_id

    return least_loaded_server_id

import asyncpg

from config import CLUSTERS, DATABASE_URL  # Импортируем конфиг с серверами
from logger import logger


async def sync_servers_with_db():
    """
    Синхронизирует сервера из конфигурации CLUSTERS с базой данных.
    """
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        logger.info("Подключение к базе данных для синхронизации серверов успешно.")

        for cluster_name, servers in CLUSTERS.items():
            for server_key, server_info in servers.items():
                # Проверяем, существует ли сервер в базе данных
                exists = await conn.fetchval(
                    """
                    SELECT 1 FROM servers
                    WHERE cluster_name = $1 AND server_name = $2
                    """,
                    cluster_name,
                    server_info["name"],
                )

                # Если сервера нет, добавляем его
                if not exists:
                    await conn.execute(
                        """
                        INSERT INTO servers (cluster_name, server_name, api_url, subscription_url, inbound_id)
                        VALUES ($1, $2, $3, $4, $5)
                        """,
                        cluster_name,
                        server_info["name"],
                        server_info["API_URL"],
                        server_info["SUBSCRIPTION"],
                        server_info["INBOUND_ID"],
                    )
                    logger.info(f"Сервер {server_info['name']} из кластера {cluster_name} добавлен в базу данных.")
                else:
                    logger.info(f"Сервер {server_info['name']} из кластера {cluster_name} уже существует.")

    except Exception as e:
        logger.error(f"Ошибка при синхронизации серверов: {e}")
    finally:
        await conn.close()

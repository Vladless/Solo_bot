import asyncpg

from config import DATABASE_URL
from logger import logger

try:
    from config import CLUSTERS
except ImportError:
    CLUSTERS = None
    logger.warning("Переменная CLUSTERS не найдена в конфигурации. Добавьте сервера через админ-панель!")


async def sync_servers_with_db():
    """
    Синхронизирует сервера из конфигурации CLUSTERS с базой данных.
    Если CLUSTERS не найден, синхронизация не будет выполнена.
    """
    if CLUSTERS is None:
        logger.info("Конфигурация CLUSTERS не найдена. Синхронизация не будет выполнена.")
        return

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        logger.info("Подключение к базе данных для синхронизации серверов успешно.")

        for cluster_name, servers in CLUSTERS.items():
            for server_key, server_info in servers.items():
                exists = await conn.fetchval(
                    """
                    SELECT 1 FROM servers
                    WHERE cluster_name = $1 AND server_name = $2
                    """,
                    cluster_name,
                    server_info["name"],
                )

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
        if 'conn' in locals():
            await conn.close()

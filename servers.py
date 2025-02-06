import asyncio
import re
from datetime import datetime, timedelta

import asyncpg
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import ADMIN_ID, DATABASE_URL, PING_TIME
from ping3 import ping

from bot import bot
from database import check_unique_server_name, create_server, get_servers
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
            for _server_key, server_info in servers.items():
                exists = await check_unique_server_name(server_info["name"], conn, cluster_name)

                if not exists:
                    await create_server(
                        cluster_name=cluster_name,
                        server_name=server_info["name"],
                        api_url=server_info["API_URL"],
                        subscription_url=server_info["SUBSCRIPTION"],
                        inbound_id=server_info["INBOUND_ID"],
                        session=conn,
                    )
                    logger.info(f"Сервер {server_info['name']} из кластера {cluster_name} добавлен в базу данных.")
                else:
                    logger.info(f"Сервер {server_info['name']} из кластера {cluster_name} уже существует.")

    except Exception as e:
        logger.error(f"Ошибка при синхронизации серверов: {e}")
    finally:
        if "conn" in locals():
            await conn.close()


last_ping_times = {}
last_notification_times = {}


async def ping_server(server_ip: str) -> bool:
    """
    Функция пинга сервера.
    Возвращает True, если сервер доступен, иначе False.
    """
    try:
        logger.debug(f"Пингуем сервер {server_ip}...")
        response = ping(server_ip, timeout=3)
        if response is False:
            logger.warning(f"Сервер {server_ip} не отвечает.")
            return False
        return True
    except Exception as e:
        logger.error(f"Ошибка при пинге сервера {server_ip}: {e}")
        return False


async def notify_admin(server_name: str):
    """
    Отправляет уведомление всем администраторам о недоступности сервера.
    Уведомления отправляются не чаще чем раз в 3 минуты.
    """
    try:
        current_time = datetime.now()
        last_notification_time = last_notification_times.get(server_name)

        if last_notification_time and current_time - last_notification_time < timedelta(minutes=3):
            logger.info(f"Не отправляем уведомление для сервера {server_name}, так как прошло менее 3 минут.")
            return

        logger.info(f"Отправка уведомлений администратору о недоступности сервера {server_name}...")

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="Управление сервером", callback_data=f"manage_server|{server_name}"))

        for admin_id in ADMIN_ID:
            await bot.send_message(
                admin_id,
                (
                    f"❌ <b>Сервер '{server_name}'</b> не отвечает более 3 минут.\n\n"
                    "Проверьте соединение к серверу, подключение к панели или удалите его из таблицы серверов в боте, "
                    "чтобы не выдать подписку к неработающему серверу."
                ),
                reply_markup=builder.as_markup(),
            )
            logger.info(f"Уведомление отправлено администратору с ID {admin_id} о сервере {server_name}.")

        last_notification_times[server_name] = current_time
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления администраторам: {e}")


async def check_servers():
    """
    Периодическая проверка серверов с учетом извлечения хоста из `api_url`.
    """
    while True:
        servers = await get_servers()
        current_time = datetime.now()

        logger.info(f"Начинаю проверку серверов: {current_time}")

        for cluster_name, cluster_servers in servers.items():
            logger.debug(f"Проверка кластеров: {cluster_name}")
            for server in cluster_servers:
                original_api_url = server["api_url"]
                server_name = server["server_name"]

                server_host = extract_host(original_api_url)
                logger.debug(f"Проверка доступности сервера '{server_name}' с хостом {server_host}")

                is_online = await ping_server(server_host)

                if is_online:
                    last_ping_times[server_name] = current_time
                else:
                    last_ping_time = last_ping_times.get(server_name)
                    if last_ping_time and current_time - last_ping_time > timedelta(minutes=3):
                        logger.warning(f"Сервер {server_name} не отвечает более 3 минут. Отправляю уведомление.")
                        await notify_admin(server_name)
                    elif not last_ping_time:
                        last_ping_times[server_name] = current_time
                        logger.info(f"Сервер {server_name} не отвечал ранее, но теперь зарегистрирован.")

        logger.info("Завершена проверка всех серверов.")
        await asyncio.sleep(PING_TIME)


def extract_host(api_url: str) -> str:
    """
    Извлекает только хост из `api_url` (без путей, портов и параметров).
    """
    match = re.match(r"(https?://)?([^:/]+)", api_url)
    if match:
        host = match.group(2)
        logger.debug(f"Извлечён хост: {host} из URL: {api_url}")
        return host
    logger.error(f"Не удалось извлечь хост из URL: {api_url}")
    return api_url

import base64
from datetime import datetime

import aiohttp
from aiohttp import web
import asyncpg

from config import DATABASE_URL, TRANSITION_DATE_STR
from database import get_servers_from_db
from logger import logger


async def fetch_url_content(url, tg_id):
    try:
        logger.info(f"Получение URL: {url} для tg_id: {tg_id}")
        async with aiohttp.ClientSession() as session:
            async with session.get(url, ssl=False) as response:
                if response.status == 200:
                    content = await response.text()
                    logger.info(f"Успешно получен контент с {url} для tg_id: {tg_id}")
                    return base64.b64decode(content).decode("utf-8").split("\n")
                else:
                    logger.error(f"Не удалось получить {url} для tg_id: {tg_id}, статус: {response.status}")
                    return []
    except Exception as e:
        logger.error(f"Ошибка при получении {url} для tg_id: {tg_id}: {e}")
        return []


async def combine_unique_lines(urls, tg_id, query_string):
    all_lines = []
    logger.info(f"Начинаем объединение подписок для tg_id: {tg_id}, запрос: {query_string}")

    urls_with_query = [f"{url}?{query_string}" for url in urls]
    logger.info(f"Составлены URL-адреса: {urls_with_query}")

    for url in urls_with_query:
        lines = await fetch_url_content(url, tg_id)
        all_lines.extend(lines)

    all_lines = list(set(filter(None, all_lines)))
    logger.info(f"Объединено {len(all_lines)} строк после фильтрации и удаления дубликатов для tg_id: {tg_id}")

    return all_lines


transition_date = datetime.strptime(TRANSITION_DATE_STR, "%Y-%m-%d %H:%M:%S")

transition_timestamp_ms = int(transition_date.timestamp() * 1000)

transition_timestamp_ms_adjusted = transition_timestamp_ms - (3 * 60 * 60 * 1000)

logger.info(f"Время перехода (с поправкой на часовой пояс): {transition_timestamp_ms_adjusted}")


async def handle_old_subscription(request):
    email = request.match_info.get("email")

    if not email:
        logger.warning("Получен запрос без email")
        return web.Response(
            text="❌ Неверные параметры запроса. Требуется email.",
            status=400,
        )

    logger.info(f"Обработка запроса для старого клиента с email: {email}")

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        key_info = await conn.fetchrow("SELECT created_at FROM keys WHERE email = $1", email)

        if not key_info:
            logger.warning(f"Клиент с email {email} не найден в базе.")
            return web.Response(
                text="❌ Клиент с таким email не найден.",
                status=404,
            )

        created_at_ms = key_info["created_at"]
        logger.info(f"Значение created_at для клиента с email {email}: {created_at_ms}")

        created_at_datetime = datetime.utcfromtimestamp(created_at_ms / 1000)
        logger.info(f"Время создания клиента в формате datetime (UTC): {created_at_datetime}")

        if created_at_ms >= transition_timestamp_ms_adjusted:
            logger.info(f"Клиент с email {email} является новым.")
            return web.Response(
                text="❌ Эта ссылка устарела. Пожалуйста, обновите ссылку.",
                status=400,
            )

        servers = await get_servers_from_db()

        urls = []
        for cluster_name, cluster_servers in servers.items():
            for server in cluster_servers:
                server_subscription_url = f"{server['subscription_url']}/{email}"
                urls.append(server_subscription_url)

        combined_subscriptions = await combine_unique_lines(urls, email, "")

        base64_encoded = base64.b64encode("\n".join(combined_subscriptions).encode("utf-8")).decode("utf-8")

        headers = {
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Disposition": "inline",
            "profile-update-interval": "7",
            "profile-title": email,
        }

        logger.info(f"Возвращаем объединенные подписки для email: {email}")
        return web.Response(text=base64_encoded, headers=headers)

    finally:
        await conn.close()


async def handle_new_subscription(request):
    email = request.match_info.get("email")
    tg_id = request.match_info.get("tg_id")

    if not email or not tg_id:
        logger.warning("Получен запрос с отсутствующими параметрами email или tg_id")
        return web.Response(
            text="❌ Неверные параметры запроса. Требуются email и tg_id.",
            status=400,
        )

    logger.info(f"Обработка запроса для нового клиента: email={email}, tg_id={tg_id}")

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        client_data = await conn.fetchrow("SELECT tg_id FROM keys WHERE email = $1", email)

        if not client_data:
            logger.warning(f"Клиент с email {email} не найден в базе.")
            return web.Response(
                text="❌ Клиент с таким email не найден.",
                status=404,
            )

        stored_tg_id = client_data["tg_id"]

        if str(tg_id) != str(stored_tg_id):
            logger.warning(f"Неверный tg_id для клиента с email {email}.")
            return web.Response(
                text="❌ Неверные данные. Получите свой ключ в боте.",
                status=403,
            )
    finally:
        await conn.close()

    servers = await get_servers_from_db()

    urls = []
    for cluster_name, cluster_servers in servers.items():
        for server in cluster_servers:
            server_subscription_url = f"{server['subscription_url']}/{email}"
            urls.append(server_subscription_url)

    query_string = request.query_string
    logger.info(f"Извлечен query string: {query_string}")

    combined_subscriptions = await combine_unique_lines(urls, tg_id, query_string)

    base64_encoded = base64.b64encode("\n".join(combined_subscriptions).encode("utf-8")).decode("utf-8")

    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "Content-Disposition": "inline",
        "profile-update-interval": "7",
        "profile-title": email,
    }

    logger.info(f"Возвращаем объединенные подписки для email: {email}")
    return web.Response(text=base64_encoded, headers=headers)

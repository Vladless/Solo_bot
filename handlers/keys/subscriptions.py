import asyncio
import base64
from datetime import datetime

import aiohttp
import asyncpg
from aiohttp import web

from config import DATABASE_URL, PROJECT_NAME, SUB_MESSAGE, SUPERNODE, TRANSITION_DATE_STR
from database import get_servers_from_db
from logger import logger


async def fetch_url_content(url, tg_id):
    try:
        logger.info(f"Получение URL: {url} для tg_id: {tg_id}")
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, ssl=False) as response:
                if response.status == 200:
                    content = await response.text()
                    logger.info(f"Успешно получен контент с {url} для tg_id: {tg_id}")
                    return base64.b64decode(content).decode("utf-8").split("\n")
                else:
                    logger.error(
                        f"Не удалось получить {url} для tg_id: {tg_id}, статус: {response.status}"
                    )
                    return []
    except asyncio.TimeoutError:
        logger.error(f"Таймаут при получении {url} для tg_id: {tg_id}")
        return []
    except Exception as e:
        logger.error(f"Ошибка при получении {url} для tg_id: {tg_id}: {e}")
        return []


async def combine_unique_lines(urls, tg_id, query_string):
    if SUPERNODE:
        logger.info(f"Режим SUPERNODE активен. Возвращаем первую ссылку для tg_id: {tg_id}")
        urls_with_query = [f"{urls[0]}?{query_string}"] if urls else []
        return await fetch_url_content(urls_with_query[0], tg_id) if urls_with_query else []

    all_lines = []
    logger.info(
        f"Начинаем объединение подписок для tg_id: {tg_id}, запрос: {query_string}"
    )

    urls_with_query = [f"{url}?{query_string}" for url in urls]
    logger.info(f"Составлены URL-адреса: {urls_with_query}")

    for url in urls_with_query:
        lines = await fetch_url_content(url, tg_id)
        all_lines.extend(lines)

    all_lines = list(set(filter(None, all_lines)))
    logger.info(
        f"Объединено {len(all_lines)} строк после фильтрации и удаления дубликатов для tg_id: {tg_id}"
    )

    return all_lines


transition_date = datetime.strptime(TRANSITION_DATE_STR, "%Y-%m-%d %H:%M:%S")

transition_timestamp_ms = int(transition_date.timestamp() * 1000)

transition_timestamp_ms_adjusted = transition_timestamp_ms - (3 * 60 * 60 * 1000)

logger.info(
    f"Время перехода (с поправкой на часовой пояс): {transition_timestamp_ms_adjusted}"
)


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
        key_info = await conn.fetchrow(
            "SELECT created_at, server_id FROM keys WHERE email = $1", email
        )

        if not key_info:
            logger.warning(f"Клиент с email {email} не найден в базе.")
            return web.Response(
                text="❌ Клиент с таким email не найден.",
                status=404,
            )

        created_at_ms = key_info["created_at"]
        cluster_name = key_info.get("server_id")
        if not cluster_name:
            logger.warning(f"У клиента с email {email} отсутствует cluster_name.")
            return web.Response(
                text="❌ Устаревшие данные. Обратитесь в поддержку.",
                status=400,
            )

        logger.info(
            f"Значение created_at для клиента с email {email}: {created_at_ms}, кластер: {cluster_name}"
        )

        created_at_datetime = datetime.utcfromtimestamp(created_at_ms / 1000)
        logger.info(
            f"Время создания клиента в формате datetime (UTC): {created_at_datetime}"
        )

        if created_at_ms >= transition_timestamp_ms_adjusted:
            logger.info(f"Клиент с email {email} является новым.")
            return web.Response(
                text="❌ Эта ссылка устарела. Пожалуйста, обновите ссылку.",
                status=400,
            )

        servers = await get_servers_from_db()
        cluster_servers = servers.get(cluster_name, [])
        logger.info(f"Сервера в кластере: {cluster_servers}")

        urls = []
        for server in cluster_servers:
            server_subscription_url = f"{server['subscription_url']}/{email}"
            urls.append(server_subscription_url)

        combined_subscriptions = await combine_unique_lines(urls, email, "")

        base64_encoded = base64.b64encode(
            "\n".join(combined_subscriptions).encode("utf-8")
        ).decode("utf-8")

        encoded_project_name = f"{PROJECT_NAME} - {SUB_MESSAGE}"
        headers = {
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Disposition": "inline",
            "profile-update-interval": "7",
            "profile-title": "base64:"
            + base64.b64encode(encoded_project_name.encode("utf-8")).decode("utf-8"),
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

        client_data = await conn.fetchrow(
            "SELECT tg_id, server_id FROM keys WHERE email = $1", email
        )

        if not client_data:
            logger.warning(f"Клиент с email {email} не найден в базе.")
            return web.Response(
                text="❌ Клиент с таким email не найден.",
                status=404,
            )

        stored_tg_id = client_data["tg_id"]
        cluster_name = client_data["server_id"]

        if str(tg_id) != str(stored_tg_id):
            logger.warning(f"Неверный tg_id для клиента с email {email}.")
            return web.Response(
                text="❌ Неверные данные. Получите свой ключ в боте.",
                status=403,
            )

    finally:
        await conn.close()

    servers = await get_servers_from_db()
    cluster_servers = servers.get(cluster_name, [])

    urls = []
    for server in cluster_servers:
        server_subscription_url = f"{server['subscription_url']}/{email}"
        urls.append(server_subscription_url)

    query_string = request.query_string
    logger.info(f"Извлечен query string: {query_string}")

    combined_subscriptions = await combine_unique_lines(urls, tg_id, query_string)

    base64_encoded = base64.b64encode(
        "\n".join(combined_subscriptions).encode("utf-8")
    ).decode("utf-8")

    encoded_project_name = f"{PROJECT_NAME} - {SUB_MESSAGE}"

    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "Content-Disposition": "inline",
        "profile-update-interval": "7",
        "profile-title": "base64:"
        + base64.b64encode(encoded_project_name.encode("utf-8")).decode("utf-8"),
    }

    logger.info(f"Возвращаем объединенные подписки для email: {email}")
    return web.Response(text=base64_encoded, headers=headers)

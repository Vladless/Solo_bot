import asyncio
import base64
from datetime import datetime

import aiohttp
import asyncpg
from aiohttp import web
from config import DATABASE_URL, PROJECT_NAME, SUB_MESSAGE, SUPERNODE, TRANSITION_DATE_STR, USE_COUNTRY_SELECTION

from database import get_key_details, get_servers
from logger import logger

db_pool = None


async def init_db_pool():
    """Инициализация пула соединений, если он ещё не создан."""
    global db_pool
    if not db_pool:
        db_pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=5, max_size=20)


async def fetch_url_content(url, tg_id):
    """Получает содержимое подписки по URL и декодирует его."""
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
                    logger.error(f"Не удалось получить {url} для tg_id: {tg_id}, статус: {response.status}")
                    return []
    except TimeoutError:
        logger.error(f"Таймаут при получении {url} для tg_id: {tg_id}")
        return []
    except Exception as e:
        logger.error(f"Ошибка при получении {url} для tg_id: {tg_id}: {e}")
        return []


async def combine_unique_lines(urls, tg_id, query_string):
    """Объединяет строки подписки, удаляя дубликаты."""
    if SUPERNODE:
        logger.info(f"Режим SUPERNODE активен. Возвращаем первую ссылку для tg_id: {tg_id}")
        if not urls:
            return []
        url_with_query = f"{urls[0]}?{query_string}" if query_string else urls[0]
        return await fetch_url_content(url_with_query, tg_id)

    logger.info(f"Начинаем объединение подписок для tg_id: {tg_id}, запрос: {query_string}")

    urls_with_query = [f"{url}?{query_string}" if query_string else url for url in urls]
    logger.info(f"Составлены URL-адреса: {urls_with_query}")

    tasks = [fetch_url_content(url, tg_id) for url in urls_with_query]
    results = await asyncio.gather(*tasks)

    all_lines = set()
    for lines in results:
        all_lines.update(filter(None, lines))

    logger.info(f"Объединено {len(all_lines)} строк после фильтрации и удаления дубликатов для tg_id: {tg_id}")

    return list(all_lines)


transition_date = datetime.strptime(TRANSITION_DATE_STR, "%Y-%m-%d %H:%M:%S")
transition_timestamp_ms = int(transition_date.timestamp() * 1000)
transition_timestamp_ms_adjusted = transition_timestamp_ms - (3 * 60 * 60 * 1000)

logger.info(f"Время перехода (с поправкой на часовой пояс): {transition_timestamp_ms_adjusted}")


async def handle_subscription(request, old_subscription=False):
    """Обрабатывает запрос на подписку (старую или новую)."""
    email = request.match_info.get("email")
    tg_id = request.match_info.get("tg_id") if not old_subscription else None

    if not email or (not old_subscription and not tg_id):
        logger.warning("Получен запрос с отсутствующими параметрами")
        return web.Response(text="❌ Неверные параметры запроса.", status=400)

    logger.info(
        f"Обработка запроса для {'старого' if old_subscription else 'нового'} клиента: email={email}, tg_id={tg_id}"
    )

    await init_db_pool()

    async with db_pool.acquire() as conn:
        client_data = await get_key_details(email, conn)

        if not client_data:
            logger.warning(f"Клиент с email {email} не найден в базе.")
            return web.Response(text="❌ Клиент с таким email не найден.", status=404)

        stored_tg_id = client_data.get("tg_id")
        server_id = client_data["server_id"]  # В режиме выбора стран — это server_name, иначе — cluster_name

        if not old_subscription and str(tg_id) != str(stored_tg_id):
            logger.warning(f"Неверный tg_id для клиента с email {email}.")
            return web.Response(text="❌ Неверные данные. Получите свой ключ в боте.", status=403)

        if old_subscription:
            created_at_ms = client_data["created_at"]
            created_at_datetime = datetime.utcfromtimestamp(created_at_ms / 1000)

            logger.info(f"created_at для {email}: {created_at_datetime}, server_id: {server_id}")

            if created_at_ms >= transition_timestamp_ms_adjusted:
                logger.info(f"Клиент с email {email} является новым.")
                return web.Response(text="❌ Эта ссылка устарела. Пожалуйста, обновите ссылку.", status=400)

        urls = []

        if USE_COUNTRY_SELECTION:
            logger.info(f"Режим выбора страны активен. Ищем сервер {server_id} в БД.")
            server_data = await conn.fetchrow("SELECT subscription_url FROM servers WHERE server_name = $1", server_id)

            if not server_data:
                logger.warning(f"Не найден сервер {server_id} в БД!")
                return web.Response(text="❌ Сервер не найден.", status=404)

            subscription_url = server_data["subscription_url"]
            urls = [f"{subscription_url}/{email}"]
            logger.info(f"Используем подписку {urls[0]}")

        else:
            servers = await get_servers()
            logger.info(f"Режим выбора страны отключен. Используем кластер {server_id}.")
            cluster_servers = servers.get(server_id, [])

            if not cluster_servers:
                logger.warning(f"Не найдены сервера для {server_id}")
                return web.Response(text="❌ Сервер не найден.", status=404)

            urls = [f"{server['subscription_url']}/{email}" for server in cluster_servers]

        query_string = request.query_string if not old_subscription else ""
        combined_subscriptions = await combine_unique_lines(urls, tg_id or email, query_string)

        base64_encoded = base64.b64encode("\n".join(combined_subscriptions).encode("utf-8")).decode("utf-8")
        encoded_project_name = f"{PROJECT_NAME} - {SUB_MESSAGE}"

        headers = {
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Disposition": "inline",
            "profile-update-interval": "7",
            "profile-title": "base64:" + base64.b64encode(encoded_project_name.encode("utf-8")).decode("utf-8"),
        }

        logger.info(f"Возвращаем объединенные подписки для email: {email}")
        return web.Response(text=base64_encoded, headers=headers)


async def handle_old_subscription(request):
    """Обработка запроса для старых клиентов."""
    return await handle_subscription(request, old_subscription=True)


async def handle_new_subscription(request):
    """Обработка запроса для новых клиентов."""
    return await handle_subscription(request, old_subscription=False)

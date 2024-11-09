import base64
import logging

import aiohttp
from aiohttp import web

from config import SERVERS

logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def fetch_url_content(url):
    try:
        logger.debug(f"Получение URL: {url}")
        async with aiohttp.ClientSession() as session:
            async with session.get(url, ssl=False) as response:
                if response.status == 200:
                    content = await response.text()
                    logger.debug(f"Успешно получен контент с {url}")
                    return base64.b64decode(content).decode("utf-8").split("\n")
                else:
                    logger.error(
                        f"Не удалось получить {url}, статус: {response.status}"
                    )
                    return []
    except Exception as e:
        logger.error(f"Ошибка при получении {url}: {e}")
        return []


async def combine_unique_lines(urls, query_string):
    all_lines = []
    logger.debug(f"Начинаем объединение подписок для запроса: {query_string}")

    urls_with_query = [f"{url}?{query_string}" for url in urls]
    logger.debug(f"Составлены URL-адреса: {urls_with_query}")

    for url in urls_with_query:
        lines = await fetch_url_content(url)
        all_lines.extend(lines)

    all_lines = list(set(filter(None, all_lines)))
    logger.debug(
        f"Объединено {len(all_lines)} строк после фильтрации и удаления дубликатов"
    )

    return all_lines


async def handle_subscription(request):
    email = request.match_info["email"]
    logger.info(f"Получен запрос на подписку для email: {email}")

    urls = []
    for server in SERVERS.values():
        server_subscription_url = f"{server['SUBSCRIPTION']}/{email}"
        urls.append(server_subscription_url)

    query_string = request.query_string
    logger.debug(f"Извлечен query string: {query_string}")

    combined_subscriptions = await combine_unique_lines(urls, query_string)

    base64_encoded = base64.b64encode(
        "\n".join(combined_subscriptions).encode("utf-8")
    ).decode("utf-8")

    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "Content-Disposition": "inline",
        "profile-update-interval": "7",
        "profile-title": email,
    }

    logger.info(f"Возвращаем объединенные подписки для email: {email}")
    return web.Response(text=base64_encoded, headers=headers)

import asyncio
import base64
import random
import re
import time
import urllib.parse
from datetime import datetime

import aiohttp
import asyncpg
from aiohttp import web

from config import (
    DATABASE_URL, PROJECT_NAME, SUB_MESSAGE, SUPERNODE,
    TRANSITION_DATE_STR, USE_COUNTRY_SELECTION, SUPPORT_CHAT_URL, USERNAME_BOT, TOTAL_GB
)
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


async def get_subscription_urls(server_id: str, email: str, conn) -> list:
    """
    Универсальная функция, которая в зависимости от флага USE_COUNTRY_SELECTION
    получает список URL-адресов для подписки. Возвращает пустой список, если нужные данные не найдены.
    """
    if USE_COUNTRY_SELECTION:
        logger.info(f"Режим выбора страны активен. Ищем сервер {server_id} в БД.")
        server_data = await conn.fetchrow(
            "SELECT subscription_url FROM servers WHERE server_name = $1", server_id
        )
        if not server_data:
            logger.warning(f"Не найден сервер {server_id} в БД!")
            return []
        subscription_url = server_data["subscription_url"]
        urls = [f"{subscription_url}/{email}"]
        logger.info(f"Используем подписку {urls[0]}")
        return urls

    servers = await get_servers()
    logger.info(f"Режим выбора страны отключен. Используем кластер {server_id}.")
    cluster_servers = servers.get(server_id, [])
    if not cluster_servers:
        logger.warning(f"Не найдены сервера для {server_id}")
        return []
    urls = [f"{server['subscription_url']}/{email}" for server in cluster_servers]
    logger.info(f"Найдено {len(urls)} URL-адресов в кластере {server_id}")
    return urls


async def handle_subscription(request, old_subscription=False):
    """Обрабатывает запрос на подписку (старую или новую)."""
    email = request.match_info.get("email")
    tg_id = request.match_info.get("tg_id") if not old_subscription else None

    if not email or (not old_subscription and not tg_id):
        logger.warning("Получен запрос с отсутствующими параметрами")
        return web.Response(text="❌ Неверные параметры запроса.", status=400)

    logger.info(f"Обработка запроса для {'старого' if old_subscription else 'нового'} клиента: email={email}, tg_id={tg_id}")
    await init_db_pool()

    async with db_pool.acquire() as conn:
        client_data = await get_key_details(email, conn)
        if not client_data:
            logger.warning(f"Клиент с email {email} не найден в базе.")
            return web.Response(text="❌ Клиент с таким email не найден.", status=404)

        stored_tg_id = client_data.get("tg_id")
        server_id = client_data["server_id"]

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

        urls = await get_subscription_urls(server_id, email, conn)
        if not urls:
            return web.Response(text="❌ Сервер не найден.", status=404)

        query_string = request.query_string if not old_subscription else ""
        combined_subscriptions = await combine_unique_lines(urls, tg_id or email, query_string)
        random.shuffle(combined_subscriptions)

        time_left = None
        for line in combined_subscriptions:
            if "#" in line:
                try:
                    _, meta = line.split("#", 1)
                except ValueError:
                    continue
                parts = meta.split("-")
                candidate = parts[-1].strip() if parts else ""
                candidate_decoded = urllib.parse.unquote(candidate)
                m = re.search(
                    r'(?:(\d+)\s*[Dd]\s*,?\s*)?(\d+)\s*[Hh][^\d]*',
                    candidate_decoded,
                    re.IGNORECASE
                )
                if m:
                    d = int(m.group(1)) if m.group(1) else 0
                    h = int(m.group(2))
                    time_left = f"{d}D,{h}H ⏳" if d else f"{h}H ⏳"
                    break
        if not time_left:
            time_left = "N/A"

        cleaned_subscriptions = []
        for line in combined_subscriptions:
            if "#" in line:
                try:
                    base, meta = line.split("#", 1)
                except ValueError:
                    continue
                parts = meta.split("-")
                if SUPERNODE:
                    if parts:
                        country = parts[0]
                        if "_" in country:
                            country = country.split("_", 1)[1]
                        if len(parts) == 4:
                            meta_clean = country + "-" + parts[2]
                        elif len(parts) == 3:
                            meta_clean = country
                        else:
                            meta_clean = country
                    else:
                        meta_clean = ""
                else:
                    # Для SUPERNODE=False:
                    if len(parts) >= 4:
                        meta_clean = parts[0] + "-" + parts[2]
                    elif len(parts) == 3:
                        if re.search(r'\d+[DH]', parts[1], re.IGNORECASE):
                            meta_clean = parts[0]
                        else:
                            meta_clean = parts[0] + "-" + parts[1]
                    elif len(parts) == 2:
                        meta_clean = parts[0]
                    elif parts:
                        meta_clean = parts[0]
                    else:
                        meta_clean = ""
                cleaned_line = base + "#" + meta_clean
            else:
                cleaned_line = line
            cleaned_subscriptions.append(cleaned_line)

        final_subscriptions = cleaned_subscriptions
        base64_encoded = base64.b64encode("\n".join(final_subscriptions).encode("utf-8")).decode("utf-8")
        subscription_info = f"📄 Подписка: {email} - {time_left}"

        user_agent = request.headers.get("User-Agent", "")
        if "Happ" in user_agent:
            encoded_project_name = f"{PROJECT_NAME}"
            support_username = SUPPORT_CHAT_URL.split("https://t.me/")[-1]
            announce_str = f"↖️Бот | {subscription_info} | Поддержка↗️"

            expire_timestamp = 0
            m_expire = re.search(r'(?:(\d+)D,)?(\d+)H', time_left)
            if m_expire:
                d = int(m_expire.group(1)) if m_expire.group(1) else 0
                h = int(m_expire.group(2))
                expire_timestamp = int(time.time() + d * 86400 + h * 3600)

            if TOTAL_GB != 0:
                country_remaining = {}
                for line in combined_subscriptions:
                    if "#" not in line:
                        continue
                    try:
                        _, meta = line.split("#", 1)
                    except ValueError:
                        continue
                    parts = meta.split("-")
                    if len(parts) == 4:
                        remaining_str = parts[2]
                    elif len(parts) == 3:
                        remaining_str = parts[1]
                    else:
                        remaining_str = ""
                    if remaining_str:
                        remaining_str = urllib.parse.unquote(remaining_str)
                        remaining_str = remaining_str.replace(',', '.')
                        remaining_str = re.sub(r'[^0-9\.GMKB]', '', remaining_str)
                        m_total = re.search(r'([\d\.]+)([GMK]B)', remaining_str, re.IGNORECASE)
                        if m_total:
                            value = float(m_total.group(1))
                            unit = m_total.group(2).upper()
                            if unit == "GB":
                                remaining_bytes = int(value * 1073741824)
                            elif unit == "MB":
                                remaining_bytes = int(value * 1048576)
                            elif unit == "KB":
                                remaining_bytes = int(value * 1024)
                            else:
                                remaining_bytes = int(value)
                            country = parts[0].strip()
                            country_remaining[country] = remaining_bytes
                num_countries = len(country_remaining)
                issued_per_country = TOTAL_GB
                total_traffic_bytes = issued_per_country * num_countries
                consumed_traffic_bytes = total_traffic_bytes - sum(country_remaining.values())
                if consumed_traffic_bytes < 0:
                    consumed_traffic_bytes = 0
            else:
                consumed_traffic_bytes = 0
                total_traffic_bytes = 0

            subscription_userinfo = f"upload=0; download={consumed_traffic_bytes}; total={total_traffic_bytes}; expire={expire_timestamp}"
            
            headers = {
                "Content-Type": "text/plain; charset=utf-8",
                "Content-Disposition": "inline",
                "profile-update-interval": "3",
                "profile-title": "base64:" + base64.b64encode(encoded_project_name.encode("utf-8")).decode("utf-8"),
                "support-url": SUPPORT_CHAT_URL,
                "announce": "base64:" + base64.b64encode(announce_str.encode("utf-8")).decode("utf-8"),
                "profile-web-page-url": f"https://t.me/{USERNAME_BOT}",
                "subscription-userinfo": subscription_userinfo
            }
        else:
            encoded_project_name = f"{PROJECT_NAME}\n{subscription_info}"
            headers = {
                "Content-Type": "text/plain; charset=utf-8",
                "Content-Disposition": "inline",
                "profile-update-interval": "3",
                "profile-title": "base64:" + base64.b64encode(encoded_project_name.encode("utf-8")).decode("utf-8")
            }

        logger.info(f"Возвращаем объединенные подписки для email: {email}")
        return web.Response(text=base64_encoded, headers=headers)

async def handle_old_subscription(request):
    """Обработка запроса для старых клиентов."""
    return await handle_subscription(request, old_subscription=True)


async def handle_new_subscription(request):
    """Обработка запроса для новых клиентов."""
    return await handle_subscription(request, old_subscription=False)

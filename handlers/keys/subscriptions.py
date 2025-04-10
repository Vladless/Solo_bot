import asyncio
import base64
import random
import re
import time
import urllib.parse

from datetime import datetime

import aiohttp
import asyncpg
import pytz

from aiohttp import web

from config import (
    DATABASE_URL,
    PROJECT_NAME,
    SUPERNODE,
    SUPPORT_CHAT_URL,
    TOTAL_GB,
    TRANSITION_DATE_STR,
    USERNAME_BOT,
    USE_COUNTRY_SELECTION,
)
from database import get_key_details, get_servers
from handlers.utils import convert_to_bytes
from logger import logger


async def fetch_url_content(url: str, identifier: str) -> list[str]:
    """
    Получает содержимое подписки по URL и декодирует его.

    Args:
        url: URL для получения содержимого
        identifier: Идентификатор пользователя (tg_id или email)

    Returns:
        Список строк из подписки
    """
    try:
        logger.info(f"Получение URL: {url} для идентификатора: {identifier}")
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, ssl=False) as response:
                if response.status == 200:
                    content = await response.text()
                    logger.info(f"Успешно получен контент с {url} для идентификатора: {identifier}")
                    return base64.b64decode(content).decode("utf-8").split("\n")
                else:
                    logger.error(
                        f"Не удалось получить {url} для идентификатора: {identifier}, статус: {response.status}"
                    )
                    return []
    except TimeoutError:
        logger.error(f"Таймаут при получении {url} для идентификатора: {identifier}")
        return []
    except Exception as e:
        logger.error(f"Ошибка при получении {url} для идентификатора: {identifier}: {e}")
        return []


async def combine_unique_lines(urls: list[str], identifier: str, query_string: str) -> list[str]:
    """
    Объединяет строки подписки из нескольких URL, удаляя дубликаты.

    Args:
        urls: Список URL для получения подписок
        identifier: Идентификатор пользователя (tg_id или email)
        query_string: Строка запроса для добавления к URL

    Returns:
        Список уникальных строк из всех подписок
    """
    if SUPERNODE:
        logger.info(f"Режим SUPERNODE активен. Возвращаем первую ссылку для идентификатора: {identifier}")
        if not urls:
            return []
        url_with_query = f"{urls[0]}?{query_string}" if query_string else urls[0]
        return await fetch_url_content(url_with_query, identifier)

    logger.info(f"Начинаем объединение подписок для идентификатора: {identifier}, запрос: {query_string}")
    urls_with_query = [f"{url}?{query_string}" if query_string else url for url in urls]
    logger.info(f"Составлены URL-адреса: {urls_with_query}")

    tasks = [fetch_url_content(url, identifier) for url in urls_with_query]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    all_lines = set()
    for lines in results:
        all_lines.update(filter(None, lines))
    logger.info(
        f"Объединено {len(all_lines)} строк после фильтрации и удаления дубликатов для идентификатора: {identifier}"
    )
    return list(all_lines)


async def get_subscription_urls(server_id: str, email: str, conn, include_remnawave_key: str = None) -> list[str]:
    urls = []
    if USE_COUNTRY_SELECTION:
        logger.info(f"[Sub] Страна-режим активен. Ищем сервер {server_id}.")
        server_data = await conn.fetchrow("SELECT subscription_url FROM servers WHERE server_name = $1", server_id)
        if server_data and server_data["subscription_url"]:
            urls.append(f"{server_data['subscription_url']}/{email}")
    else:
        servers = await get_servers(conn)
        cluster_servers = servers.get(server_id, [])
        for server in cluster_servers:
            if url := server.get("subscription_url"):
                urls.append(f"{url}/{email}")

    if include_remnawave_key:
        urls.append(include_remnawave_key)
        logger.info(f"[Sub] Добавлена Remnawave ссылка: {include_remnawave_key}")

    logger.info(f"[Sub] Список URL подписок: {urls}")
    return urls


def get_transition_timestamp() -> int:
    """
    Получает временную метку перехода с учетом часового пояса Москвы.

    Returns:
        Временная метка перехода в миллисекундах
    """
    moscow_tz = pytz.timezone("Europe/Moscow")
    transition_date_naive = datetime.strptime(TRANSITION_DATE_STR, "%Y-%m-%d %H:%M:%S")
    transition_date = moscow_tz.localize(transition_date_naive)
    transition_timestamp_ms = int(transition_date.timestamp() * 1000)
    return transition_timestamp_ms


def calculate_traffic(cleaned_subscriptions: list[str], expiry_time_ms: int | None) -> str:
    """
    Рассчитывает информацию о трафике на основе подписок.

    Args:
        cleaned_subscriptions: Список строк подписки
        expiry_time_ms: Время истечения подписки в миллисекундах

    Returns:
        Строка с информацией о трафике
    """
    expire_timestamp = int(expiry_time_ms / 1000) if expiry_time_ms else 0

    if TOTAL_GB != 0:
        country_remaining = {}
        for line in cleaned_subscriptions:
            if "#" not in line:
                continue

            try:
                _, meta = line.split("#", 1)
            except ValueError:
                continue

            parts = meta.split("-")
            country = parts[0].strip()
            remaining_str = parts[1].strip() if len(parts) == 2 else ""

            if remaining_str:
                remaining_str = remaining_str.replace(",", ".")
                m_total = re.search(r"([\d\.]+)\s*([GMKTB]B)", remaining_str, re.IGNORECASE)
                if m_total:
                    value = float(m_total.group(1))
                    unit = m_total.group(2).upper()
                    remaining_bytes = convert_to_bytes(value, unit)
                    country_remaining[country] = remaining_bytes

        num_countries = len(country_remaining)
        issued_per_country = TOTAL_GB
        total_traffic_bytes = issued_per_country * num_countries
        consumed_traffic_bytes = total_traffic_bytes - sum(country_remaining.values())

        if consumed_traffic_bytes < 0:
            consumed_traffic_bytes = 0
    else:
        consumed_traffic_bytes = 1
        total_traffic_bytes = 0

    return f"upload=0; download={consumed_traffic_bytes}; total={total_traffic_bytes}; expire={expire_timestamp}"


def clean_subscription_line(line: str) -> str:
    """
    Очищает строку подписки, оставляя только нужную информацию.

    Args:
        line: Исходная строка подписки

    Returns:
        Очищенная строка подписки
    """
    if "#" not in line:
        return line

    try:
        base, meta = line.split("#", 1)
    except ValueError:
        return line

    parts = meta.split("-")
    country = parts[0].strip() if parts else ""
    traffic = ""

    for part in parts[1:]:
        part_decoded = urllib.parse.unquote(part).strip()
        if re.search(r"\d+(?:[.,]\d+)?\s*(?:GB|MB|KB|TB)", part_decoded, re.IGNORECASE):
            traffic = part_decoded
            break

    meta_clean = f"{country} - {traffic}" if traffic else country
    return base + "#" + meta_clean


def format_time_left(expiry_time_ms: int | None) -> str:
    """
    Форматирует оставшееся время подписки.

    Args:
        expiry_time_ms: Время истечения подписки в миллисекундах

    Returns:
        Отформатированная строка с оставшимся временем
    """
    if not expiry_time_ms:
        return "N/A"

    now_ms = int(time.time() * 1000)
    remaining_sec = max((expiry_time_ms - now_ms) / 1000, 0)
    days = int(remaining_sec // 86400)
    hours = int((remaining_sec % 86400) // 3600)

    return f"{days}D,{hours}H ⏳" if days else f"{hours}H ⏳"


def prepare_headers(
    user_agent: str, project_name: str, subscription_info: str, subscription_userinfo: str
) -> dict[str, str]:
    """
    Подготавливает заголовки ответа в зависимости от User-Agent клиента.

    Args:
        user_agent: User-Agent клиента
        project_name: Название проекта
        subscription_info: Информация о подписке
        subscription_userinfo: Информация о трафике

    Returns:
        Словарь с заголовками ответа
    """
    if "Happ" in user_agent:
        encoded_project_name = f"{project_name}"
        announce_str = f"↖️Бот | {subscription_info} | Поддержка↗️"
        return {
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Disposition": "inline",
            "profile-update-interval": "3",
            "profile-title": "base64:" + base64.b64encode(encoded_project_name.encode("utf-8")).decode("utf-8"),
            "support-url": SUPPORT_CHAT_URL,
            "announce": "base64:" + base64.b64encode(announce_str.encode("utf-8")).decode("utf-8"),
            "profile-web-page-url": f"https://t.me/{USERNAME_BOT}",
            "subscription-userinfo": subscription_userinfo,
        }
    elif "Hiddify" in user_agent:
        parts = subscription_info.split(" - ")[0].split(": ")
        key_info = parts[1] if len(parts) > 1 else parts[0]

        encoded_project_name = f"{project_name}\n📄 Подписка: {key_info}"
        return {
            "profile-update-interval": "3",
            "profile-title": "base64:" + base64.b64encode(encoded_project_name.encode("utf-8")).decode("utf-8"),
            "subscription-userinfo": subscription_userinfo,
        }
    else:
        encoded_project_name = f"{project_name}\n{subscription_info}"
        return {
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Disposition": "inline",
            "profile-update-interval": "3",
            "profile-title": "base64:" + base64.b64encode(encoded_project_name.encode("utf-8")).decode("utf-8"),
        }


async def handle_subscription(request: web.Request, old_subscription: bool = False) -> web.Response:
    """
    Обрабатывает запрос на подписку (старую или новую).

    Args:
        request: Объект запроса
        old_subscription: Флаг, указывающий на тип подписки (старая или новая)

    Returns:
        Ответ с подпиской или сообщением об ошибке
    """
    email = request.match_info.get("email")
    tg_id = request.match_info.get("tg_id") if not old_subscription else None

    if not email or (not old_subscription and not tg_id):
        logger.warning("Получен запрос с отсутствующими параметрами")
        return web.Response(text="❌ Неверные параметры запроса.", status=400)

    logger.info(
        f"Обработка запроса для {'старого' if old_subscription else 'нового'} клиента: email={email}, tg_id={tg_id}"
    )

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        client_data = await get_key_details(email, conn)
        if not client_data:
            logger.warning(f"Клиент с email {email} не найден в базе.")
            return web.Response(text="❌ Клиент с таким email не найден.", status=404)

        stored_tg_id = client_data.get("tg_id")
        server_id = client_data["server_id"]

        if not old_subscription and int(tg_id) != int(stored_tg_id):
            logger.warning(f"Неверный tg_id для клиента с email {email}.")
            return web.Response(text="❌ Неверные данные. Получите свой ключ в боте.", status=403)

        if old_subscription:
            created_at_ms = client_data["created_at"]
            created_at_datetime = datetime.utcfromtimestamp(created_at_ms / 1000)
            logger.info(f"created_at для {email}: {created_at_datetime}, server_id: {server_id}")

            transition_timestamp_ms = get_transition_timestamp()
            logger.info(f"Время перехода (с учетом часового пояса Москвы): {transition_timestamp_ms}")

            if created_at_ms >= transition_timestamp_ms:
                logger.info(f"Клиент с email {email} является новым.")
                return web.Response(text="❌ Эта ссылка устарела. Пожалуйста, обновите ссылку.", status=400)

        expiry_time_ms = client_data.get("expiry_time")
        time_left = format_time_left(expiry_time_ms)

        urls = await get_subscription_urls(
    server_id, email, conn, include_remnawave_key=client_data.get("remnawave_link")
)

        if not urls:
            return web.Response(text="❌ Сервер не найден.", status=404)

        query_string = request.query_string if not old_subscription else ""
        combined_subscriptions = await combine_unique_lines(urls, tg_id or email, query_string)
        random.shuffle(combined_subscriptions)

        cleaned_subscriptions = [clean_subscription_line(line) for line in combined_subscriptions]

        base64_encoded = base64.b64encode("\n".join(cleaned_subscriptions).encode("utf-8")).decode("utf-8")
        subscription_info = f"📄 Подписка: {email} - {time_left}"

        user_agent = request.headers.get("User-Agent", "")
        subscription_userinfo = calculate_traffic(cleaned_subscriptions, expiry_time_ms)
        headers = prepare_headers(user_agent, PROJECT_NAME, subscription_info, subscription_userinfo)

        logger.info(f"Возвращаем объединенные подписки для email: {email}")
        return web.Response(text=base64_encoded, headers=headers)
    finally:
        await conn.close()


async def handle_old_subscription(request: web.Request) -> web.Response:
    """Обработка запроса для старых клиентов."""
    return await handle_subscription(request, old_subscription=True)


async def handle_new_subscription(request: web.Request) -> web.Response:
    """Обработка запроса для новых клиентов."""
    return await handle_subscription(request, old_subscription=False)

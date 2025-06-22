import asyncio
import base64
import random
import re
import time
import urllib.parse

import aiohttp
from aiohttp import web
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import (
    PROJECT_NAME,
    RANDOM_SUBSCRIPTIONS,
    SUPERNODE,
    SUPPORT_CHAT_URL,
    USE_COUNTRY_SELECTION,
    USERNAME_BOT,
)
from database import get_key_details, get_servers
from database.models import Server
from handlers.utils import convert_to_bytes
from logger import logger


async def fetch_url_content(
    url: str, identifier: str
) -> tuple[list[str], dict[str, str]]:
    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, ssl=False) as response:
                if response.status == 200:
                    content = await response.text()
                    lines = base64.b64decode(content).decode("utf-8").split("\n")
                    headers = {k.lower(): v for k, v in response.headers.items()}
                    logger.debug(
                        f"Fetched {url}: {len(lines)} lines, headers: {headers}"
                    )
                    return lines, headers
                return [], {}
    except Exception as e:
        logger.error(f"Error fetching URL {url}: {e}")
        return [], {}


async def combine_subscription_lines(urls: list[str]) -> list[str]:
    combined = []
    for url in urls:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        content = await response.text(encoding='utf-8')  # Ensure UTF-8 encoding
                        for line in content.splitlines():
                            line = line.strip()
                            if line and not any(line.startswith(proto) for proto in ("vmess://", "trojan://", "ss://", "ssr://")):
                                combined.append(line)
        except Exception as e:
            logger.error(f"Error fetching subscription from {url}: {e}")
    return combined


async def combine_unique_lines(
    urls: list[str], identifier: str, query_string: str
) -> tuple[list[str], list[dict[str, str]]]:
    if SUPERNODE:
        logger.info(
            f"Режим SUPERNODE активен. Возвращаем первую ссылку для идентификатора: {identifier}"
        )
        if not urls:
            return [], []
        url_with_query = f"{urls[0]}?{query_string}" if query_string else urls[0]
        lines, headers = await fetch_url_content(url_with_query, identifier)
        return lines, [headers]

    urls_with_query = [f"{url}?{query_string}" if query_string else url for url in urls]
    tasks = [fetch_url_content(url, identifier) for url in urls_with_query]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    all_lines = set()
    all_headers = []
    for result in results:
        if isinstance(result, tuple):
            lines, headers = result
            all_lines.update(filter(None, lines))
            all_headers.append(headers)
    return list(all_lines), all_headers


async def get_subscription_urls(
    server_id: str, email: str, session: AsyncSession, include_remnawave_key: str = None
) -> list[str]:
    urls = []
    if USE_COUNTRY_SELECTION:
        result = await session.execute(
            select(Server.subscription_url).where(Server.server_name == server_id)
        )
        server_data = result.scalar()
        if server_data:
            urls.append(f"{server_data}/{email}")
    else:
        servers = await get_servers(session)
        cluster_servers = servers.get(server_id, [])
        for server in cluster_servers:
            if url := server.get("subscription_url"):
                urls.append(f"{url}/{email}")

    if include_remnawave_key:
        urls.append(include_remnawave_key)

    return urls


def calculate_traffic(
    cleaned_subscriptions: list[str],
    expiry_time_ms: int | None,
    headers_list: list[dict[str, str]],
) -> str:
    logger.debug(
        f"Calculating traffic with subscriptions: {cleaned_subscriptions}, headers: {headers_list}"
    )
    expire_timestamp = int(expiry_time_ms / 1000) if expiry_time_ms else 0

    upload = 0
    download = 0
    total = 0
    for headers in headers_list:
        userinfo = headers.get("subscription-userinfo", "")
        if userinfo:
            parts = userinfo.split(";")
            for part in parts:
                part = part.strip()
                if part.startswith("upload="):
                    upload += int(part.split("=")[1])
                elif part.startswith("download="):
                    download += int(part.split("=")[1])
                elif part.startswith("total="):
                    total += int(part.split("=")[1])
            logger.debug(f"Processed Subscription-Userinfo: {userinfo}")

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
                logger.debug(f"Found traffic: {value}{unit} for {country}")

    consumed_traffic_bytes = (
        total - sum(country_remaining.values()) if country_remaining else download
    )
    if consumed_traffic_bytes < 0:
        consumed_traffic_bytes = 0
    download = max(download, consumed_traffic_bytes)

    if download == 0 and total == 0 and not country_remaining:
        download = 1

    result = f"upload={upload}; download={download}; total={total}; expire={expire_timestamp}"
    logger.debug(f"Calculated subscription-userinfo: {result}")
    return result


def clean_subscription_line(line: str) -> str:
    if not line:
        return line

    # First, URL-decode the entire line to handle any URL-encoded characters
    try:
        import urllib.parse
        decoded_line = urllib.parse.unquote(line)
    except Exception as e:
        logger.error(f"Error URL-decoding line: {e}")
        decoded_line = line

    # Handle lines with metadata (after #)
    if "#" in decoded_line:
        try:
            base_part, meta_part = decoded_line.split("#", 1)
            base_part = base_part.strip()
            meta_part = meta_part.strip()
            
            # Process metadata (country and traffic)
            meta_parts = meta_part.split("-", 1)
            country = meta_parts[0].strip()
            traffic = ""
            
            if len(meta_parts) > 1:
                # Look for traffic pattern in the rest of the meta
                traffic_match = re.search(
                    r"(\d+(?:[.,]\d+)?\s*[GMK]?B)", 
                    meta_parts[1], 
                    re.IGNORECASE
                )
                if traffic_match:
                    traffic = traffic_match.group(1).strip()
            
            # Rebuild the line with proper formatting
            meta_clean = f"{country} - {traffic}" if traffic else country
            result = base_part
            if meta_clean:
                result += f" #{meta_clean}"
            return result.strip()
            
        except Exception as e:
            logger.error(f"Error parsing subscription line: {e}")
            return decoded_line
    
    # For lines without metadata, just clean up extra spaces
    return ' '.join(part for part in decoded_line.split() if part)


def format_time_left(expiry_time_ms: int | None) -> str:
    if not expiry_time_ms:
        return "N/A"
    now_ms = int(time.time() * 1000)
    remaining_sec = max((expiry_time_ms - now_ms) / 1000, 0)
    days = int(remaining_sec // 86400)
    hours = int((remaining_sec % 86400) // 3600)
    return f"{days}D,{hours}H ⏳" if days else f"{hours}H ⏳"


def prepare_headers(
    user_agent: str,
    project_name: str,
    subscription_info: str,
    subscription_userinfo: str,
) -> dict[str, str]:
    if "Happ" in user_agent:
        encoded_project_name = f"{project_name}"
        announce_str = f"↖️Бот | {subscription_info} | Поддержка↗️"
        return {
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Disposition": "inline",
            "profile-update-interval": "3",
            "profile-title": "base64:"
            + base64.b64encode(encoded_project_name.encode("utf-8")).decode("utf-8"),
            "support-url": SUPPORT_CHAT_URL,
            "announce": "base64:"
            + base64.b64encode(announce_str.encode("utf-8")).decode("utf-8"),
            "profile-web-page-url": f"https://t.me/{USERNAME_BOT}",
            "subscription-userinfo": subscription_userinfo,
        }
    elif "Hiddify" in user_agent:
        parts = subscription_info.split(" - ")[0].split(": ")
        key_info = parts[1] if len(parts) > 1 else parts[0]
        encoded_project_name = f"{project_name}\n📄 Подписка: {key_info}"
        return {
            "profile-update-interval": "3",
            "profile-title": "base64:"
            + base64.b64encode(encoded_project_name.encode("utf-8")).decode("utf-8"),
            "subscription-userinfo": subscription_userinfo,
        }
    elif "v2raytun" in user_agent:
        encoded_project_name = f"{project_name}\n{subscription_info}"
        announce_str = "🔑 Выберите сервер ⬇️ | 💬 Поддержка ➡️"
        return {
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Disposition": "inline",
            "update-always": "true",
            "announce": "base64:" + base64.b64encode(announce_str.encode("utf-8")).decode("utf-8"),
            "announce-url": f"{SUPPORT_CHAT_URL}",
            "profile-title": "base64:" + base64.b64encode(encoded_project_name.encode("utf-8")).decode("utf-8"),
            "subscription-userinfo": subscription_userinfo,
        }
    else:
        # Default headers for other clients
        encoded_project_name = f"{project_name}\n{subscription_info}"
        return {
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Disposition": "inline",
            "profile-update-interval": "3",
            "profile-title": "base64:" + base64.b64encode(encoded_project_name.encode("utf-8")).decode("utf-8"),
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }


async def handle_subscription(request: web.Request) -> web.Response:
    email = request.match_info.get("email")
    tg_id = request.match_info.get("tg_id")

    if not email or not tg_id:
        return web.Response(text="❌ Неверные параметры запроса.", status=400)

    sessionmaker = request.app["sessionmaker"]

    async with sessionmaker() as session:
        try:
            key = await get_key_details(session, email)
            if not key:
                return web.Response(
                    text="❌ Клиент с таким email не найден.", status=404
                )

            if int(tg_id) != int(key["tg_id"]):
                return web.Response(
                    text="❌ Неверные данные. Получите свой ключ в боте.", status=403
                )

            expiry_time_ms = key["expiry_time"]
            server_id = key["server_id"]
            remnawave_link = key["remnawave_link"]

            time_left = format_time_left(expiry_time_ms)

            urls = await get_subscription_urls(
                server_id, email, session, include_remnawave_key=remnawave_link
            )
            if not urls:
                return web.Response(text="❌ Сервер не найден.", status=404)

            query_string = request.query_string
            combined_subscriptions, headers_list = await combine_unique_lines(
                urls, tg_id or email, query_string
            )
            if RANDOM_SUBSCRIPTIONS:
                random.shuffle(combined_subscriptions)

            cleaned_subscriptions = [
                clean_subscription_line(line) for line in combined_subscriptions
            ]

            # Join the cleaned subscription lines with proper newlines
            subscription_content = "\n".join(cleaned_subscriptions)
            
            # Log the raw subscription content for debugging
            logger.debug(f"Raw subscription content (first 200 chars): {subscription_content[:200]}")
            
            # Ensure proper UTF-8 encoding before base64
            try:
                base64_encoded = base64.b64encode(
                    subscription_content.encode("utf-8")
                ).decode("utf-8")
            except UnicodeEncodeError as e:
                logger.error(f"UTF-8 encoding error: {e}")
                # Fallback to replacing problematic characters
                base64_encoded = base64.b64encode(
                    subscription_content.encode("utf-8", errors="replace")
                ).decode("utf-8")
            
            subscription_info = f"📄 Подписка: {email} — {time_left}"
            logger.debug(f"Subscription info: {subscription_info}")

            user_agent = request.headers.get("User-Agent", "")
            subscription_userinfo = calculate_traffic(
                cleaned_subscriptions, expiry_time_ms, headers_list
            )
            headers = prepare_headers(
                user_agent, PROJECT_NAME, subscription_info, subscription_userinfo
            )

            # Create response with minimal parameters
            response = web.Response(
                text=base64_encoded,
                headers=headers
            )
            
            # Set content type and cache control headers
            response.content_type = 'text/plain; charset=utf-8'
            response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
            
            logger.debug(f"Response headers: {dict(response.headers)}")
            return response

        except Exception as e:
            logger.error(f"Ошибка в handle_subscription: {e}", exc_info=True)
            return web.Response(text=f"❌ Ошибка сервера: {e}", status=500)

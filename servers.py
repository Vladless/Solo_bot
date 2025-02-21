import asyncio
import re
from datetime import datetime, timedelta

from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from ping3 import ping

from bot import bot
from config import ADMIN_ID, DATABASE_URL, PING_TIME
from database import get_servers
from keyboards.admin.servers_kb import AdminServerEditorCallback
from logger import logger

last_ping_times = {}
last_notification_times = {}
PING_SEMAPHORE = asyncio.Semaphore(3)


async def ping_server(server_ip: str) -> bool:
    """Пингует сервер через ICMP или TCP 443, если ICMP недоступен или возникает ошибка."""
    async with PING_SEMAPHORE:
        try:
            response = ping(server_ip, timeout=3)
            if response is not None and response is not False:
                return True
            return await check_tcp_connection(server_ip, 443)
        except Exception:
            return await check_tcp_connection(server_ip, 443)


async def check_tcp_connection(host: str, port: int) -> bool:
    """Проверяет доступность сервера через TCP (порт 443)."""
    try:
        reader, writer = await asyncio.open_connection(host, port)
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


async def notify_admin(server_name: str):
    """Отправляет уведомление администраторам о недоступности сервера (не чаще чем раз в 3 минуты)."""
    current_time = datetime.now()
    last_notification_time = last_notification_times.get(server_name)

    if last_notification_time and current_time - last_notification_time < timedelta(minutes=3):
        return

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="Управление сервером",
            callback_data=AdminServerEditorCallback(action="servers_manage", data=server_name).pack(),
        )
    )

    for admin_id in ADMIN_ID:
        await bot.send_message(
            admin_id,
            (
                f"❌ <b>Сервер '{server_name}'</b> не отвечает более {PING_TIME * 3} секунд.\n\n"
                "Проверьте соединение к серверу или удалите его из списка, чтобы не выдавать подписки на неработающий сервер."
            ),
            reply_markup=builder.as_markup(),
        )

    last_notification_times[server_name] = current_time


async def check_servers():
    """
    Периодическая проверка серверов.
    Использует `asyncio.gather()` для ускорения.
    """
    while True:
        servers = await get_servers()
        current_time = datetime.now()

        tasks = []
        server_info_list = []

        for _, cluster_servers in servers.items():
            for server in cluster_servers:
                original_api_url = server["api_url"]
                server_name = server["server_name"]
                server_host = extract_host(original_api_url)

                server_info_list.append((server_name, server_host))
                tasks.append(ping_server(server_host))

        results = await asyncio.gather(*tasks)

        offline_servers = []

        for (server_name, _), is_online in zip(server_info_list, results, strict=False):
            if is_online:
                last_ping_times[server_name] = current_time
            else:
                last_ping_time = last_ping_times.get(server_name)
                if last_ping_time and current_time - last_ping_time > timedelta(seconds=PING_TIME * 3):
                    offline_servers.append(server_name)
                    await notify_admin(server_name)
                elif not last_ping_time:
                    last_ping_times[server_name] = current_time

        online_servers = [name for name, _ in server_info_list if name not in offline_servers]
        logger.info(f"Проверка серверов завершена. Доступно: {len(online_servers)}, Недоступно: {len(offline_servers)}")
        if offline_servers:
            logger.warning(f"🚨 Не отвечает {len(offline_servers)} серверов: {', '.join(offline_servers)}")

        await asyncio.sleep(PING_TIME)


def extract_host(api_url: str) -> str:
    """Извлекает хост из `api_url`."""
    match = re.match(r"(https?://)?([^:/]+)", api_url)
    return match.group(2) if match else api_url

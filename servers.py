import asyncio
import re
import ssl

from datetime import datetime, timedelta

from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from ping3 import ping

from bot import bot
from core.executor import run_io
from database import async_session_maker, get_servers
from handlers.admin.servers.keyboard import AdminServerCallback
from logger import logger
from settings.config import ADMIN_ID, PING_TIME


last_ping_times = {}
last_down_times = {}
notified_servers = set()
PING_SEMAPHORE = asyncio.Semaphore(3)


def _sync_ping(server_ip: str, timeout: float = 3):
    """Синхронный ping для вызова в пуле потоков."""
    return ping(server_ip, timeout=timeout)


async def ping_server(server_ip: str) -> bool:
    async with PING_SEMAPHORE:
        try:
            response = await run_io(_sync_ping, server_ip, 3)
            if response is not None and response is not False:
                return True
            return await check_tcp_connection(server_ip, 443)
        except Exception:
            return await check_tcp_connection(server_ip, 443)


async def check_tcp_connection(host: str, port: int) -> bool:
    """Проверяет доступность сервера через TCP с попыткой SSL-соединения."""
    try:
        ssl_context = ssl.create_default_context()
        _reader, writer = await asyncio.open_connection(host, port, ssl=ssl_context)
        writer.close()
        await writer.wait_closed()
        return True
    except ssl.SSLError as e:
        err_text = str(e).lower()
        if "certificate has expired" in err_text:
            logger.warning(f"[SSL Error] Сертификат сервера {host} просрочен: {e}")
            await notify_ssl_error(host, str(e))
            return False
        return True
    except Exception:
        return False


async def notify_ssl_error(server_host: str, error_text: str):
    message = (
        f"⚠️ <b>Ошибка SSL на сервере</b> <code>{server_host}</code>\n\n"
        f"<b>Описание:</b> {error_text}\n"
        "Проверьте корректность сертификата или конфигурации HTTPS."
    )
    for admin_id in ADMIN_ID:
        await bot.send_message(admin_id, message)


async def notify_admin(server_name: str, status: str, down_duration: timedelta = None):
    """Отправляет уведомление администратору."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="Управление сервером",
            callback_data=AdminServerCallback(action="manage", data=server_name).pack(),
        )
    )

    if status == "down":
        message = (
            f"❌ <b>Сервер '{server_name}'</b> не отвечает!\n\n"
            "Проверьте соединение или удалите его из списка, чтобы не выдавать подписки на неработающий сервер."
        )
    else:
        downtime = str(down_duration).split(".")[0]
        message = f"✅ <b>Сервер '{server_name}' снова в сети!</b>\n\n⏳ Время простоя: {downtime}."

    for admin_id in ADMIN_ID:
        logger.info(f"📨 Отправляем уведомление '{status}' администратору {admin_id} о сервере {server_name}")
        await bot.send_message(admin_id, message, reply_markup=builder.as_markup())


async def check_servers(sessionmaker=None):
    """
    Периодическая проверка серверов.
    Использует короткую сессию на итерацию, чтобы не держать транзакцию во время ping.
    """
    maker = sessionmaker or async_session_maker
    while True:
        async with maker() as session:
            servers = await get_servers(session=session)
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

        logger.info(f"Начинаем проверку {len(server_info_list)} серверов...")

        results = await asyncio.gather(*tasks, return_exceptions=True)

        offline_servers = set()
        restored_servers = set()
        online_servers = set()

        for (server_name, server_host), result in zip(server_info_list, results, strict=False):
            is_online = bool(result) if not isinstance(result, Exception) else False

            if is_online:
                last_ping_times[server_name] = current_time
                online_servers.add(server_name)

                if server_name in notified_servers:
                    down_time = last_down_times.pop(server_name, current_time)
                    down_duration = current_time - down_time
                    await notify_admin(server_name, "up", down_duration)

                    notified_servers.remove(server_name)
                    restored_servers.add(server_name)

            else:
                last_ping_time = last_ping_times.get(server_name)

                if last_ping_time is None:
                    last_ping_times[server_name] = current_time
                    last_down_times[server_name] = current_time

                if last_ping_time and (current_time - last_ping_time > timedelta(seconds=PING_TIME * 3)):
                    if server_name not in notified_servers:
                        logger.warning(
                            f"🚨 Уведомление: сервер {server_name} не отвечает более {PING_TIME * 3} секунд!"
                        )
                        await notify_admin(server_name, "down")
                        notified_servers.add(server_name)
                        last_down_times[server_name] = current_time
                    offline_servers.add(server_name)

        all_servers = {name for name, _ in server_info_list}
        true_offline_servers = all_servers - online_servers

        logger.info(f"✅ Доступно серверов: {len(online_servers)}, ❌ Недоступно: {len(true_offline_servers)}")

        if true_offline_servers:
            logger.warning(f"🚨 Не отвечает {len(true_offline_servers)} серверов: {', '.join(true_offline_servers)}")
        if restored_servers:
            logger.info(f"✅ Восстановились {len(restored_servers)} серверов: {', '.join(restored_servers)}")

        await asyncio.sleep(PING_TIME)


def extract_host(api_url: str) -> str:
    """Извлекает хост из `api_url`."""
    match = re.match(r"(https?://)?([^:/]+)", api_url)
    return match.group(2) if match else api_url

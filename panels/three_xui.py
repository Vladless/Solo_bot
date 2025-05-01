import time

from dataclasses import dataclass
from typing import Any

import httpx
import py3xui

from py3xui import AsyncApi

from config import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    LIMIT_IP,
    SUPERNODE,
    USE_XUI_TOKEN,
    XUI_TOKEN,
)
from logger import logger


@dataclass
class ClientConfig:
    """Конфигурация клиента для добавления/обновления."""

    client_id: str
    email: str
    tg_id: str
    limit_ip: int
    total_gb: int
    expiry_time: int
    enable: bool
    flow: str
    inbound_id: int
    sub_id: str


_xui_instance_cache: dict[str, tuple[AsyncApi, float]] = {}
SESSION_TTL = 1800


async def get_xui_instance(api_url: str) -> AsyncApi:
    key = f"{api_url}|{ADMIN_USERNAME}"
    current_time = time.time()

    xui_entry = _xui_instance_cache.get(key)
    if xui_entry:
        xui, last_login = xui_entry
        if current_time - last_login < SESSION_TTL:
            return xui
        else:
            logger.info("[XUI Cache] Сессия устарела (>30 минут), переподключение...")
            await xui.login()
            _xui_instance_cache[key] = (xui, current_time)
            return xui

    xui = AsyncApi(
        api_url,
        ADMIN_USERNAME,
        ADMIN_PASSWORD,
        token=XUI_TOKEN if USE_XUI_TOKEN else None,
        logger=logger,
    )
    await xui.login()
    _xui_instance_cache[key] = (xui, current_time)
    return xui


async def add_client(xui: py3xui.AsyncApi, config: ClientConfig) -> dict[str, Any]:
    try:
        client = py3xui.Client(
            id=config.client_id,
            email=config.email.lower(),
            limit_ip=config.limit_ip,
            total_gb=config.total_gb,
            expiry_time=config.expiry_time,
            enable=config.enable,
            tg_id=config.tg_id,
            sub_id=config.sub_id,
            flow=config.flow,
        )

        response = await xui.client.add(config.inbound_id, [client])
        logger.info(f"Клиент {config.email} успешно добавлен с ID {config.client_id}")
        return response if response else {"status": "failed"}

    except httpx.ConnectTimeout as e:
        logger.error(f"Ошибка при добавлении клиента {config.email}: {e}")
        return {"status": "failed", "error": "Timeout"}

    except Exception as e:
        error_message = str(e)
        if "Duplicate email" in error_message:
            logger.warning(f"Дублированный email: {config.email}. Пропуск. Сообщение: {error_message}")
            return {"status": "duplicate", "email": config.email}

        logger.error(f"Ошибка при добавлении клиента {config.email}: {error_message}")
        return {"status": "failed", "error": error_message}


async def extend_client_key(
    xui: py3xui.AsyncApi,
    inbound_id: int,
    email: str,
    new_expiry_time: int,
    client_id: str,
    total_gb: int,
    sub_id: str,
    tg_id: int,
) -> bool | None:
    try:
        client = await xui.client.get_by_email(email)
        if not client or not client.id:
            logger.warning(f"Клиент с email {email} не найден или не имеет ID.")
            return None

        logger.info(f"Обновление ключа клиента {email} с ID {client.id} до {new_expiry_time}")

        client.id = client_id
        client.expiry_time = new_expiry_time
        client.flow = "xtls-rprx-vision"
        client.sub_id = sub_id
        client.total_gb = total_gb
        client.enable = True
        client.limit_ip = LIMIT_IP
        client.inbound_id = inbound_id
        client.tg_id = tg_id

        await xui.client.update(client.id, client)
        await xui.client.reset_stats(inbound_id, email)
        logger.info(f"Ключ клиента {email} успешно продлён до {new_expiry_time}")
        return True

    except httpx.ConnectTimeout as e:
        logger.error(f"Ошибка при обновлении клиента {email}: {e}")
        return False

    except Exception as e:
        logger.error(f"Ошибка при обновлении клиента с email {email}: {e}")
        return False


async def delete_client(
    xui: py3xui.AsyncApi,
    inbound_id: int,
    email: str,
    client_id: str,
) -> bool:
    """
    Удаляет клиента с сервера 3x-ui.

    Args:
        xui: Экземпляр API клиента
        inbound_id: ID входящего соединения
        email: Email клиента
        client_id: ID клиента

    Returns:
        bool: True если удаление успешно, False в противном случае
    """
    try:
        if SUPERNODE:
            await xui.client.delete(inbound_id, client_id)
            logger.info(f"Клиент с ID {client_id} был удален успешно (SUPERNODE)")
            return True

        client = await xui.client.get_by_email(email)
        if not client:
            logger.warning(f"Клиент с email {email} и ID {client_id} не найден")
            return False

        client.id = client_id
        await xui.client.delete(inbound_id, client.id)
        logger.info(f"Клиент с ID {client_id} был удален успешно")
        return True

    except httpx.ConnectTimeout as e:
        logger.error(f"Ошибка при удалении клиента {email}: {e}")
        return False

    except Exception as e:
        logger.error(f"Ошибка при удалении клиента с ID {client_id}: {e}")
        return False


async def get_client_traffic(xui: py3xui.AsyncApi, client_id: str) -> dict[str, Any]:
    try:
        traffic_data = await xui.client.get_traffic_by_id(client_id)
        if not traffic_data:
            logger.warning(f"Трафик для клиента {client_id} не найден.")
            return {"status": "not_found", "client_id": client_id}

        logger.info(f"Трафик для клиента {client_id} успешно получен.")
        return {"status": "success", "client_id": client_id, "traffic": traffic_data}

    except httpx.ConnectTimeout as e:
        logger.error(f"Ошибка при получении трафика клиента {client_id}: {e}")
        return {"status": "error", "error": "Timeout"}

    except Exception as e:
        logger.error(f"Ошибка при получении трафика клиента {client_id}: {e}")
        return {"status": "error", "error": str(e)}


async def toggle_client(xui: py3xui.AsyncApi, inbound_id: int, email: str, client_id: str, enable: bool = True) -> bool:
    try:
        client = await xui.client.get_by_email(email)
        if not client:
            logger.warning(f"Клиент с email {email} и ID {client_id} не найден.")
            return False

        client.sub_id = email
        client.enable = enable
        client.id = client_id
        client.flow = "xtls-rprx-vision"
        client.limit_ip = LIMIT_IP
        client.inbound_id = inbound_id

        await xui.client.update(client.id, client)
        status = "включен" if enable else "отключен"
        logger.info(f"Клиент с email {email} и ID {client_id} успешно {status}.")
        return True

    except httpx.ConnectTimeout as e:
        status = "включении" if enable else "отключении"
        logger.error(f"Ошибка при {status} клиента с email {email} и ID {client_id}: {e}")
        return False

    except Exception as e:
        status = "включении" if enable else "отключении"
        logger.error(f"Ошибка при {status} клиента с email {email} и ID {client_id}: {e}")
        return False

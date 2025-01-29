from dataclasses import dataclass
from typing import Any

import py3xui

from config import LIMIT_IP, SUPERNODE
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


async def add_client(xui: py3xui.API, config: ClientConfig) -> dict[str, Any]:
    """
    Добавляет клиента на сервер через 3x-ui.

    Args:
        xui: Экземпляр API клиента
        config: Конфигурация клиента

    Returns:
        Dict[str, Any]: Результат операции в формате
            {'status': 'success'|'failed'|'duplicate', 'error': str, 'email': str}
    """
    try:
        await xui.login()

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

    except Exception as e:
        error_message = str(e)
        if "Duplicate email" in error_message:
            logger.warning(f"Дублированный email: {config.email}. Пропуск. Сообщение: {error_message}")
            return {"status": "duplicate", "email": config.email}

        logger.error(f"Ошибка при добавлении клиента {config.email}: {error_message}")
        return {"status": "failed", "error": error_message}


async def extend_client_key(
    xui: py3xui.API, inbound_id: int, email: str, new_expiry_time: int, client_id: str, total_gb: int, sub_id: str
) -> bool | None:
    """
    Обновляет срок действия ключа клиента.

    Args:
        xui: Экземпляр API клиента
        inbound_id: ID входящего соединения
        email: Email клиента
        new_expiry_time: Новое время истечения
        client_id: ID клиента
        total_gb: Общий объем трафика
        sub_id: ID подписки

    Returns:
        Optional[bool]: True если успешно, False если ошибка, None если клиент не найден
    """
    try:
        await xui.login()
        client = await xui.client.get_by_email(email)

        if not client:
            logger.warning(f"Клиент с email {email} не найден")
            return None

        if not client.id:
            logger.warning(f"Ошибка: клиент {email} не имеет действительного ID")
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

        await xui.client.update(client.id, client)
        await xui.client.reset_stats(inbound_id, email)
        logger.info(f"Ключ клиента {email} успешно продлён до {new_expiry_time}")
        return True

    except Exception as e:
        logger.error(f"Ошибка при обновлении клиента с email {email}: {e}")
        return False


async def delete_client(
    xui: py3xui.API,
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
        await xui.login()

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

    except Exception as e:
        logger.error(f"Ошибка при удалении клиента с ID {client_id}: {e}")
        return False

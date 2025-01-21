import py3xui

from config import LIMIT_IP, SUPERNODE
from logger import logger


async def add_client(
    xui,
    client_id: str,
    email: str,
    tg_id: str,
    limit_ip: int,
    total_gb: int,
    expiry_time: int,
    enable: bool,
    flow: str,
    inbound_id: int,
    sub_id,
):
    """
    Adds a client to the server via 3x-ui.
    """
    try:
        await xui.login()

        client = py3xui.Client(
            id=client_id,
            email=email.lower(),
            limit_ip=limit_ip,
            total_gb=total_gb,
            expiry_time=expiry_time,
            enable=enable,
            tg_id=tg_id,
            sub_id=sub_id,
            flow=flow,
        )

        response = await xui.client.add(inbound_id, [client])

        logger.info(f"Клиент {email} успешно добавлен с ID {client_id}.")

        return response if response else {"status": "failed"}

    except Exception as e:
        error_message = str(e)

        if "Duplicate email" in error_message:
            logger.warning(f"Дублированный email: {email}. Пропуск. Сообщение: {error_message}")
            return {"status": "duplicate", "email": email}

        logger.error(f"Ошибка при добавлении клиента {email}: {error_message}")
        return {"status": "failed", "error": error_message}


async def extend_client_key(
    xui, inbound_id, email: str, new_expiry_time: int, client_id: str, total_gb: int, sub_id=str
):
    """
    Функция для обновления срока действия ключа клиента по email.
    """
    await xui.login()
    try:
        client = await xui.client.get_by_email(email)

        if not client:
            logger.warning(f"Клиент с email {email} не найден.")
            return

        if not client.id:
            logger.warning(f"Ошибка: клиент {email} не имеет действительного ID.")
            return

        logger.info(f"Обновление ключа клиента {client.email} с ID {client.id} до нового времени: {new_expiry_time}")

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
        logger.info(f"Ключ клиента {client.email} успешно продлён до {new_expiry_time}.")

    except Exception as e:
        logger.error(f"Ошибка при обновлении клиента с email {email}: {e}")


async def delete_client(
    xui,
    inbound_id: int,
    email: str,
    client_id: str,
) -> bool:
    """
    Функция для удаления клиента с сервера 3x-ui.
    Возвращает True при успешном удалении, иначе False.
    """
    await xui.login()
    try:
        if SUPERNODE:
            await xui.client.delete(inbound_id, client_id)
            logger.info(f"Клиент с ID {client_id} был удален успешно (SUPERNODE).")
            return True

        client = await xui.client.get_by_email(email)

        if not client:
            logger.warning(f"Клиент с email {email} и ID {client_id} не найден.")
            return False

        client.id = client_id

        await xui.client.delete(inbound_id, client.id)
        logger.info(f"Клиент с ID {client_id} был удален успешно.")
        return True

    except Exception as e:
        logger.error(f"Ошибка при удалении клиента с ID {client_id}: {e}")
        return False

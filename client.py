import py3xui

from config import TOTAL_GB
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
            total_gb=TOTAL_GB,
            expiry_time=expiry_time,
            enable=enable,
            tg_id=tg_id,
            sub_id=email,
            flow=flow,
        )

        response = await xui.client.add(1, [client])

        logger.info(f"Клиент {email} успешно добавлен с ID {client_id}.")

        return response if response else {"status": "failed"}

    except Exception as e:
        logger.error(f"Ошибка при добавлении клиента {email}: {e}")
        return {"status": "failed", "error": str(e)}


async def extend_client_key(
    xui, email: str, new_expiry_time: int, client_id: str, total_gb: int
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

        logger.info(
            f"Обновление ключа клиента {client.email} с ID {client.id} до нового времени: {new_expiry_time}"
        )

        client.id = client_id
        client.expiry_time = new_expiry_time
        client.flow = "xtls-rprx-vision"
        client.sub_id = email
        client.total_gb = total_gb
        client.enable = True
        client.limit_ip = 1

        await xui.client.update(client.id, client)
        logger.info(
            f"Ключ клиента {client.email} успешно продлён до {new_expiry_time}."
        )

    except Exception as e:
        logger.error(f"Ошибка при обновлении клиента с email {email}: {e}")


async def delete_client(
    xui,
    email: str,
    client_id: str,
) -> bool:
    """
    Функция для удаления клиента с сервера 3x-ui.
    Возвращает True при успешном удалении, иначе False.
    """
    await xui.login()
    try:
        client = await xui.client.get_by_email(email)

        if not client:
            logger.warning(f"Клиент с email {email} и ID {client_id} не найден.")
            return False

        client.id = client_id
        inbound_id = 1

        await xui.client.delete(inbound_id, client.id)
        logger.info(f"Клиент с ID {client_id} был удален успешно.")
        return True

    except Exception as e:
        logger.error(f"Ошибка при удалении клиента с ID {client_id}: {e}")
        return False

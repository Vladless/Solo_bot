import json
from datetime import datetime
from typing import Any

import asyncpg
import pytz

from config import CASHBACK, CHECK_REFERRAL_REWARD_ISSUED, DATABASE_URL, REFERRAL_BONUS_PERCENTAGES
from logger import logger


async def create_temporary_data(session, tg_id: int, state: str, data: dict):
    """Сохраняет временные данные пользователя."""
    await session.execute(
        """
        INSERT INTO temporary_data (tg_id, state, data, updated_at)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (tg_id)
        DO UPDATE SET state = $2, data = $3, updated_at = $4
        """,
        tg_id,
        state,
        json.dumps(data),
        datetime.utcnow(),
    )


async def get_temporary_data(session, tg_id: int) -> dict | None:
    """Извлекает временные данные пользователя."""
    result = await session.fetchrow("SELECT state, data FROM temporary_data WHERE tg_id = $1", tg_id)
    if result:
        return {"state": result["state"], "data": json.loads(result["data"])}
    return None


async def clear_temporary_data(session, tg_id: int):
    # TODO rename delete_temporary_data
    await session.execute("DELETE FROM temporary_data WHERE tg_id = $1", tg_id)


async def create_blocked_user(tg_id: int, conn: asyncpg.Connection):
    await conn.execute(
        "INSERT INTO blocked_users (tg_id) VALUES ($1) ON CONFLICT (tg_id) DO NOTHING",
        tg_id,
    )


async def delete_blocked_user(tg_id: int | list[int], conn: asyncpg.Connection):
    """
    Удаляет пользователя или список пользователей из списка заблокированных.

    :param tg_id: ID пользователя Telegram или список ID
    :param conn: Подключение к базе данных
    """
    if isinstance(tg_id, list):
        await conn.execute("DELETE FROM blocked_users WHERE tg_id = ANY($1)", tg_id)
    else:
        await conn.execute("DELETE FROM blocked_users WHERE tg_id = $1", tg_id)


async def init_db(file_path: str = "assets/schema.sql"):
    with open(file_path) as file:
        sql_content = file.read()

    statements = [stmt.strip() for stmt in sql_content.split(";") if stmt.strip()]
    conn = await asyncpg.connect(DATABASE_URL)

    try:
        for statement in statements:
            await conn.execute(statement)
    except Exception as e:
        logger.error(f"Error while executing SQL statement: {e}")
    finally:
        logger.info("Tables created successfully")
        await conn.close()


async def check_unique_server_name(server_name: str, session: Any, cluster_name: str | None = None) -> bool:
    """
    Проверяет уникальность имени сервера.

    :param server_name: Имя сервера.
    :param session: Сессия базы данных.
    :param cluster_name: Имя кластера (опционально).
    :return: True, если имя сервера уникально, False, если уже существует.
    """
    if cluster_name:
        result = await session.fetchrow(
            "SELECT 1 FROM servers WHERE server_name = $1 AND cluster_name = $2 LIMIT 1", server_name, cluster_name
        )
    else:
        result = await session.fetchrow("SELECT 1 FROM servers WHERE server_name = $1 LIMIT 1", server_name)

    return result is None


async def check_server_name_by_cluster(server_name: str, session: Any) -> dict | None:
    """
    Проверяет принадлежность сервера к кластеру.

    Args:
        server_name (str): Имя сервера для проверки
        session (Any): Сессия базы данных

    Returns:
        dict | None: Словарь с информацией о кластере или None, если сервер не найден
            - cluster_name (str): Название кластера
    """
    try:
        cluster_info = await session.fetchrow(
            """
            SELECT cluster_name 
            FROM servers 
            WHERE server_name = $1
            """,
            server_name,
        )
        if cluster_info:
            logger.info(f"Найден кластер для сервера {server_name}")
            return dict(cluster_info)
        logger.info(f"Кластер для сервера {server_name} не найден")
        return None
    except Exception as e:
        logger.error(f"Ошибка при поиске кластера для сервера {server_name}: {e}")
        raise


async def create_coupon(coupon_code: str, amount: float, usage_limit: int, session: Any):
    """
    Создает новый купон в базе данных.

    Args:
        coupon_code (str): Уникальный код купона.
        amount (float): Сумма, которую дает купон.
        usage_limit (int): Максимальное количество использований купона.
        session (Any): Сессия базы данных для выполнения запроса.

    Raises:
        Exception: В случае ошибки при создании купона.

    Example:
        await create_coupon('SALE50', 50.0, 5, session)
    """
    try:
        await session.execute(
            """
            INSERT INTO coupons (code, amount, usage_limit, usage_count, is_used)
            VALUES ($1, $2, $3, 0, FALSE)
        """,
            coupon_code,
            amount,
            usage_limit,
        )
        logger.info(f"Успешно создан купон с кодом {coupon_code} на сумму {amount}")
    except Exception as e:
        logger.error(f"Ошибка при создании купона {coupon_code}: {e}")
        raise


async def get_coupon_by_code(coupon_code: str, session: Any) -> dict | None:
    """
    Получает информацию о купоне по его коду.

    Args:
        coupon_code (str): Код купона для поиска
        session (Any): Сессия базы данных

    Returns:
        dict | None: Словарь с информацией о купоне или None, если купон не найден
            - id (int): ID купона
            - usage_limit (int): Лимит использований
            - usage_count (int): Текущее количество использований
            - is_used (bool): Флаг использования
            - amount (float): Сумма купона

    Raises:
        Exception: В случае ошибки при выполнении запроса
    """
    try:
        result = await session.fetchrow(
            """
            SELECT id, usage_limit, usage_count, is_used, amount
            FROM coupons
            WHERE code = $1 AND (usage_count < usage_limit OR usage_limit = 0) AND is_used = FALSE
            """,
            coupon_code,
        )
        return dict(result) if result else None
    except Exception as e:
        logger.error(f"Ошибка при получении купона {coupon_code}: {e}")
        raise


async def get_all_coupons(session: Any, page: int = 1, per_page: int = 10):
    """
    Получает список купонов из базы данных с пагинацией.

    Args:
        session (Any): Сессия базы данных для выполнения запроса
        page (int): Номер страницы (по умолчанию 1)
        per_page (int): Количество купонов на странице (по умолчанию 10)

    Returns:
        dict: Словарь с информацией о купонах и пагинации:
            - coupons (list): Список словарей с информацией о купонах
            - total (int): Общее количество купонов
            - pages (int): Общее количество страниц
            - current_page (int): Текущая страница

    Raises:
        Exception: В случае ошибки при получении данных из базы
    """
    try:
        offset = (page - 1) * per_page
        coupons = await session.fetch(
            """
            SELECT code, amount, usage_limit, usage_count
            FROM coupons
            ORDER BY id
            LIMIT $1 OFFSET $2
            """,
            per_page,
            offset,
        )

        total_count = await session.fetchval("SELECT COUNT(*) FROM coupons")
        total_pages = -(-total_count // per_page)  # Округление вверх

        logger.info(f"Успешно получено {len(coupons)} купонов из базы данных (страница {page})")

        return {"coupons": coupons, "total": total_count, "pages": total_pages, "current_page": page}
    except Exception as e:
        logger.error(f"Критическая ошибка при получении списка купонов: {e}")
        logger.exception("Трассировка стека ошибки получения купонов")
        return {"coupons": [], "total": 0, "pages": 0, "current_page": page}


async def delete_coupon(coupon_code: str, session: Any):
    """
    Удаляет купон из базы данных по его коду.

    Args:
        coupon_code (str): Код купона для удаления
        session (Any): Сессия базы данных для выполнения запроса

    Returns:
        bool: True, если купон успешно удален, False если купон не найден или произошла ошибка

    Raises:
        Exception: В случае ошибки при выполнении запроса к базе данных

    Example:
        result = await delete_coupon('SALE50', session)
    """
    try:
        coupon_record = await session.fetchrow(
            """
            SELECT id FROM coupons WHERE code = $1
        """,
            coupon_code,
        )

        if not coupon_record:
            logger.info(f"Купон {coupon_code} не найден в базе данных")
            return False

        await session.execute(
            """
            DELETE FROM coupons WHERE code = $1
        """,
            coupon_code,
        )

        logger.info(f"Купон {coupon_code} успешно удален из базы данных")
        return True

    except Exception as e:
        logger.error(f"Произошла ошибка при удалении купона {coupon_code}: {e}")
        return False


async def update_trial(tg_id: int, status: int, session: Any):
    """
    Устанавливает статус триального периода для пользователя.

    Args:
        tg_id (int): Telegram ID пользователя
        status (int): Статус триального периода (0 - доступен, 1 - использован)
        session (Any): Сессия базы данных

    Returns:
        bool: True, если статус успешно установлен, False в случае ошибки
    """
    try:
        await session.execute(
            """
            INSERT INTO connections (tg_id, trial) 
            VALUES ($1, $2) 
            ON CONFLICT (tg_id) 
            DO UPDATE SET trial = $2
            """,
            tg_id,
            status,
        )
        status_text = "восстановлен" if status == 0 else "использован"
        logger.info(f"Триальный период успешно {status_text} для пользователя {tg_id}")
        return True
    except Exception as e:
        logger.error(f"Ошибка при установке статуса триального периода для пользователя {tg_id}: {e}")
        return False


async def add_connection(tg_id: int, balance: float = 0.0, trial: int = 0, session: Any = None):
    """
    Добавляет новое подключение для пользователя в базу данных.

    Args:
        tg_id (int): Telegram ID пользователя
        balance (float, optional): Начальный баланс пользователя. По умолчанию 0.0.
        trial (int, optional): Статус триального периода. По умолчанию 0.
        session (Any, optional): Сессия базы данных.

    Raises:
        Exception: Если возникает ошибка при добавлении подключения в базу данных.
    """
    try:
        await session.execute(
            """
            INSERT INTO connections (tg_id, balance, trial)
            VALUES ($1, $2, $3)
            """,
            tg_id,
            balance,
            trial,
        )
        logger.info(
            f"Успешно добавлено новое подключение для пользователя {tg_id} с балансом {balance} и статусом триала {trial}"
        )
    except Exception as e:
        logger.error(f"Не удалось добавить подключение для пользователя {tg_id}. Причина: {e}")
        raise


async def check_connection_exists(tg_id: int):
    """
    Проверяет существование подключения для указанного пользователя в базе данных.

    Args:
        tg_id (int): Telegram ID пользователя для проверки.

    Returns:
        bool: True, если подключение существует, иначе False.

    Raises:
        Exception: В случае ошибки при подключении к базе данных.
    """
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        exists = await conn.fetchval(
            """
            SELECT EXISTS(SELECT 1 FROM connections WHERE tg_id = $1)
            """,
            tg_id,
        )
        logger.info(
            f"Проверка существования подключения для пользователя {tg_id}: {'найдено' if exists else 'не найдено'}"
        )
        return exists
    except Exception as e:
        logger.error(f"Ошибка при проверке подключения для пользователя {tg_id}: {e}")
        raise
    finally:
        if conn:
            await conn.close()


async def store_key(
    tg_id: int,
    client_id: str,
    email: str,
    expiry_time: int,
    key: str,
    server_id: str,
    session: Any,
):
    """
    Сохраняет информацию о ключе в базу данных.

    Args:
        tg_id (int): Telegram ID пользователя
        client_id (str): Уникальный идентификатор клиента
        email (str): Электронная почта или имя устройства
        expiry_time (int): Время истечения ключа в миллисекундах
        key (str): Ключ доступа
        server_id (str): Идентификатор сервера

    Raises:
        Exception: Если возникает ошибка при сохранении ключа в базу данных
    """
    try:
        await session.execute(
            """
            INSERT INTO keys (tg_id, client_id, email, created_at, expiry_time, key, server_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            tg_id,
            client_id,
            email,
            int(datetime.utcnow().timestamp() * 1000),
            expiry_time,
            key,
            server_id,
        )
        logger.info(f"Ключ успешно сохранен для пользователя {tg_id} на сервере {server_id}")
    except Exception as e:
        logger.error(f"Ошибка при сохранении ключа для пользователя {tg_id}: {e}")
        raise


async def get_keys(tg_id: int, session: Any):
    """
    Получает список ключей для указанного пользователя.

    Args:
        tg_id (int): Telegram ID пользователя

    Returns:
        list: Список записей ключей с информацией о клиенте, электронной почте, времени создания и ключе

    Raises:
        Exception: В случае ошибки при подключении к базе данных или выполнении запроса
    """
    try:
        records = await session.fetch(
            """
            SELECT *
            FROM keys
            WHERE tg_id = $1
            """,
            tg_id,
        )
        logger.info(f"Успешно получено {len(records)} ключей для пользователя {tg_id}")
        return records
    except Exception as e:
        logger.error(f"Ошибка при получении ключей для пользователя {tg_id}: {e}")
        raise


async def get_keys_by_server(tg_id: int | None, server_id: str, session: Any):
    """
    Получает список ключей на определенном сервере. Если tg_id=None, возвращает все ключи на сервере.

    Args:
        tg_id (int | None): Telegram ID пользователя или None для всех пользователей
        server_id (str): Идентификатор сервера

    Returns:
        list: Список записей ключей с информацией о клиенте, электронной почте, времени создания и ключе

    Raises:
        Exception: В случае ошибки при подключении к базе данных или выполнении запроса
    """
    try:
        if tg_id is not None:
            records = await session.fetch(
                """
                SELECT *
                FROM keys
                WHERE tg_id = $1 AND server_id = $2
                """,
                tg_id,
                server_id,
            )
            logger.info(f"Успешно получено {len(records)} ключей для пользователя {tg_id} на сервере {server_id}")
        else:
            records = await session.fetch(
                """
                SELECT *
                FROM keys
                WHERE server_id = $1
                """,
                server_id,
            )
            logger.info(f"Успешно получено {len(records)} ключей на сервере {server_id}")

        return records
    except Exception as e:
        error_msg = f"Ошибка при получении ключей на сервере {server_id}"
        if tg_id is not None:
            error_msg += f" для пользователя {tg_id}"
        logger.error(f"{error_msg}: {e}")
        raise


async def get_key_by_server(tg_id: int, client_id: str, session: Any):
    query = """
        SELECT 
            tg_id, 
            client_id, 
            email, 
            created_at, 
            expiry_time, 
            key, 
            server_id, 
            notified, 
            notified_24h
        FROM keys
        WHERE tg_id = $1 AND client_id = $2
    """
    record = await session.fetchrow(query, tg_id, client_id)
    return record


async def get_balance(tg_id: int) -> float:
    """
    Получает баланс пользователя из базы данных.

    Args:
        tg_id (int): Telegram ID пользователя

    Returns:
        float: Баланс пользователя, 0.0 если баланс не найден

    Raises:
        Exception: В случае ошибки при подключении к базе данных или выполнении запроса
    """
    conn = None
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        balance = await conn.fetchval("SELECT balance FROM connections WHERE tg_id = $1", tg_id)
        return round(balance, 1) if balance is not None else 0.0
    except Exception as e:
        logger.error(f"Ошибка при получении баланса для пользователя {tg_id}: {e}")
        return 0.0
    finally:
        if conn:
            await conn.close()


async def update_balance(
    tg_id: int,
    amount: float,
    session: Any = None,
    is_admin: bool = False,
    skip_referral: bool = False,  # <- флаг "пропустить реферальное начисление"
    skip_cashback: bool = False,  # <- флаг "пропустить кэшбэк"
):
    """
    Обновляет баланс пользователя в базе данных.
    - Кэшбек применяется только для положительных сумм, если пополнение НЕ через админку и не пропущен явно.
    - Реферальный бонус тоже не срабатывает, если явно попросили пропустить (например, при начислении за купон).
    """
    conn = None
    try:
        if session is None:
            conn = await asyncpg.connect(DATABASE_URL)
            session = conn

        # Если пополнение не от админа и не сказали пропустить кэшбэк
        if CASHBACK > 0 and amount > 0 and not is_admin and not skip_cashback:
            extra = amount * (CASHBACK / 100.0)
        else:
            extra = 0

        total_amount = int(amount + extra)

        current_balance = await session.fetchval("SELECT balance FROM connections WHERE tg_id = $1", tg_id) or 0

        new_balance = current_balance + total_amount

        await session.execute(
            """
            UPDATE connections
            SET balance = $1
            WHERE tg_id = $2
            """,
            new_balance,
            tg_id,
        )
        logger.info(
            f"Баланс пользователя {tg_id} обновлен. Было: {int(current_balance)}, пополнение: {amount} "
            f"({'+ кешбэк' if extra > 0 else 'без кешбэка'}), стало: {new_balance}"
        )

        # Если не админ и не пропустили реферальное начисление — обрабатываем реферальную цепочку
        if not is_admin and not skip_referral:
            await handle_referral_on_balance_update(tg_id, int(amount))

    except Exception as e:
        logger.error(f"Ошибка при обновлении баланса для пользователя {tg_id}: {e}")
        raise
    finally:
        if conn is not None:
            await conn.close()


async def get_trial(tg_id: int, session: Any) -> int:
    """
    Получает статус триала для пользователя из базы данных.

    Args:
        tg_id (int): Telegram ID пользователя
        session (Any): Сессия базы данных

    Returns:
        int: Статус триала (0 - не использован, 1 - использован)
    """
    try:
        trial = await session.fetchval("SELECT trial FROM connections WHERE tg_id = $1", tg_id)
        logger.info(f"Получен статус триала для пользователя {tg_id}: {trial}")
        return trial if trial is not None else 0
    except Exception as e:
        logger.error(f"Ошибка при получении статуса триала для пользователя {tg_id}: {e}")
        return 0


async def get_key_count(tg_id: int) -> int:
    """
    Получает количество ключей для указанного пользователя.

    Args:
        tg_id (int): Telegram ID пользователя

    Returns:
        int: Количество ключей пользователя, 0 если ключей нет

    Raises:
        Exception: В случае ошибки при подключении к базе данных
    """
    conn = None
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        count = await conn.fetchval("SELECT COUNT(*) FROM keys WHERE tg_id = $1", tg_id)
        logger.info(f"Получено количество ключей для пользователя {tg_id}: {count}")
        return count if count is not None else 0
    except Exception as e:
        logger.error(f"Ошибка при получении количества ключей для пользователя {tg_id}: {e}")
        return 0
    finally:
        if conn:
            await conn.close()


async def add_referral(referred_tg_id: int, referrer_tg_id: int, session: Any):
    try:
        if referred_tg_id == referrer_tg_id:
            logger.warning(f"Пользователь {referred_tg_id} попытался использовать свою собственную реферальную ссылку.")
            return

        await session.execute(
            """
            INSERT INTO referrals (referred_tg_id, referrer_tg_id)
            VALUES ($1, $2)
            """,
            referred_tg_id,
            referrer_tg_id,
        )
        logger.info(f"Добавлена реферальная связь: приглашенный {referred_tg_id}, пригласивший {referrer_tg_id}")
    except Exception as e:
        logger.error(f"Ошибка при добавлении реферала: {e}")
        raise


async def handle_referral_on_balance_update(tg_id: int, amount: float):
    """
    Обработка многоуровневой реферальной системы при обновлении баланса пользователя.

    Метод анализирует цепочку рефералов для указанного пользователя и начисляет
    бонусы реферерам на разных уровнях согласно настроенным процентам.

    Args:
        tg_id (int): Идентификатор Telegram пользователя, пополнившего баланс
        amount (float): Сумма пополнения баланса
    """
    conn = None
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        logger.info(f"Начало обработки реферальной системы для пользователя {tg_id}")

        MAX_REFERRAL_LEVELS = len(REFERRAL_BONUS_PERCENTAGES.keys())
        if MAX_REFERRAL_LEVELS == 0:
            logger.warning("Реферальные бонусы отключены.")
            return

        visited_tg_ids = set()
        current_tg_id = tg_id
        referral_chain = []

        for level in range(1, MAX_REFERRAL_LEVELS + 1):
            if current_tg_id in visited_tg_ids:
                logger.warning(f"Обнаружен цикл в реферальной цепочке для пользователя {current_tg_id}. Прекращение.")
                break

            visited_tg_ids.add(current_tg_id)

            referral = await conn.fetchrow(
                """
                SELECT referrer_tg_id, reward_issued
                FROM referrals 
                WHERE referred_tg_id = $1
                """,
                current_tg_id,
            )

            if not referral:
                logger.info(f"Цепочка рефералов завершена на уровне {level}.")
                break

            referrer_tg_id = referral["referrer_tg_id"]

            if referrer_tg_id in visited_tg_ids:
                logger.warning(f"Реферер {referrer_tg_id} уже обработан. Пропуск.")
                break

            if CHECK_REFERRAL_REWARD_ISSUED and referral["reward_issued"]:
                logger.info(f"Реферальный бонус уже выдан для пользователя {current_tg_id}. Прекращение начисления.")
                break

            referral_chain.append({"tg_id": referrer_tg_id, "level": level})
            current_tg_id = referrer_tg_id

        for referral in referral_chain:
            referrer_tg_id = referral["tg_id"]
            level = referral["level"]

            bonus = REFERRAL_BONUS_PERCENTAGES.get(level, 0)
            if bonus <= 0:
                logger.warning(f"Процент бонуса для уровня {level} равен 0. Пропуск.")
                continue

            if isinstance(bonus, (int, float)):
                bonus = round(bonus, 2)

            if bonus > 0:
                logger.info(f"Начисление бонуса {bonus} рублей рефереру {referrer_tg_id} на уровне {level}.")
                await update_balance(referrer_tg_id, bonus, skip_referral=True, skip_cashback=True)

                if CHECK_REFERRAL_REWARD_ISSUED:
                    await conn.execute(
                        """
                        UPDATE referrals
                        SET reward_issued = TRUE
                        WHERE referred_tg_id = $1
                        """,
                        tg_id,
                    )

    except Exception as e:
        logger.error(f"Ошибка при обработке многоуровневой реферальной системы для {tg_id}: {e}")
    finally:
        if conn:
            await conn.close()


async def get_referral_stats(referrer_tg_id: int):
    conn = None
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        logger.info(
            f"Установлено подключение к базе данных для получения статистики рефералов пользователя {referrer_tg_id}"
        )

        total_referrals = await conn.fetchval(
            """
            SELECT COUNT(*) 
            FROM referrals 
            WHERE referrer_tg_id = $1
            """,
            referrer_tg_id,
        )
        logger.debug(f"Получено общее количество рефералов: {total_referrals}")

        active_referrals = await conn.fetchval(
            """
            SELECT COUNT(*) 
            FROM referrals 
            WHERE referrer_tg_id = $1 AND reward_issued = TRUE
            """,
            referrer_tg_id,
        )
        logger.debug(f"Получено количество активных рефералов: {active_referrals}")

        MAX_REFERRAL_LEVELS = len(REFERRAL_BONUS_PERCENTAGES.keys())

        referrals_by_level_records = await conn.fetch(
            f"""
            WITH RECURSIVE referral_levels AS (
                SELECT referred_tg_id, referrer_tg_id, 1 AS level
                FROM referrals 
                WHERE referrer_tg_id = $1
                
                UNION
                
                SELECT r.referred_tg_id, r.referrer_tg_id, rl.level + 1
                FROM referrals r
                JOIN referral_levels rl ON r.referrer_tg_id = rl.referred_tg_id
                WHERE rl.level < {MAX_REFERRAL_LEVELS}
            )
            SELECT level, 
                   COUNT(*) AS level_count, 
                   COUNT(CASE WHEN reward_issued = TRUE THEN 1 END) AS active_level_count
            FROM referral_levels rl
            JOIN referrals r ON rl.referred_tg_id = r.referred_tg_id
            GROUP BY level
            ORDER BY level
            """,
            referrer_tg_id,
        )

        referrals_by_level = {
            record["level"]: {
                "total": record["level_count"],
                "active": record["active_level_count"],
            }
            for record in referrals_by_level_records
        }
        logger.debug(f"Получена статистика рефералов по уровням: {referrals_by_level}")

        if CHECK_REFERRAL_REWARD_ISSUED:
            bonus_cte = f"""
            WITH RECURSIVE
            referral_levels AS (
                SELECT 
                    referred_tg_id, 
                    referrer_tg_id, 
                    1 AS level
                FROM referrals 
                WHERE referrer_tg_id = $1 AND reward_issued = TRUE
                
                UNION
                
                SELECT 
                    r.referred_tg_id, 
                    r.referrer_tg_id, 
                    rl.level + 1
                FROM referrals r
                JOIN referral_levels rl ON r.referrer_tg_id = rl.referred_tg_id
                WHERE rl.level < {MAX_REFERRAL_LEVELS} AND r.reward_issued = TRUE
            ),
            earliest_payments AS (
                SELECT DISTINCT ON (tg_id) tg_id, amount, created_at
                FROM payments
                WHERE status = 'success'
                ORDER BY tg_id, created_at
            )
            """
            total_referral_bonus_query = (
                bonus_cte
                + f"""
                SELECT 
                    COALESCE(SUM(
                        CASE
                            {" ".join([f"WHEN rl.level = {level} THEN {REFERRAL_BONUS_PERCENTAGES[level]} * ep.amount" if isinstance(REFERRAL_BONUS_PERCENTAGES[level], float) else f"WHEN rl.level = {level} THEN {REFERRAL_BONUS_PERCENTAGES[level]}" for level in REFERRAL_BONUS_PERCENTAGES])}
                            ELSE 0 
                        END
                    ), 0) AS total_bonus
                FROM referral_levels rl
                JOIN earliest_payments ep ON rl.referred_tg_id = ep.tg_id
                WHERE rl.level <= {MAX_REFERRAL_LEVELS}
                """
            )
        else:
            bonus_cte = f"""
            WITH RECURSIVE
            referral_levels AS (
                SELECT 
                    referred_tg_id, 
                    referrer_tg_id, 
                    1 AS level
                FROM referrals 
                WHERE referrer_tg_id = $1
                
                UNION
                
                SELECT 
                    r.referred_tg_id, 
                    r.referrer_tg_id, 
                    rl.level + 1
                FROM referrals r
                JOIN referral_levels rl ON r.referrer_tg_id = rl.referred_tg_id
                WHERE rl.level < {MAX_REFERRAL_LEVELS}
            )
            """

            total_referral_bonus_query = (
                bonus_cte
                + f"""
                SELECT 
                    COALESCE(SUM(
                        CASE
                            {" ".join([f"WHEN rl.level = {level} THEN {REFERRAL_BONUS_PERCENTAGES[level]} * p.amount" if isinstance(REFERRAL_BONUS_PERCENTAGES[level], float) else f"WHEN rl.level = {level} THEN {REFERRAL_BONUS_PERCENTAGES[level]}" for level in REFERRAL_BONUS_PERCENTAGES])}
                            ELSE 0 
                        END
                    ), 0) AS total_bonus
                FROM referral_levels rl
                JOIN payments p ON rl.referred_tg_id = p.tg_id
                WHERE p.status = 'success' AND rl.level <= {MAX_REFERRAL_LEVELS}
                """
            )

        total_referral_bonus = await conn.fetchval(total_referral_bonus_query, referrer_tg_id)
        logger.debug(f"Получена общая сумма бонусов от рефералов: {total_referral_bonus}")

        return {
            "total_referrals": total_referrals,
            "active_referrals": active_referrals,
            "referrals_by_level": referrals_by_level,
            "total_referral_bonus": total_referral_bonus,
        }

    except Exception as e:
        logger.error(f"Ошибка при получении статистики рефералов для пользователя {referrer_tg_id}: {e}")
        raise
    finally:
        if conn:
            await conn.close()
            logger.info("Закрытие подключения к базе данных")


async def update_key_expiry(client_id: str, new_expiry_time: int, session: Any):
    """
    Обновление времени истечения ключа для указанного клиента.

    Args:
        client_id (str): Уникальный идентификатор клиента
        new_expiry_time (int): Новое время истечения ключа
        session (Any): Сессия подключения к базе данных

    Raises:
        Exception: В случае ошибки при подключении к базе данных или обновлении ключа
    """
    try:
        await session.execute(
            """
            UPDATE keys
            SET expiry_time = $1, notified = FALSE, notified_24h = FALSE
            WHERE client_id = $2
        """,
            new_expiry_time,
            client_id,
        )
        logger.info(f"Успешно обновлено время истечения ключа для клиента {client_id}")

    except Exception as e:
        logger.error(f"Ошибка при обновлении времени истечения ключа для клиента {client_id}: {e}")
        raise


async def add_balance_to_client(client_id: str, amount: float):
    """
    Добавление баланса клиенту по его идентификатору Telegram.

    Args:
        client_id (str): Идентификатор клиента в Telegram
        amount (float): Сумма для пополнения баланса

    Raises:
        Exception: В случае ошибки при подключении к базе данных или обновлении баланса
    """
    conn = None
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        logger.info(f"Установлено подключение к базе данных для пополнения баланса клиента {client_id}")

        await conn.execute(
            """
            UPDATE connections
            SET balance = balance + $1
            WHERE tg_id = $2
            """,
            amount,
            client_id,
        )
        logger.info(f"Успешно пополнен баланс клиента {client_id} на сумму {amount}")

    except Exception as e:
        logger.error(f"Ошибка при пополнении баланса для клиента {client_id}: {e}")
        raise
    finally:
        if conn:
            await conn.close()
            logger.info("Закрытие подключения к базе данных")


async def get_client_id_by_email(email: str):
    """
    Получение идентификатора клиента по электронной почте.

    Args:
        email (str): Электронная почта клиента

    Returns:
        str: Идентификатор клиента или None, если клиент не найден

    Raises:
        Exception: В случае ошибки при подключении к базе данных или выполнении запроса
    """
    conn = None
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        logger.info(f"Установлено подключение к базе данных для поиска client_id по email: {email}")

        client_id = await conn.fetchval(
            """
            SELECT client_id FROM keys WHERE email = $1
        """,
            email,
        )

        if client_id:
            logger.info(f"Найден client_id для email: {email}")
        else:
            logger.warning(f"Не найден client_id для email: {email}")

        return client_id

    except Exception as e:
        logger.error(f"Ошибка при получении client_id для email {email}: {e}")
        raise
    finally:
        if conn:
            await conn.close()
            logger.info("Закрытие подключения к базе данных")


async def get_tg_id_by_client_id(client_id: str):
    """
    Получение Telegram ID по идентификатору клиента.

    Args:
        client_id (str): Идентификатор клиента

    Returns:
        int или None: Telegram ID клиента, если найден, иначе None

    Raises:
        Exception: В случае ошибки при подключении к базе данных или выполнении запроса
    """
    conn = None
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        logger.info(f"Установлено подключение к базе данных для поиска Telegram ID по client_id: {client_id}")

        result = await conn.fetchrow("SELECT tg_id FROM keys WHERE client_id = $1", client_id)

        if result:
            logger.info(f"Найден Telegram ID для client_id: {client_id}")
            return result["tg_id"]
        else:
            logger.warning(f"Не найден Telegram ID для client_id: {client_id}")
            return None

    except Exception as e:
        logger.error(f"Ошибка при получении Telegram ID для client_id {client_id}: {e}")
        raise
    finally:
        if conn:
            await conn.close()
            logger.info("Закрытие подключения к базе данных")


async def upsert_user(
    tg_id: int,
    username: str = None,
    first_name: str = None,
    last_name: str = None,
    language_code: str = None,
    is_bot: bool = False,
):
    """
    Обновляет или вставляет информацию о пользователе в базу данных.

    Args:
        tg_id (int): Идентификатор пользователя в Telegram
        username (str, optional): Имя пользователя в Telegram
        first_name (str, optional): Имя пользователя
        last_name (str, optional): Фамилия пользователя
        language_code (str, optional): Код языка пользователя
        is_bot (bool, optional): Флаг, указывающий является ли пользователь ботом

    Raises:
        Exception: В случае ошибки при работе с базой данных
    """
    conn = None
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        logger.info(f"Установлено подключение к базе данных для обновления пользователя {tg_id}")

        await conn.execute(
            """
            INSERT INTO users (tg_id, username, first_name, last_name, language_code, is_bot, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (tg_id) DO UPDATE 
            SET 
                username = COALESCE(EXCLUDED.username, users.username),
                first_name = COALESCE(EXCLUDED.first_name, users.first_name),
                last_name = COALESCE(EXCLUDED.last_name, users.last_name),
                language_code = COALESCE(EXCLUDED.language_code, users.language_code),
                is_bot = EXCLUDED.is_bot,
                updated_at = CURRENT_TIMESTAMP
            """,
            tg_id,
            username,
            first_name,
            last_name,
            language_code,
            is_bot,
        )
        logger.info(f"Успешно обновлена информация о пользователе {tg_id}")
    except Exception as e:
        logger.error(f"Ошибка при обновлении информации о пользователе {tg_id}: {e}")
        raise
    finally:
        if conn:
            await conn.close()
            logger.info("Закрытие подключения к базе данных")


async def add_payment(tg_id: int, amount: float, payment_system: str):
    """
    Добавляет информацию о платеже в базу данных.

    Args:
        tg_id (int): Идентификатор пользователя в Telegram
        amount (float): Сумма платежа
        payment_system (str): Система оплаты

    Raises:
        Exception: В случае ошибки при добавлении платежа
    """
    conn = None
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        logger.info(f"Установлено подключение к базе данных для добавления платежа пользователя {tg_id}")

        await conn.execute(
            """
            INSERT INTO payments (tg_id, amount, payment_system, status)
            VALUES ($1, $2, $3, 'success')
            """,
            tg_id,
            amount,
            payment_system,
        )
        logger.info(f"Успешно добавлен платеж для пользователя {tg_id} на сумму {amount}")
    except Exception as e:
        logger.error(f"Ошибка при добавлении платежа для пользователя {tg_id}: {e}")
        raise
    finally:
        if conn:
            await conn.close()
            logger.info("Закрытие подключения к базе данных после добавления платежа")


async def add_notification(tg_id: int, notification_type: str, session: Any):
    """
    Добавляет запись о notification в базу данных.

    Args:
        tg_id (int): Идентификатор пользователя в Telegram
        notification_type (str): Тип уведомления
        session (Any): Сессия базы данных для выполнения запроса

    Raises:
        Exception: В случае ошибки при добавлении notification
    """
    try:
        await session.execute(
            """
            INSERT INTO notifications (tg_id, notification_type)
            VALUES ($1, $2)
            ON CONFLICT (tg_id, notification_type) 
            DO UPDATE SET last_notification_time = NOW()
            """,
            tg_id,
            notification_type,
        )
        logger.info(f"Успешно добавлено уведомление типа {notification_type} для пользователя {tg_id}")
    except Exception as e:
        logger.error(f"Ошибка при добавлении notification для пользователя {tg_id}: {e}")
        raise


async def check_notification_time(tg_id: int, notification_type: str, hours: int = 12, session: Any = None) -> bool:
    """
    Проверяет, прошло ли указанное количество часов с момента последнего уведомления.

    Args:
        tg_id (int): Идентификатор пользователя в Telegram
        notification_type (str): Тип уведомления
        hours (int, optional): Количество часов для проверки. По умолчанию 12.
        session (Any): Сессия базы данных для выполнения запроса

    Returns:
        bool: True, если с момента последнего уведомления прошло больше указанного времени, иначе False

    Raises:
        Exception: В случае ошибки при проверке времени уведомления
    """
    conn = None
    try:
        conn = session if session is not None else await asyncpg.connect(DATABASE_URL)

        result = await conn.fetchval(
            """
            SELECT 
                CASE 
                    WHEN MAX(last_notification_time) IS NULL THEN TRUE
                    WHEN NOW() - MAX(last_notification_time) > ($1 * INTERVAL '1 hour') THEN TRUE
                    ELSE FALSE 
                END AS can_notify
            FROM notifications 
            WHERE tg_id = $2 AND notification_type = $3
            """,
            hours,
            tg_id,
            notification_type,
        )

        can_notify = result if result is not None else True

        logger.info(
            f"Проверка уведомления типа {notification_type} для пользователя {tg_id}: {'можно отправить' if can_notify else 'слишком рано'}"
        )

        return can_notify

    except Exception as e:
        logger.error(f"Ошибка при проверке времени уведомления для пользователя {tg_id}: {e}")
        return False

    finally:
        if conn is not None and session is None:
            await conn.close()


async def get_last_notification_time(tg_id: int, notification_type: str, session: Any = None) -> int:
    """
    Возвращает время последнего уведомления в миллисекундах (UTC).

    Args:
        tg_id (int): Telegram ID пользователя.
        notification_type (str): Тип уведомления.
        session (Any): Сессия базы данных.

    Returns:
        int: Время последнего уведомления в миллисекундах, или None, если уведомления не было.
    """
    conn = None
    try:
        conn = session if session is not None else await asyncpg.connect(DATABASE_URL)

        last_notification_time = await conn.fetchval(
            """
            SELECT EXTRACT(EPOCH FROM MAX(last_notification_time AT TIME ZONE 'Europe/Moscow' AT TIME ZONE 'UTC')) * 1000
            FROM notifications 
            WHERE tg_id = $1 AND notification_type = $2
            """,
            tg_id,
            notification_type,
        )

        return int(last_notification_time) if last_notification_time is not None else None

    except Exception as e:
        logger.error(f"Ошибка при получении времени последнего уведомления для пользователя {tg_id}: {e}")
        return None

    finally:
        if conn is not None and session is None:
            await conn.close()


async def get_servers(session: Any = None):
    conn = None
    try:
        conn = session if session is not None else await asyncpg.connect(DATABASE_URL)

        result = await conn.fetch(
            """
            SELECT cluster_name, server_name, api_url, subscription_url, inbound_id 
            FROM servers
            """
        )
        servers = {}
        for row in result:
            cluster_name = row["cluster_name"]
            if cluster_name not in servers:
                servers[cluster_name] = []

            servers[cluster_name].append(
                {
                    "server_name": row["server_name"],
                    "api_url": row["api_url"],
                    "subscription_url": row["subscription_url"],
                    "inbound_id": row["inbound_id"],
                }
            )

        return servers

    finally:
        if conn is not None and session is None:
            await conn.close()


async def delete_user_data(session: Any, tg_id: int):
    try:
        await session.execute("DELETE FROM gifts WHERE sender_tg_id = $1 OR recipient_tg_id = $1", tg_id)
    except Exception as e:
        logger.warning(f"У Вас версия без подарков для {tg_id}: {e}")
    await session.execute("DELETE FROM payments WHERE tg_id = $1", tg_id)
    await session.execute("DELETE FROM users WHERE tg_id = $1", tg_id)
    await session.execute("DELETE FROM connections WHERE tg_id = $1", tg_id)
    await delete_key(tg_id, session)
    await session.execute("DELETE FROM referrals WHERE referrer_tg_id = $1", tg_id)


async def store_gift_link(
    gift_id: str,
    sender_tg_id: int,
    selected_months: int,
    expiry_time: datetime,
    gift_link: str,
    session: Any = None,
):
    """
    Добавляет информацию о подарке в базу данных.

    Args:
        gift_id (str): Уникальный идентификатор подарка
        sender_tg_id (int): Идентификатор пользователя, который отправил подарок
        selected_months (int): Количество месяцев подписки
        expiry_time (datetime): Время окончания подписки
        gift_link (str): Ссылка для активации подарка
        session (Any): Сессия базы данных для выполнения запроса

    Returns:
        bool: True, если информация о подарке успешно добавлена, иначе False

    Raises:
        Exception: В случае ошибки при сохранении информации о подарке
    """
    conn = None
    try:
        conn = session if session is not None else await asyncpg.connect(DATABASE_URL)

        result = await conn.execute(
            """
            INSERT INTO gifts (gift_id, sender_tg_id, recipient_tg_id, selected_months, expiry_time, gift_link, created_at, is_used)
            VALUES ($1, $2, NULL, $3, $4, $5, $6, FALSE)
            """,
            gift_id,
            sender_tg_id,
            selected_months,
            expiry_time,
            gift_link,
            datetime.utcnow(),
        )

        if result:
            logger.info(f"Подарок с ID {gift_id} успешно добавлен в базу данных.")
            return True
        else:
            logger.error(f"Не удалось добавить подарок с ID {gift_id} в базу данных.")
            return False
    except Exception as e:
        logger.error(f"Ошибка при сохранении подарка с ID {gift_id} в базе данных: {e}")
        return False

    finally:
        if conn is not None and session is None:
            await conn.close()


async def get_key_details(email, session):
    record = await session.fetchrow(
        """
        SELECT k.server_id, k.key, k.email, k.expiry_time, k.client_id, k.created_at, c.tg_id, c.balance
        FROM keys k
        JOIN connections c ON k.tg_id = c.tg_id
        WHERE k.email = $1
        """,
        email,
    )

    if not record:
        return None

    cluster_name = record["server_id"]

    moscow_tz = pytz.timezone("Europe/Moscow")
    expiry_date = datetime.fromtimestamp(record["expiry_time"] / 1000, tz=moscow_tz)
    current_date = datetime.now(moscow_tz)
    time_left = expiry_date - current_date

    if time_left.total_seconds() <= 0:
        days_left_message = "<b>Ключ истек.</b>"
    elif time_left.days > 0:
        days_left_message = f"Осталось дней: <b>{time_left.days}</b>"
    else:
        hours_left = time_left.seconds // 3600
        days_left_message = f"Осталось часов: <b>{hours_left}</b>"

    return {
        "key": record["key"],
        "server_id": record["server_id"],
        "created_at": record["created_at"],
        "expiry_time": record["expiry_time"],
        "client_id": record["client_id"],
        "expiry_date": expiry_date.strftime("%d %B %Y года %H:%M"),
        "days_left_message": days_left_message,
        "server_name": cluster_name,
        "balance": record["balance"],
        "tg_id": record["tg_id"],
        "email": record["email"],
    }


async def delete_key(identifier, session):
    """
    Удаляет ключ из базы данных по client_id или tg_id

    Args:
        identifier (int | str): client_id или tg_id для удаления
        session: Сессия базы данных

    Raises:
        Exception: В случае ошибки при удалении ключа
    """
    try:
        identifier_str = str(identifier)

        if identifier_str.isdigit():
            query = "DELETE FROM keys WHERE tg_id = $1"
        else:
            query = "DELETE FROM keys WHERE client_id = $1"

        await session.execute(query, identifier)
        logger.info(f"Ключ с идентификатором {identifier} успешно удалён")
    except Exception as e:
        logger.error(f"Ошибка при удалении ключа с идентификатором {identifier} из базы данных: {e}")


async def create_server(
    cluster_name: str, server_name: str, api_url: str, subscription_url: str, inbound_id: int, session: Any
):
    """
    Добавляет новый сервер в базу данных.

    Args:
        cluster_name (str): Название кластера
        server_name (str): Название сервера
        api_url (str): URL API сервера
        subscription_url (str): URL подписки
        inbound_id (int): ID входящего подключения
        session (Any): Сессия базы данных

    Raises:
        Exception: В случае ошибки при добавлении сервера
    """
    try:
        await session.execute(
            """
            INSERT INTO servers (cluster_name, server_name, api_url, subscription_url, inbound_id)
            VALUES ($1, $2, $3, $4, $5)
            """,
            cluster_name,
            server_name,
            api_url,
            subscription_url,
            inbound_id,
        )
        logger.info(f"Сервер {server_name} успешно добавлен в кластер {cluster_name}")
    except Exception as e:
        logger.error(f"Ошибка при добавлении сервера {server_name} в кластер {cluster_name}: {e}")
        raise


async def delete_server(server_name: str, session: Any):
    """
    Удаляет сервер из базы данных по его названию.

    Args:
        server_name (str): Название сервера для удаления
        session (Any): Сессия базы данных

    Raises:
        Exception: В случае ошибки при удалении сервера
    """
    try:
        await session.execute(
            """
            DELETE FROM servers WHERE server_name = $1
            """,
            server_name,
        )
        logger.info(f"Сервер {server_name} успешно удалён из базы данных")
    except Exception as e:
        logger.error(f"Ошибка при удалении сервера {server_name} из базы данных: {e}")
        raise


async def create_coupon_usage(coupon_id: int, user_id: int, session: Any):
    """
    Создаёт запись об использовании купона в базе данных.

    Args:
        coupon_id (int): ID купона
        user_id (int): ID пользователя
        session (Any): Сессия базы данных

    Raises:
        Exception: В случае ошибки при создании записи
    """
    try:
        await session.execute(
            """
            INSERT INTO coupon_usages (coupon_id, user_id, used_at)
            VALUES ($1, $2, $3)
            """,
            coupon_id,
            user_id,
            datetime.utcnow(),
        )
        logger.info(f"Создана запись об использовании купона {coupon_id} пользователем {user_id}")
    except Exception as e:
        logger.error(f"Ошибка при создании записи об использовании купона {coupon_id} пользователем {user_id}: {e}")
        raise


async def check_coupon_usage(coupon_id: int, user_id: int, session: Any) -> bool:
    """
    Проверяет, использовал ли пользователь данный купон.

    Args:
        coupon_id (int): ID купона для проверки
        user_id (int): ID пользователя для проверки
        session (Any): Сессия базы данных

    Returns:
        bool: True если купон уже использован, False если нет

    Raises:
        Exception: В случае ошибки при выполнении запроса
    """
    try:
        result = await session.fetchrow(
            """
            SELECT 1 FROM coupon_usages WHERE coupon_id = $1 AND user_id = $2
            """,
            coupon_id,
            user_id,
        )
        return result is not None
    except Exception as e:
        logger.error(f"Ошибка при проверке использования купона {coupon_id} пользователем {user_id}: {e}")
        raise


async def update_coupon_usage_count(coupon_id: int, session: Any):
    """
    Обновляет счетчик использования купона и его статус.

    Args:
        coupon_id (int): ID купона для обновления
        session (Any): Сессия базы данных

    Raises:
        Exception: В случае ошибки при обновлении данных купона
    """
    try:
        await session.execute(
            """
            UPDATE coupons
            SET usage_count = usage_count + 1,
                is_used = CASE WHEN usage_count + 1 >= usage_limit AND usage_limit > 0 THEN TRUE ELSE FALSE END
            WHERE id = $1
            """,
            coupon_id,
        )
        logger.info(f"Успешно обновлен счетчик использования купона {coupon_id}")
    except Exception as e:
        logger.error(f"Ошибка при обновлении счетчика использования купона {coupon_id}: {e}")
        raise


async def get_last_payments(tg_id: int, session: Any):
    """
    Получает последние 3 платежа пользователя.

    Args:
        tg_id (int): Telegram ID пользователя
        session (Any): Сессия базы данных

    Returns:
        list: Список последних платежей пользователя

    Raises:
        Exception: В случае ошибки при выполнении запроса
    """
    try:
        records = await session.fetch(
            """
            SELECT amount, payment_system, status, created_at
            FROM payments 
            WHERE tg_id = $1
            ORDER BY created_at DESC
            LIMIT 3
            """,
            tg_id,
        )
        logger.info(f"Успешно получены последние платежи для пользователя {tg_id}")
        return records
    except Exception as e:
        logger.error(f"Ошибка при получении последних платежей для пользователя {tg_id}: {e}")
        raise


async def get_coupon_details(coupon_id: str, session: Any):
    """
    Получает детали купона по его ID.

    Args:
        coupon_id (str): ID купона
        session (Any): Сессия базы данных

    Returns:
        dict: Словарь с деталями купона или None если купон не найден

    Raises:
        Exception: В случае ошибки при выполнении запроса
    """
    try:
        record = await session.fetchrow(
            """
            SELECT id, code, discount, usage_count, usage_limit, is_used
            FROM coupons
            WHERE id = $1
            """,
            coupon_id,
        )

        if record:
            logger.info(f"Успешно получены детали купона {coupon_id}")
            return dict(record)

        logger.warning(f"Купон {coupon_id} не найден")
        return None

    except Exception as e:
        logger.error(f"Ошибка при получении деталей купона {coupon_id}: {e}")
        raise


async def get_referral_by_referred_id(referred_tg_id: int, session: Any):
    """
    Получает информацию о реферале по ID приглашенного пользователя.

    Args:
        referred_tg_id (int): ID приглашенного пользователя
        session (Any): Сессия базы данных

    Returns:
        dict: Словарь с информацией о реферале или None если не найден

    Raises:
        Exception: В случае ошибки при выполнении запроса
    """
    try:
        record = await session.fetchrow(
            """
            SELECT * FROM referrals 
            WHERE referred_tg_id = $1
            """,
            referred_tg_id,
        )

        if record:
            logger.info(f"Успешно получена информация о реферале для пользователя {referred_tg_id}")
            return dict(record)

        logger.info(f"Реферал для пользователя {referred_tg_id} не найден")
        return None

    except Exception as e:
        logger.error(f"Ошибка при получении информации о реферале для пользователя {referred_tg_id}: {e}")
        raise


async def get_all_keys(session: Any = None):
    """
    Получает все записи из таблицы keys.

    Args:
        session (Any, optional): Сессия базы данных. По умолчанию None.

    Returns:
        list: Список всех записей из таблицы keys

    Raises:
        Exception: В случае ошибки при выполнении запроса
    """
    conn = None
    try:
        conn = session if session is not None else await asyncpg.connect(DATABASE_URL)
        keys = await conn.fetch("SELECT * FROM keys")
        logger.info(f"Успешно получены все записи из таблицы keys. Количество: {len(keys)}")
        return keys
    except Exception as e:
        logger.error(f"Ошибка при получении записей из таблицы keys: {e}")
        raise
    finally:
        if conn is not None and session is None:
            await conn.close()

import json
from datetime import datetime
from typing import Any

import asyncpg

from config import DATABASE_URL, REFERRAL_BONUS_PERCENTAGES
from logger import logger


async def save_temporary_data(session, tg_id: int, state: str, data: dict):
    """Сохраняет временные данные пользователя."""
    await session.execute(
        """
        INSERT INTO temporary_data (tg_id, state, data, updated_at)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (tg_id)
        DO UPDATE SET state = $2, data = $3, updated_at = $4
        """,
        tg_id, state, json.dumps(data), datetime.utcnow()
    )

async def get_temporary_data(session, tg_id: int) -> dict | None:
    """Извлекает временные данные пользователя."""
    result = await session.fetchrow(
        "SELECT state, data FROM temporary_data WHERE tg_id = $1",
        tg_id
    )
    if result:
        return {
            "state": result["state"],
            "data": json.loads(result["data"])
        }
    return None

async def clear_temporary_data(session, tg_id: int):
    await session.execute(
        "DELETE FROM temporary_data WHERE tg_id = $1",
        tg_id
    )

async def add_blocked_user(tg_id: int, conn: asyncpg.Connection):
    await conn.execute(
        "INSERT INTO blocked_users (tg_id) VALUES ($1) ON CONFLICT (tg_id) DO NOTHING",
        tg_id
    )



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


async def check_unique_server_name(server_name: str) -> bool:
    """
    Проверяет уникальность имени сервера.

    :param server_name: Имя сервера.
    :return: True, если имя сервера уникально, False, если уже существует.
    """
    conn = await asyncpg.connect(DATABASE_URL)

    result = await conn.fetchrow(
        "SELECT 1 FROM servers WHERE server_name = $1 LIMIT 1", server_name
    )

    await conn.close()

    return result is None


async def create_coupon(
    coupon_code: str, amount: float, usage_limit: int, session: Any
):
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


async def get_all_coupons(session: Any):
    """
    Получает список всех купонов из базы данных.

    Returns:
        list: Список словарей с информацией о купонах, каждый словарь содержит:
            - code (str): Код купона
            - amount (int): Сумма купона
            - usage_limit (int): Максимальное количество использований
            - usage_count (int): Текущее количество использований купона

    Raises:
        Exception: В случае ошибки при получении данных из базы
    """
    try:
        coupons = await session.fetch(
            """
            SELECT code, amount, usage_limit, usage_count
            FROM coupons
        """
        )

        logger.info(f"Успешно получено {len(coupons)} купонов из базы данных")

        return coupons
    except Exception as e:
        logger.error(f"Критическая ошибка при получении списка купонов: {e}")
        logger.exception("Трассировка стека ошибки получения купонов")
        return []


async def delete_coupon_from_db(coupon_code: str, session: Any):
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
        result = await delete_coupon_from_db('SALE50', session)
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


async def restore_trial(tg_id: int, session: Any):
    """
    Восстанавливает возможность использования триального периода для пользователя.

    Args:
        tg_id (int): Telegram ID пользователя
        session (Any): Сессия базы данных

    Returns:
        bool: True, если триал успешно восстановлен, False в случае ошибки
    """
    try:
        await session.execute(
            """
            INSERT INTO connections (tg_id, trial) 
            VALUES ($1, 0) 
            ON CONFLICT (tg_id) 
            DO UPDATE SET trial = 0
            """,
            tg_id,
        )
        logger.info(f"Триальный период успешно восстановлен для пользователя {tg_id}")
        return True
    except Exception as e:
        logger.error(
            f"Ошибка при восстановлении триального периода для пользователя {tg_id}: {e}"
        )
        return False


async def use_trial(tg_id: int, session: Any):
    """
    Отмечает использование триального периода для пользователя.

    Args:
        tg_id (int): Telegram ID пользователя
        session (Any): Сессия базы данных

    Returns:
        bool: True, если триал успешно использован, False в случае ошибки
    """
    try:
        await session.execute(
            """
            INSERT INTO connections (tg_id, trial) 
            VALUES ($1, 1) 
            ON CONFLICT (tg_id) 
            DO UPDATE SET trial = 1
            """,
            tg_id,
        )
        logger.info(f"Триальный период успешно использован для пользователя {tg_id}")
        return True
    except Exception as e:
        logger.error(f"Ошибка при использовании триала для пользователя {tg_id}: {e}")
        return False


async def add_connection(
    tg_id: int, balance: float = 0.0, trial: int = 0, session: Any = None
):
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
        logger.error(
            f"Не удалось добавить подключение для пользователя {tg_id}. Причина: {e}"
        )
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
        logger.info(
            f"Ключ успешно сохранен для пользователя {tg_id} на сервере {server_id}"
        )
    except Exception as e:
        logger.error(f"Ошибка при сохранении ключа для пользователя {tg_id}: {e}")
        raise


async def get_keys(tg_id: int):
    """
    Получает список ключей для указанного пользователя.

    Args:
        tg_id (int): Telegram ID пользователя

    Returns:
        list: Список записей ключей с информацией о клиенте, электронной почте, времени создания и ключе

    Raises:
        Exception: В случае ошибки при подключении к базе данных или выполнении запроса
    """
    conn = None
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        records = await conn.fetch(
            """
            SELECT client_id, email, created_at, key
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
    finally:
        if conn:
            await conn.close()


async def get_keys_by_server(tg_id: int, server_id: str):
    """
    Получает список ключей для указанного пользователя на определенном сервере.

    Args:
        tg_id (int): Telegram ID пользователя
        server_id (str): Идентификатор сервера

    Returns:
        list: Список записей ключей с информацией о клиенте, электронной почте, времени создания и ключе

    Raises:
        Exception: В случае ошибки при подключении к базе данных или выполнении запроса
    """
    conn = None
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        records = await conn.fetch(
            """
            SELECT client_id, email, created_at, key
            FROM keys
            WHERE tg_id = $1 AND server_id = $2
            """,
            tg_id,
            server_id,
        )
        logger.info(
            f"Успешно получено {len(records)} ключей для пользователя {tg_id} на сервере {server_id}"
        )
        return records
    except Exception as e:
        logger.error(
            f"Ошибка при получении ключей для пользователя {tg_id} на сервере {server_id}: {e}"
        )
        raise
    finally:
        if conn:
            await conn.close()


async def has_active_key(tg_id: int) -> bool:
    """
    Проверяет наличие активных ключей для указанного пользователя.

    Args:
        tg_id (int): Telegram ID пользователя

    Returns:
        bool: True, если у пользователя есть активные ключи, иначе False

    Raises:
        Exception: В случае ошибки при подключении к базе данных или выполнении запроса
    """
    conn = None
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        count = await conn.fetchval("SELECT COUNT(*) FROM keys WHERE tg_id = $1", tg_id)
        logger.info(
            f"Проверка наличия ключей для пользователя {tg_id}. Найдено ключей: {count}"
        )
        return count > 0
    except Exception as e:
        logger.error(
            f"Ошибка при проверке наличия ключей для пользователя {tg_id}: {e}"
        )
        raise
    finally:
        if conn:
            await conn.close()


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
        balance = await conn.fetchval(
            "SELECT balance FROM connections WHERE tg_id = $1", tg_id
        )
        logger.info(f"Получен баланс для пользователя {tg_id}: {balance}")
        return balance if balance is not None else 0.0
    except Exception as e:
        logger.error(f"Ошибка при получении баланса для пользователя {tg_id}: {e}")
        return 0.0
    finally:
        if conn:
            await conn.close()


async def update_balance(tg_id: int, amount: float):
    """
    Обновляет баланс пользователя в базе данных.

    Args:
        tg_id (int): Telegram ID пользователя
        amount (float): Сумма для обновления баланса

    Raises:
        Exception: В случае ошибки при подключении к базе данных или обновлении баланса
    """
    conn = None
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        await conn.execute(
            """
            UPDATE connections
            SET balance = balance + $1
            WHERE tg_id = $2
            """,
            amount,
            tg_id,
        )
        logger.info(f"Баланс пользователя {tg_id} обновлен на сумму {amount}")

        await handle_referral_on_balance_update(tg_id, amount)

    except Exception as e:
        logger.error(f"Ошибка при обновлении баланса для пользователя {tg_id}: {e}")
        raise
    finally:
        if conn:
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
        trial = await session.fetchval(
            "SELECT trial FROM connections WHERE tg_id = $1", tg_id
        )
        logger.info(f"Получен статус триала для пользователя {tg_id}: {trial}")
        return trial if trial is not None else 0
    except Exception as e:
        logger.error(
            f"Ошибка при получении статуса триала для пользователя {tg_id}: {e}"
        )
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
        logger.error(
            f"Ошибка при получении количества ключей для пользователя {tg_id}: {e}"
        )
        return 0
    finally:
        if conn:
            await conn.close()


async def get_all_users(conn):
    """
    Получает список всех пользователей из базы данных.

    Args:
        conn: Подключение к базе данных

    Returns:
        list: Список Telegram ID всех пользователей

    Raises:
        Exception: В случае ошибки при получении данных
    """
    try:
        users = await conn.fetch("SELECT tg_id FROM connections")
        logger.info(f"Получен список всех пользователей. Количество: {len(users)}")
        return users
    except Exception as e:
        logger.error(f"Ошибка при получении списка пользователей: {e}")
        raise


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
        logger.info(
            f"Добавлена реферальная связь: приглашенный {referred_tg_id}, пригласивший {referrer_tg_id}"
        )
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
                logger.warning(
                    f"Обнаружен цикл в реферальной цепочке для пользователя {current_tg_id}. Прекращение."
                )
                break

            visited_tg_ids.add(current_tg_id)

            referral = await conn.fetchrow(
                """
                SELECT referrer_tg_id 
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

            referral_chain.append({"tg_id": referrer_tg_id, "level": level})
            current_tg_id = referrer_tg_id

        for referral in referral_chain:
            referrer_tg_id = referral["tg_id"]
            level = referral["level"]

            bonus_percent = REFERRAL_BONUS_PERCENTAGES.get(level, 0)
            if bonus_percent <= 0:
                logger.warning(f"Процент бонуса для уровня {level} равен 0. Пропуск.")
                continue

            bonus = round(amount * bonus_percent, 2)

            if bonus > 0:
                logger.info(
                    f"Начисление бонуса {bonus} рублей рефереру {referrer_tg_id} на уровне {level}."
                )
                await update_balance(referrer_tg_id, bonus)

    except Exception as e:
        logger.error(
            f"Ошибка при обработке многоуровневой реферальной системы для {tg_id}: {e}"
        )
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
            SELECT COUNT(*) FROM referrals WHERE referrer_tg_id = $1
            """,
            referrer_tg_id,
        )
        logger.debug(f"Получено общее количество рефералов: {total_referrals}")

        active_referrals = await conn.fetchval(
            """
            SELECT COUNT(*) FROM referrals WHERE referrer_tg_id = $1 AND reward_issued = TRUE
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

        total_referral_bonus = await conn.fetchval(
            f"""
            WITH RECURSIVE referral_levels AS (
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
            SELECT 
                SUM(p.amount * CASE 
                    {" ".join([f"WHEN rl.level = {level} THEN {REFERRAL_BONUS_PERCENTAGES[level]}" for level in REFERRAL_BONUS_PERCENTAGES])}
                    ELSE 0
                END) AS total_bonus
            FROM referral_levels rl
            JOIN payments p ON rl.referred_tg_id = p.tg_id
            WHERE p.status = 'success'
            """,
            referrer_tg_id,
        )

        total_referral_bonus = total_referral_bonus or 0
        logger.debug(
            f"Получена общая сумма бонусов от рефералов: {total_referral_bonus}"
        )

        return {
            "total_referrals": total_referrals,
            "active_referrals": active_referrals,
            "referrals_by_level": referrals_by_level,
            "total_referral_bonus": total_referral_bonus,
        }

    except Exception as e:
        logger.error(
            f"Ошибка при получении статистики рефералов для пользователя {referrer_tg_id}: {e}"
        )
        raise
    finally:
        if conn:
            await conn.close()
            logger.info("Закрытие подключения к базе данных")


async def update_key_expiry(client_id: str, new_expiry_time: int):
    """
    Обновление времени истечения ключа для указанного клиента.

    Args:
        client_id (str): Уникальный идентификатор клиента
        new_expiry_time (int): Новое время истечения ключа

    Raises:
        Exception: В случае ошибки при подключении к базе данных или обновлении ключа
    """
    conn = None
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        logger.info(
            f"Установлено подключение к базе данных для обновления времени истечения ключа клиента {client_id}"
        )

        await conn.execute(
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
        logger.error(
            f"Ошибка при обновлении времени истечения ключа для клиента {client_id}: {e}"
        )
        raise
    finally:
        if conn:
            await conn.close()
            logger.info("Закрытие подключения к базе данных")


async def delete_key(client_id: str):
    """
    Удаление ключа из базы данных для указанного клиента.

    Args:
        client_id (str): Уникальный идентификатор клиента, ключ которого будет удален

    Raises:
        Exception: В случае ошибки при подключении к базе данных или удалении ключа
    """
    conn = None
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        logger.info(
            f"Установлено подключение к базе данных для удаления ключа клиента {client_id}"
        )

        await conn.execute(
            """
            DELETE FROM keys
            WHERE client_id = $1
            """,
            client_id,
        )
        logger.info(f"Успешно удален ключ для клиента {client_id}")

    except Exception as e:
        logger.error(f"Ошибка при удалении ключа для клиента {client_id}: {e}")
        raise
    finally:
        if conn:
            await conn.close()
            logger.info("Закрытие подключения к базе данных")


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
        logger.info(
            f"Установлено подключение к базе данных для пополнения баланса клиента {client_id}"
        )

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
        logger.info(
            f"Установлено подключение к базе данных для поиска client_id по email: {email}"
        )

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
        logger.info(
            f"Установлено подключение к базе данных для поиска Telegram ID по client_id: {client_id}"
        )

        result = await conn.fetchrow(
            "SELECT tg_id FROM keys WHERE client_id = $1", client_id
        )

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
        logger.info(
            f"Установлено подключение к базе данных для обновления пользователя {tg_id}"
        )

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
        logger.info(
            f"Установлено подключение к базе данных для добавления платежа пользователя {tg_id}"
        )

        await conn.execute(
            """
            INSERT INTO payments (tg_id, amount, payment_system, status)
            VALUES ($1, $2, $3, 'success')
            """,
            tg_id,
            amount,
            payment_system,
        )
        logger.info(
            f"Успешно добавлен платеж для пользователя {tg_id} на сумму {amount}"
        )
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
        logger.info(
            f"Успешно добавлено уведомление типа {notification_type} для пользователя {tg_id}"
        )
    except Exception as e:
        logger.error(
            f"Ошибка при добавлении notification для пользователя {tg_id}: {e}"
        )
        raise


async def check_notification_time(
    tg_id: int, notification_type: str, hours: int = 12, session: Any = None
) -> bool:
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
        logger.error(
            f"Ошибка при проверке времени уведомления для пользователя {tg_id}: {e}"
        )
        return False

    finally:
        if conn is not None and session is None:
            await conn.close()


async def get_servers_from_db():
    conn = await asyncpg.connect(DATABASE_URL)

    result = await conn.fetch(
        """
        SELECT cluster_name, server_name, api_url, subscription_url, inbound_id 
        FROM servers
        """
    )

    await conn.close()

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


async def delete_user_data(session: Any, tg_id: int):

    try:
        await session.execute("DELETE FROM gifts WHERE sender_tg_id = $1 OR recipient_tg_id = $1", tg_id)
    except Exception as e:
        logger.warning(
            f"У Вас версия без подарков для {tg_id}: {e}"
        )
    await session.execute("DELETE FROM payments WHERE tg_id = $1", tg_id)
    await session.execute("DELETE FROM users WHERE tg_id = $1", tg_id)
    await session.execute("DELETE FROM connections WHERE tg_id = $1", tg_id)
    await session.execute("DELETE FROM keys WHERE tg_id = $1", tg_id)
    await session.execute("DELETE FROM referrals WHERE referrer_tg_id = $1", tg_id)


async def store_gift_link(
    gift_id: str, sender_tg_id: int, selected_months: int, expiry_time: datetime, gift_link: str, session: Any = None
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

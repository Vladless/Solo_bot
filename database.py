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
    await session.execute("DELETE FROM temporary_data WHERE tg_id = $1", tg_id)


async def create_blocked_user(tg_id: int, conn: asyncpg.Connection):
    await conn.execute(
        "INSERT INTO blocked_users (tg_id) VALUES ($1) ON CONFLICT (tg_id) DO NOTHING",
        tg_id,
    )


async def delete_blocked_user(tg_id: int | list[int], conn: asyncpg.Connection):
    """
    Удаляет пользователя или список пользователей из списка заблокированных.
    """
    if isinstance(tg_id, list):
        await conn.execute("DELETE FROM blocked_users WHERE tg_id = ANY($1)", tg_id)
    else:
        await conn.execute("DELETE FROM blocked_users WHERE tg_id = $1", tg_id)


async def init_db(file_path: str = "assets/schema.sql"):
    with open(file_path) as file:
        sql_content = file.read()

    conn = await asyncpg.connect(DATABASE_URL)

    try:
        await conn.execute(sql_content)
    except Exception as e:
        logger.error(f"Error while executing SQL statement: {e}")
    finally:
        logger.info("Tables created successfully")
        await conn.close()


async def check_unique_server_name(server_name: str, session: Any, cluster_name: str | None = None) -> bool:
    """
    Проверяет уникальность имени сервера.
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


async def create_coupon(coupon_code: str, amount: int, usage_limit: int, session: Any, days: int = None):
    """
    Создает новый купон в базе данных.
    """
    try:
        await session.execute(
            """
            INSERT INTO coupons (code, amount, usage_limit, usage_count, is_used, days)
            VALUES ($1, $2, $3, 0, FALSE, $4)
            """,
            coupon_code,
            amount,
            usage_limit,
            days,
        )
        logger.info(f"Успешно создан купон с кодом {coupon_code} на сумму {amount} или {days} дней")
    except Exception as e:
        logger.error(f"Ошибка при создании купона {coupon_code}: {e}")
        raise


async def get_coupon_by_code(coupon_code: str, session: Any) -> dict | None:
    """
    Получает информацию о купоне по его коду.
    """
    try:
        result = await session.fetchrow(
            """
            SELECT id, usage_limit, usage_count, is_used, amount, days
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
    """
    try:
        offset = (page - 1) * per_page
        coupons = await session.fetch(
            """
            SELECT id, code, amount, usage_limit, usage_count, days, is_used  -- Добавлено id
            FROM coupons
            ORDER BY id
            LIMIT $1 OFFSET $2
            """,
            per_page,
            offset,
        )
        total_count = await session.fetchval("SELECT COUNT(*) FROM coupons")
        total_pages = -(-total_count // per_page)
        logger.info(f"Успешно получено {len(coupons)} купонов из базы данных (страница {page})")
        return {"coupons": coupons, "total": total_count, "pages": total_pages, "current_page": page}
    except Exception as e:
        logger.error(f"Критическая ошибка при получении списка купонов: {e}")
        logger.exception("Трассировка стека ошибки получения купонов")
        return {"coupons": [], "total": 0, "pages": 0, "current_page": page}


async def delete_coupon(coupon_code: str, session: Any):
    """
    Удаляет купон из базы данных по его коду.
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
    """
    try:
        await session.execute(
            """
            UPDATE users SET trial = $1 WHERE tg_id = $2
            """,
            status,
            tg_id,
        )
        status_text = "восстановлен" if status == 0 else "использован"
        logger.info(f"Триальный период успешно {status_text} для пользователя {tg_id}")
        return True
    except Exception as e:
        logger.error(f"Ошибка при установке статуса триального периода для пользователя {tg_id}: {e}")
        return False


async def add_user(
    tg_id: int,
    username: str = None,
    first_name: str = None,
    last_name: str = None,
    language_code: str = None,
    is_bot: bool = False,
    session: Any = None,
    source_code: str = None,
):
    """
    Добавляет нового пользователя в таблицу users.
    """
    try:
        await session.execute(
            """
            INSERT INTO users (tg_id, username, first_name, last_name, language_code, is_bot, source_code)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (tg_id) DO NOTHING
            """,
            tg_id,
            username,
            first_name,
            last_name,
            language_code,
            is_bot,
            source_code,
        )
        logger.info(f"[DB] Новый пользователь добавлен: {tg_id} (source: {source_code})")
    except Exception as e:
        logger.error(f"[DB] Ошибка при добавлении пользователя {tg_id}: {e}")
        raise


async def check_user_exists(tg_id: int) -> bool:
    """
    Проверяет существование пользователя в таблице users.
    """
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        exists = await conn.fetchval("SELECT EXISTS(SELECT 1 FROM users WHERE tg_id = $1)", tg_id)
        logger.info(f"[DB] Пользователь {tg_id} {'найден' if exists else 'не найден'}")
        return exists
    except Exception as e:
        logger.error(f"[DB] Ошибка при проверке пользователя {tg_id}: {e}")
        return False
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
    remnawave_link: str = None,
):
    """
    Сохраняет информацию о ключе в базу данных, если ключ ещё не существует.
    """
    try:
        existing_key = await session.fetchrow(
            "SELECT 1 FROM keys WHERE tg_id = $1 AND client_id = $2",
            tg_id,
            client_id,
        )

        if existing_key:
            logger.info(f"[Store Key] Ключ уже существует — пропускаем: tg_id={tg_id}, client_id={client_id}")
            return

        await session.execute(
            """
            INSERT INTO keys (tg_id, client_id, email, created_at, expiry_time, key, server_id, remnawave_link)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            tg_id,
            client_id,
            email,
            int(datetime.utcnow().timestamp() * 1000),
            expiry_time,
            key,
            server_id,
            remnawave_link,
        )
        logger.info(f"✅ Ключ сохранён: tg_id={tg_id}, client_id={client_id}, server_id={server_id}")

    except Exception as e:
        logger.error(f"❌ Ошибка при сохранении ключа для tg_id={tg_id}, client_id={client_id}: {e}")
        raise


async def get_clusters(session) -> list[str]:
    """
    Получает список уникальных имён кластеров из таблицы servers.
    """
    rows = await session.fetch("SELECT DISTINCT cluster_name FROM servers ORDER BY cluster_name")
    return [row["cluster_name"] for row in rows]


async def get_keys(tg_id: int, session: Any):
    """
    Получает список ключей для указанного пользователя.
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
    """
    conn = None
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        balance = await conn.fetchval("SELECT balance FROM users WHERE tg_id = $1", tg_id)
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
    skip_referral: bool = False,
    skip_cashback: bool = False,
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

        if CASHBACK > 0 and amount > 0 and not is_admin and not skip_cashback:
            extra = amount * (CASHBACK / 100.0)
        else:
            extra = 0

        total_amount = int(amount + extra)

        current_balance = await session.fetchval("SELECT balance FROM users WHERE tg_id = $1", tg_id) or 0

        new_balance = current_balance + total_amount

        await session.execute(
            """
            UPDATE users
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
    Получает статус триала для пользователя из таблицы users.
    """
    try:
        trial = await session.fetchval("SELECT trial FROM users WHERE tg_id = $1", tg_id)
        logger.info(f"[DB] Статус триала для пользователя {tg_id}: {trial}")
        return trial if trial is not None else 0
    except Exception as e:
        logger.error(f"[DB] Ошибка получения trial для пользователя {tg_id}: {e}")
        return 0


async def get_key_count(tg_id: int) -> int:
    """
    Получает количество ключей для указанного пользователя.
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
    """

    if amount <= 0:
        return
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

            bonus_val = REFERRAL_BONUS_PERCENTAGES.get(level, 0)
            if bonus_val <= 0:
                logger.warning(f"Процент бонуса для уровня {level} равен 0. Пропуск.")
                continue

            if bonus_val < 1:
                bonus_amount = round(amount * bonus_val, 2)
            else:
                bonus_amount = bonus_val

            logger.info(f"Начисление бонуса {bonus_amount} рублей рефереру {referrer_tg_id} на уровне {level}.")
            await update_balance(referrer_tg_id, bonus_amount, skip_referral=True, skip_cashback=True)

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


async def get_total_referrals(conn, referrer_tg_id: int) -> int:
    total = await conn.fetchval(
        """
        SELECT COUNT(*) 
        FROM referrals 
        WHERE referrer_tg_id = $1
        """,
        referrer_tg_id,
    )
    logger.debug(f"Получено общее количество рефералов: {total}")
    return total


async def get_active_referrals(conn, referrer_tg_id: int) -> int:
    active = await conn.fetchval(
        """
        SELECT COUNT(*) 
        FROM referrals 
        WHERE referrer_tg_id = $1 AND reward_issued = TRUE
        """,
        referrer_tg_id,
    )
    logger.debug(f"Получено количество активных рефералов: {active}")
    return active


async def get_referrals_by_level(conn, referrer_tg_id: int, max_levels: int) -> dict:
    query = f"""
        WITH RECURSIVE referral_levels AS (
            SELECT referred_tg_id, referrer_tg_id, 1 AS level
            FROM referrals 
            WHERE referrer_tg_id = $1
            
            UNION
            
            SELECT r.referred_tg_id, r.referrer_tg_id, rl.level + 1
            FROM referrals r
            JOIN referral_levels rl ON r.referrer_tg_id = rl.referred_tg_id
            WHERE rl.level < {max_levels}
        )
        SELECT level, 
               COUNT(*) AS level_count, 
               COUNT(CASE WHEN reward_issued = TRUE THEN 1 END) AS active_level_count
        FROM referral_levels rl
        JOIN referrals r ON rl.referred_tg_id = r.referred_tg_id
        GROUP BY level
        ORDER BY level
    """
    records = await conn.fetch(query, referrer_tg_id)
    referrals_by_level = {
        record["level"]: {
            "total": record["level_count"],
            "active": record["active_level_count"],
        }
        for record in records
    }
    logger.debug(f"Получена статистика рефералов по уровням: {referrals_by_level}")
    return referrals_by_level


async def get_total_referral_bonus(conn, referrer_tg_id: int, max_levels: int) -> float:
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
                WHERE rl.level < {max_levels} AND r.reward_issued = TRUE
            ),
            earliest_payments AS (
                SELECT DISTINCT ON (tg_id) tg_id, amount, created_at
                FROM payments
                WHERE status = 'success'
                ORDER BY tg_id, created_at
            )
        """
        bonus_query = (
            bonus_cte
            + f"""
            SELECT 
                COALESCE(SUM(
                    CASE
                        {
                " ".join([
                    f"WHEN rl.level = {level} THEN {REFERRAL_BONUS_PERCENTAGES[level]} * ep.amount"
                    if isinstance(REFERRAL_BONUS_PERCENTAGES[level], float)
                    else f"WHEN rl.level = {level} THEN {REFERRAL_BONUS_PERCENTAGES[level]}"
                    for level in REFERRAL_BONUS_PERCENTAGES
                ])
            }
                        ELSE 0 
                    END
                ), 0) AS total_bonus
            FROM referral_levels rl
            JOIN earliest_payments ep ON rl.referred_tg_id = ep.tg_id
            WHERE rl.level <= {max_levels}
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
                WHERE rl.level < {max_levels}
            )
        """
        bonus_query = (
            bonus_cte
            + f"""
            SELECT 
                COALESCE(SUM(
                    CASE
                        {
                " ".join([
                    f"WHEN rl.level = {level} THEN {REFERRAL_BONUS_PERCENTAGES[level]} * p.amount"
                    if isinstance(REFERRAL_BONUS_PERCENTAGES[level], float)
                    else f"WHEN rl.level = {level} THEN {REFERRAL_BONUS_PERCENTAGES[level]}"
                    for level in REFERRAL_BONUS_PERCENTAGES
                ])
            }
                        ELSE 0 
                    END
                ), 0) AS total_bonus
            FROM referral_levels rl
            JOIN payments p ON rl.referred_tg_id = p.tg_id
            WHERE p.status = 'success' AND rl.level <= {max_levels}
        """
        )
    total_bonus = await conn.fetchval(bonus_query, referrer_tg_id)
    logger.debug(f"Получена общая сумма бонусов от рефералов: {total_bonus}")
    return total_bonus


async def get_referral_stats(referrer_tg_id: int):
    conn = None
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        logger.info(
            f"Установлено подключение к базе данных для получения статистики рефералов пользователя {referrer_tg_id}"
        )
        total_referrals = await get_total_referrals(conn, referrer_tg_id)
        active_referrals = await get_active_referrals(conn, referrer_tg_id)
        max_levels = len(REFERRAL_BONUS_PERCENTAGES.keys())
        referrals_by_level = await get_referrals_by_level(conn, referrer_tg_id, max_levels)
        total_referral_bonus = await get_total_referral_bonus(conn, referrer_tg_id, max_levels)

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


async def get_client_id_by_email(email: str):
    """
    Получение идентификатора клиента по электронной почте.
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


async def upsert_user(
    tg_id: int,
    username: str = None,
    first_name: str = None,
    last_name: str = None,
    language_code: str = None,
    is_bot: bool = False,
    session: Any = None,
    only_if_exists: bool = False,
) -> dict | None:
    """
    Обновляет или вставляет информацию о пользователе в базу данных.
    """
    conn = None
    close_conn = False

    try:
        if session:
            conn = session
            logger.debug(f"Используем существующую сессию для обновления пользователя {tg_id}")
        else:
            conn = await asyncpg.connect(DATABASE_URL)
            close_conn = True
            logger.info(f"Установлено новое подключение к БД для пользователя {tg_id}")

        if only_if_exists:
            logger.debug(f"[upsert_user] Режим only_if_exists: проверяю наличие пользователя {tg_id}")
            exists = await conn.fetchval("SELECT EXISTS(SELECT 1 FROM users WHERE tg_id = $1)", tg_id)
            if not exists:
                return None

            user_data = await conn.fetchrow(
                """
                UPDATE users 
                SET 
                    username = COALESCE($2, username),
                    first_name = COALESCE($3, first_name),
                    last_name = COALESCE($4, last_name),
                    language_code = COALESCE($5, language_code),
                    is_bot = $6,
                    updated_at = CURRENT_TIMESTAMP
                WHERE tg_id = $1
                RETURNING 
                    tg_id, username, first_name, last_name, language_code, 
                    is_bot, created_at, updated_at
                """,
                tg_id,
                username,
                first_name,
                last_name,
                language_code,
                is_bot,
            )
            return dict(user_data) if user_data else None

        user_data = await conn.fetchrow(
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
            RETURNING 
                tg_id, username, first_name, last_name, language_code, 
                is_bot, created_at, updated_at
            """,
            tg_id,
            username,
            first_name,
            last_name,
            language_code,
            is_bot,
        )

        logger.debug(f"Успешно обновлена информация о пользователе {tg_id}")

        return dict(user_data)
    except Exception as e:
        logger.error(f"Ошибка при обновлении информации о пользователе {tg_id}: {e}")
        raise
    finally:
        if conn and close_conn:
            await conn.close()
            logger.debug("Закрытие подключения к базе данных")


async def add_payment(tg_id: int, amount: float, payment_system: str):
    """
    Добавляет информацию о платеже в базу данных.
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


async def delete_notification(tg_id: int, notification_type: str, session):
    """
    Удаляет уведомление пользователя по типу (например: 'email_key_expired').
    """
    try:
        await session.execute(
            "DELETE FROM notifications WHERE tg_id = $1 AND notification_type = $2",
            tg_id,
            notification_type,
        )
        logger.info(f"🗑 Уведомление '{notification_type}' для пользователя {tg_id} удалено.")
    except Exception as e:
        logger.error(f"❌ Ошибка при удалении уведомления '{notification_type}' для пользователя {tg_id}: {e}")


async def check_notification_time(tg_id: int, notification_type: str, hours: int = 12, session: Any = None) -> bool:
    """
    Проверяет, прошло ли указанное количество часов с момента последнего уведомления.
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


async def get_servers(session: Any = None, include_enabled: bool = False):
    conn = None
    try:
        conn = session if session is not None else await asyncpg.connect(DATABASE_URL)

        query = """
            SELECT cluster_name, server_name, api_url, subscription_url,
                   inbound_id, panel_type, max_keys
        """
        if include_enabled:
            query += ", enabled"

        query += " FROM servers"

        result = await conn.fetch(query)

        servers = {}
        for row in result:
            cluster_name = row["cluster_name"]
            if cluster_name not in servers:
                servers[cluster_name] = []

            servers[cluster_name].append({
                "server_name": row["server_name"],
                "api_url": row["api_url"],
                "subscription_url": row["subscription_url"],
                "inbound_id": row["inbound_id"],
                "panel_type": row["panel_type"],
                "enabled": row.get("enabled", True),
                "max_keys": row.get("max_keys"),
                "cluster_name": row["cluster_name"],
            })

        return servers

    finally:
        if conn is not None and session is None:
            await conn.close()


async def delete_user_data(session: Any, tg_id: int):
    await session.execute("DELETE FROM notifications WHERE tg_id = $1", tg_id)
    await session.execute("DELETE FROM gifts WHERE sender_tg_id = $1", tg_id)
    await session.execute("UPDATE gifts SET recipient_tg_id = NULL WHERE recipient_tg_id = $1", tg_id)
    await session.execute("DELETE FROM payments WHERE tg_id = $1", tg_id)
    await session.execute("DELETE FROM referrals WHERE referrer_tg_id = $1 OR referred_tg_id = $1", tg_id)
    await session.execute("DELETE FROM coupon_usages WHERE user_id = $1", tg_id)
    await delete_key(tg_id, session)
    await session.execute("DELETE FROM temporary_data WHERE tg_id = $1", tg_id)
    await session.execute("DELETE FROM blocked_users WHERE tg_id = $1", tg_id)
    await session.execute("DELETE FROM users WHERE tg_id = $1", tg_id)


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


async def set_user_balance(tg_id: int, balance: int, session: Any) -> None:
    try:
        await session.execute(
            "UPDATE users SET balance = $1 WHERE tg_id = $2",
            balance,
            tg_id,
        )
    except Exception as e:
        logger.error(f"Ошибка при установке баланса для пользователя {tg_id}: {e}")


async def get_key_details(email, session):
    record = await session.fetchrow(
        """
        SELECT k.server_id, k.key, k.remnawave_link, k.email, k.is_frozen,
               k.expiry_time, k.client_id, k.created_at, u.tg_id, u.balance
        FROM keys k
        JOIN users u ON k.tg_id = u.tg_id
        WHERE k.email = $1
        """,
        email,
    )

    if not record:
        return None

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

    public_link = record["key"]
    remna_link = record["remnawave_link"]

    return {
        "key": public_link,
        "remnawave_link": remna_link,
        "server_id": record["server_id"],
        "created_at": record["created_at"],
        "expiry_time": record["expiry_time"],
        "client_id": record["client_id"],
        "tg_id": record["tg_id"],
        "email": record["email"],
        "is_frozen": record["is_frozen"],
        "balance": record["balance"],
        "expiry_date": expiry_date.strftime("%d %B %Y года %H:%M"),
        "days_left_message": days_left_message,
        "link": public_link or remna_link,
        "cluster_name": record["server_id"],
        "location_name": record["server_id"],
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


async def get_referral_by_referred_id(referred_tg_id: int, session: Any):
    """
    Получает информацию о реферале по ID приглашенного пользователя.
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


async def check_notifications_bulk(
    notification_type: str, hours: int, session: Any, tg_ids: list[int] = None, emails: list[str] = None
) -> list[dict]:
    """
    Проверяет, какие пользователи могут получить уведомление указанного типа, и возвращает их данные.
    """
    try:
        query = """
            SELECT 
                u.tg_id,
                k.email,
                u.username,
                u.first_name,
                u.last_name,
                EXTRACT(EPOCH FROM MAX(n.last_notification_time AT TIME ZONE 'Europe/Moscow' AT TIME ZONE 'UTC')) * 1000 AS last_notification_time
            FROM users u
            LEFT JOIN keys k ON u.tg_id = k.tg_id
            LEFT JOIN notifications n ON u.tg_id = n.tg_id AND n.notification_type = $1
            WHERE (n.last_notification_time IS NULL OR NOW() - n.last_notification_time > ($2 * INTERVAL '1 hour'))
        """
        params = [notification_type, hours]

        if tg_ids is not None:
            query += " AND u.tg_id = ANY($3)"
            params.append(tg_ids)
        if emails is not None:
            query += " AND k.email = ANY($" + str(len(params) + 1) + ")"
            params.append(emails)

        if notification_type == "inactive_trial":
            query += """
                AND u.trial IN (0, -1)
                AND u.tg_id NOT IN (SELECT tg_id FROM blocked_users)
                AND u.tg_id NOT IN (SELECT DISTINCT tg_id FROM keys)
            """

        query += """
            GROUP BY u.tg_id, k.email, u.username, u.first_name, u.last_name
        """

        users = await session.fetch(query, *params)
        logger.info(f"Найдено {len(users)} пользователей, готовых к уведомлению типа {notification_type}")
        return [
            {
                "tg_id": user["tg_id"],
                "email": user["email"],
                "username": user["username"],
                "first_name": user["first_name"],
                "last_name": user["last_name"],
                "last_notification_time": int(user["last_notification_time"])
                if user["last_notification_time"]
                else None,
            }
            for user in users
        ]
    except Exception as e:
        logger.error(f"Ошибка при массовой проверке уведомлений типа {notification_type}: {e}")
        raise


async def create_tracking_source(name: str, code: str, type_: str, created_by: int, session):
    await session.execute(
        """
        INSERT INTO tracking_sources (name, code, type, created_by)
        VALUES ($1, $2, $3, $4)
        """,
        name,
        code,
        type_,
        created_by,
    )


async def get_all_tracking_sources(session) -> list[dict]:
    records = await session.fetch("""
        SELECT
            ts.code,
            ts.name,
            ts.created_at,
            COUNT(DISTINCT u.tg_id) AS registrations,
            COUNT(DISTINCT CASE WHEN u.trial = 1 THEN u.tg_id END) AS trials,
            COUNT(DISTINCT CASE WHEN p.status = 'success' THEN p.tg_id END) AS payments
        FROM tracking_sources ts
        LEFT JOIN users u ON u.source_code = ts.code
        LEFT JOIN payments p ON p.tg_id = u.tg_id
        GROUP BY ts.code, ts.name, ts.created_at
        ORDER BY ts.created_at DESC
    """)
    return [dict(r) for r in records]


async def get_tracking_source_stats(code: str, session) -> dict:
    result = await session.fetchrow(
        """
        SELECT
            ts.name,
            ts.code,
            ts.created_at,
            COUNT(DISTINCT u.tg_id) AS registrations,
            COUNT(DISTINCT CASE WHEN u.trial = 1 THEN u.tg_id END) AS trials,
            COUNT(DISTINCT CASE
                WHEN p.status = 'success' THEN u.tg_id
            END) AS payments
        FROM tracking_sources ts
        LEFT JOIN users u ON u.source_code = ts.code
        LEFT JOIN payments p ON p.tg_id = u.tg_id
        WHERE ts.code = $1
        GROUP BY ts.code, ts.name, ts.created_at
        """,
        code,
    )
    return dict(result) if result else {}

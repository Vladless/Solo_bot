from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from logger import logger


def _mig_out(msg: str, color: str | None = None) -> None:
    """Вывод миграций схемы: успешное завершение подсвечиваем зелёным."""
    if color:
        try:
            from rich.console import Console

            Console().print(msg, style=color, markup=False, highlight=False)
            return
        except Exception:
            pass
    print(msg, flush=True)


from settings.config import DATABASE_URL


def _is_postgresql() -> bool:
    u = (DATABASE_URL or "").lower()
    return "+asyncpg" in u or u.startswith("postgresql")


async def _table_exists(conn: AsyncConnection, table: str) -> bool:
    r = await conn.execute(
        text(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = :t
            """
        ),
        {"t": table},
    )
    return r.first() is not None


async def _ensure_migrations_table(conn: AsyncConnection) -> None:
    if not await _table_exists(conn, "schema_migrations"):
        await conn.execute(
            text(
                """
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    description TEXT
                )
                """
            )
        )


async def _get_current_version(conn: AsyncConnection) -> int:
    await _ensure_migrations_table(conn)
    r = await conn.execute(text("SELECT COALESCE(MAX(version), 0) FROM schema_migrations"))
    row = r.first()
    return int(row[0]) if row else 0


async def _mark_migration_applied(conn: AsyncConnection, version: int, description: str) -> None:
    await conn.execute(
        text(
            """
            INSERT INTO schema_migrations (version, description)
            VALUES (:v, :d)
            ON CONFLICT (version) DO NOTHING
            """
        ),
        {"v": version, "d": description},
    )


async def _users_pk_columns(conn: AsyncConnection) -> list[str]:
    r = await conn.execute(
        text(
            """
            SELECT a.attname
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            JOIN unnest(c.conkey) WITH ORDINALITY AS u(attnum, ord) ON true
            JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = u.attnum
            WHERE n.nspname = 'public'
              AND t.relname = 'users'
              AND c.contype = 'p'
            ORDER BY u.ord
            """
        )
    )
    return [row[0] for row in r.all()]


async def _column_exists(conn: AsyncConnection, table: str, column: str) -> bool:
    r = await conn.execute(
        text(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :t AND column_name = :c
            """
        ),
        {"t": table, "c": column},
    )
    return r.first() is not None


async def _column_is_identity(conn: AsyncConnection, table: str, column: str) -> bool:
    r = await conn.execute(
        text(
            """
            SELECT is_identity
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :t AND column_name = :c
            """
        ),
        {"t": table, "c": column},
    )
    row = r.first()
    return bool(row and str(row[0]).upper() == "YES")


async def _constraint_exists(conn: AsyncConnection, table: str, constraint: str) -> bool:
    r = await conn.execute(
        text(
            """
            SELECT 1
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = 'public'
              AND t.relname = :t
              AND c.conname = :c
            """
        ),
        {"t": table, "c": constraint},
    )
    return r.first() is not None


async def _exec_ignore(conn: AsyncConnection, sql: str) -> None:
    try:
        async with conn.begin_nested():
            await conn.execute(text(sql))
    except Exception as e:
        logger.debug(f"[schema_upgrade] skip: {e}")


async def _add_constraint_if_missing(conn: AsyncConnection, table: str, name: str, sql: str) -> None:
    if await _constraint_exists(conn, table, name):
        return
    await _exec_ignore(conn, sql)


async def _drop_fkeys_to_users(conn: AsyncConnection) -> None:
    if not await _table_exists(conn, "users"):
        return
    r = await conn.execute(
        text(
            """
            SELECT con.conname, rel.relname AS src_table
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
            WHERE con.confrelid = 'users'::regclass
              AND con.contype = 'f'
              AND nsp.nspname = 'public'
            """
        )
    )
    for row in r.all():
        cname, src = row[0], row[1]
        await conn.execute(text(f'ALTER TABLE "{src}" DROP CONSTRAINT IF EXISTS "{cname}"'))


async def _drop_pk(conn: AsyncConnection, table: str) -> None:
    if not await _table_exists(conn, table):
        return
    r = await conn.execute(
        text(
            """
            SELECT tc.constraint_name
            FROM information_schema.table_constraints tc
            WHERE tc.table_schema = 'public'
              AND tc.table_name = :t
              AND tc.constraint_type = 'PRIMARY KEY'
            """
        ),
        {"t": table},
    )
    row = r.first()
    if row:
        await conn.execute(text(f'ALTER TABLE "{table}" DROP CONSTRAINT IF EXISTS "{row[0]}"'))


async def _column_has_nulls(conn: AsyncConnection, table: str, column: str) -> bool:
    r = await conn.execute(text(f'SELECT 1 FROM "{table}" WHERE "{column}" IS NULL LIMIT 1'))
    return r.first() is not None


async def _safe_set_not_null(conn: AsyncConnection, table: str, column: str) -> bool:
    if await _column_has_nulls(conn, table, column):
        logger.warning(f"[schema_upgrade] {table}.{column} содержит NULL, пропуск SET NOT NULL")
        return False
    await conn.execute(text(f'ALTER TABLE "{table}" ALTER COLUMN "{column}" SET NOT NULL'))
    return True


async def _index_exists(conn: AsyncConnection, table: str, index: str) -> bool:
    r = await conn.execute(
        text(
            """
            SELECT 1
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename = :t
              AND indexname = :i
            """
        ),
        {"t": table, "i": index},
    )
    return r.first() is not None


async def _ensure_users_id_referenceable(conn: AsyncConnection) -> None:
    if not await _column_exists(conn, "users", "id"):
        return
    if await _column_is_identity(conn, "users", "id"):
        await _exec_ignore(conn, "UPDATE users SET id = DEFAULT WHERE id IS NULL")
        await _exec_ignore(
            conn,
            """
            WITH d AS (
                SELECT ctid, row_number() OVER (PARTITION BY id ORDER BY ctid) AS rn
                FROM users
                WHERE id IS NOT NULL
            )
            UPDATE users u
            SET id = DEFAULT
            FROM d
            WHERE u.ctid = d.ctid AND d.rn > 1
            """,
        )
    else:
        await _exec_ignore(conn, "CREATE SEQUENCE IF NOT EXISTS users_id_seq")
        await _exec_ignore(conn, "ALTER TABLE users ALTER COLUMN id SET DEFAULT nextval('users_id_seq')")
        await _exec_ignore(conn, "ALTER SEQUENCE users_id_seq OWNED BY users.id")
        await _exec_ignore(conn, "UPDATE users SET id = nextval('users_id_seq') WHERE id IS NULL")
        await _exec_ignore(
            conn,
            """
            WITH d AS (
                SELECT ctid, row_number() OVER (PARTITION BY id ORDER BY ctid) AS rn
                FROM users
                WHERE id IS NOT NULL
            )
            UPDATE users u
            SET id = nextval('users_id_seq')
            FROM d
            WHERE u.ctid = d.ctid AND d.rn > 1
            """,
        )
    await _exec_ignore(conn, "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_id ON users (id)")


async def _migration_v1_add_users_id(conn: AsyncConnection) -> None:
    if not await _table_exists(conn, "users"):
        return

    pk = await _users_pk_columns(conn)
    if not pk:
        return
    if pk == ["id"]:
        return

    if pk != ["tg_id"]:
        logger.warning(f"[schema_upgrade] users PK неожиданен {pk}, пропуск v1")
        return

    _mig_out("[schema_upgrade] v1: Добавление users.id")

    if not await _column_exists(conn, "users", "id"):
        await conn.execute(
            text(
                """
                ALTER TABLE users
                ADD COLUMN id BIGINT GENERATED BY DEFAULT AS IDENTITY NOT NULL
                """
            )
        )
    await _ensure_users_id_referenceable(conn)


async def _migration_v2_add_user_id_columns(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v2: Добавление user_id колонок в связанные таблицы")

    tables_columns = [
        ("keys", "user_id"),
        ("payments", "user_id"),
        ("referrals", "referred_user_id"),
        ("referrals", "referrer_user_id"),
        ("notifications", "user_id"),
        ("scheduled_broadcasts", "created_by_user_id"),
        ("gifts", "sender_user_id"),
        ("gifts", "recipient_user_id"),
        ("gift_usages", "user_id"),
        ("coupon_usages", "account_user_id"),
        ("temporary_data", "user_id"),
        ("manual_bans", "user_id"),
        ("blocked_users", "user_id"),
    ]

    for table, column in tables_columns:
        if await _table_exists(conn, table) and not await _column_exists(conn, table, column):
            await conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN {column} BIGINT'))


async def _backfill_users_from_table(conn: AsyncConnection, table: str, tg_col: str = "tg_id") -> int:
    """Auto-создание users для orphan tg_id'ов из указанной таблицы.

    Legacy клиенты обновляются с TG-only схемы (где только tg_id), и в связанных
    таблицах могут быть строки, ссылающиеся на tg_id, которого нет в users. Вместо
    удаления таких строк — создаём минимальную users-запись, чтобы FK/NOT NULL
    проходили и данные сохранялись.
    """
    if not await _table_exists(conn, table):
        return 0
    if not await _column_exists(conn, table, tg_col):
        return 0
    if not await _table_exists(conn, "users"):
        return 0
    if not await _column_exists(conn, "users", "tg_id"):
        return 0

    has_created_at = await _column_exists(conn, "users", "created_at")
    has_updated_at = await _column_exists(conn, "users", "updated_at")

    cols = ["tg_id"]
    vals = [f't."{tg_col}"']
    if has_created_at:
        cols.append("created_at")
        vals.append("NOW()")
    if has_updated_at:
        cols.append("updated_at")
        vals.append("NOW()")

    cols_sql = ", ".join(cols)
    vals_sql = ", ".join(vals)

    result = await conn.execute(
        text(
            f"""
            INSERT INTO users ({cols_sql})
            SELECT DISTINCT {vals_sql}
            FROM "{table}" t
            WHERE t."{tg_col}" IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM users u WHERE u.tg_id = t."{tg_col}"
              )
            """
        )
    )
    created = result.rowcount or 0
    if created > 0:
        _mig_out(f"[schema_upgrade] users backfill: создано {created} юзеров из orphan {table}.{tg_col}")
    return created


async def _migration_v3_populate_user_ids(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v3: Заполнение user_id из tg_id")

    if not await _table_exists(conn, "users") or not await _column_exists(conn, "users", "id"):
        return

    updates = [
        ("keys", "user_id", "tg_id"),
        ("payments", "user_id", "tg_id"),
        ("notifications", "user_id", "tg_id"),
        ("scheduled_broadcasts", "created_by_user_id", "created_by_tg_id"),
        ("gift_usages", "user_id", "tg_id"),
        ("temporary_data", "user_id", "tg_id"),
        ("manual_bans", "user_id", "tg_id"),
        ("blocked_users", "user_id", "tg_id"),
    ]

    for table, _user_col, tg_col in updates:
        await _backfill_users_from_table(conn, table, tg_col)

    for table, user_col, tg_col in updates:
        if await _table_exists(conn, table) and await _column_exists(conn, table, user_col):
            result = await conn.execute(
                text(
                    f"""
                    UPDATE "{table}" t
                    SET {user_col} = u.id
                    FROM users u
                    WHERE t.{user_col} IS NULL AND t.{tg_col} = u.tg_id
                    """
                )
            )
            updated = result.rowcount
            if updated > 0:
                logger.debug(f"[schema_upgrade] v3: заполнено {updated} записей {user_col} в {table}")

            null_count = await conn.execute(text(f'SELECT COUNT(*) FROM "{table}" WHERE {user_col} IS NULL'))
            nulls = null_count.scalar()
            if nulls > 0:
                logger.warning(f"[schema_upgrade] v3: в {table} осталось {nulls} записей с NULL {user_col}")

    if await _table_exists(conn, "referrals"):
        await _backfill_users_from_table(conn, "referrals", "referred_tg_id")
        await _backfill_users_from_table(conn, "referrals", "referrer_tg_id")
        if await _column_exists(conn, "referrals", "referred_user_id"):
            await conn.execute(
                text(
                    """
                    UPDATE referrals r
                    SET referred_user_id = u.id
                    FROM users u
                    WHERE r.referred_user_id IS NULL AND r.referred_tg_id = u.tg_id
                    """
                )
            )
        if await _column_exists(conn, "referrals", "referrer_user_id"):
            await conn.execute(
                text(
                    """
                    UPDATE referrals r
                    SET referrer_user_id = u.id
                    FROM users u
                    WHERE r.referrer_user_id IS NULL AND r.referrer_tg_id = u.tg_id
                    """
                )
            )

    if await _table_exists(conn, "gifts"):
        await _backfill_users_from_table(conn, "gifts", "sender_tg_id")
        await _backfill_users_from_table(conn, "gifts", "recipient_tg_id")
        if await _column_exists(conn, "gifts", "sender_user_id"):
            await conn.execute(
                text(
                    """
                    UPDATE gifts g
                    SET sender_user_id = u.id
                    FROM users u
                    WHERE g.sender_user_id IS NULL AND g.sender_tg_id = u.tg_id
                    """
                )
            )
        if await _column_exists(conn, "gifts", "recipient_user_id"):
            await conn.execute(
                text(
                    """
                    UPDATE gifts g
                    SET recipient_user_id = u.id
                    FROM users u
                    WHERE g.recipient_user_id IS NULL AND g.recipient_tg_id IS NOT NULL
                      AND g.recipient_tg_id = u.tg_id
                    """
                )
            )

    if await _table_exists(conn, "coupon_usages") and await _column_exists(conn, "coupon_usages", "account_user_id"):
        await _backfill_users_from_table(conn, "coupon_usages", "user_id")
        await conn.execute(
            text(
                """
                UPDATE coupon_usages c
                SET account_user_id = u.id
                FROM users u
                WHERE c.account_user_id IS NULL AND c.user_id = u.tg_id
                """
            )
        )


async def _migration_v4_add_tg_id_mirrors(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v4: Добавление tg_id mirror колонок")

    mirrors = [
        ("referrals", "referred_tg_id"),
        ("referrals", "referrer_tg_id"),
        ("notifications", "tg_id"),
        ("gift_usages", "tg_id"),
        ("keys", "tg_id"),
        ("payments", "tg_id"),
        ("gifts", "sender_tg_id"),
        ("gifts", "recipient_tg_id"),
        ("scheduled_broadcasts", "created_by_tg_id"),
        ("coupon_usages", "tg_id"),
        ("temporary_data", "tg_id"),
        ("manual_bans", "tg_id"),
        ("blocked_users", "tg_id"),
    ]

    for table, column in mirrors:
        if await _table_exists(conn, table) and not await _column_exists(conn, table, column):
            await conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN {column} BIGINT'))


async def _migration_v5_switch_pks_to_user_id(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v5: Переключение PK на user_id где возможно")

    await _drop_fkeys_to_users(conn)
    await _ensure_users_id_referenceable(conn)

    if await _table_exists(conn, "referrals"):
        can_harden = not await _column_has_nulls(conn, "referrals", "referred_user_id")
        can_harden = can_harden and not await _column_has_nulls(conn, "referrals", "referrer_user_id")
        if can_harden:
            await _drop_pk(conn, "referrals")
            await conn.execute(text("ALTER TABLE referrals ALTER COLUMN referred_user_id SET NOT NULL"))
            await conn.execute(text("ALTER TABLE referrals ALTER COLUMN referrer_user_id SET NOT NULL"))
            await conn.execute(text("ALTER TABLE referrals ADD PRIMARY KEY (referred_user_id, referrer_user_id)"))
        else:
            logger.warning("[schema_upgrade] referrals содержит NULL user_id, пропуск перевода PK")

    if await _table_exists(conn, "notifications") and await _safe_set_not_null(conn, "notifications", "user_id"):
        await _drop_pk(conn, "notifications")
        await conn.execute(text("ALTER TABLE notifications ADD PRIMARY KEY (user_id, notification_type)"))

    if await _table_exists(conn, "gift_usages") and await _safe_set_not_null(conn, "gift_usages", "user_id"):
        await _drop_pk(conn, "gift_usages")
        await conn.execute(text("ALTER TABLE gift_usages ADD PRIMARY KEY (gift_id, user_id)"))

    if await _table_exists(conn, "coupon_usages"):
        await _drop_pk(conn, "coupon_usages")
        has_user_id = await _column_exists(conn, "coupon_usages", "user_id")
        has_account_user_id = await _column_exists(conn, "coupon_usages", "account_user_id")
        if has_account_user_id and not has_user_id:
            await conn.execute(text("ALTER TABLE coupon_usages RENAME COLUMN account_user_id TO user_id"))
        elif has_account_user_id and has_user_id:
            await conn.execute(
                text(
                    """
                    UPDATE coupon_usages
                    SET user_id = account_user_id
                    WHERE account_user_id IS NOT NULL
                    """
                )
            )
            await conn.execute(text("ALTER TABLE coupon_usages DROP COLUMN account_user_id"))
        elif not has_user_id:
            await conn.execute(text("ALTER TABLE coupon_usages ADD COLUMN user_id BIGINT"))
        if await _safe_set_not_null(conn, "coupon_usages", "user_id"):
            await conn.execute(text("ALTER TABLE coupon_usages ADD PRIMARY KEY (coupon_id, user_id)"))

    for tbl in ("temporary_data", "manual_bans", "blocked_users"):
        if not await _table_exists(conn, tbl):
            continue
        if not await _column_exists(conn, tbl, "user_id"):
            continue
        _mig_out(f"[schema_upgrade] {tbl} оставлен на legacy PK по tg_id")

    if await _table_exists(conn, "users"):
        await _drop_pk(conn, "users")
        await conn.execute(text("ALTER TABLE users ADD PRIMARY KEY (id)"))
        await conn.execute(text("ALTER TABLE users ALTER COLUMN tg_id DROP NOT NULL"))
        await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_users_tg_id ON users (tg_id)"))


async def _migration_v6_add_foreign_keys(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v6: Добавление foreign key constraints")

    if await _table_exists(conn, "referrals"):
        await _add_constraint_if_missing(
            conn,
            "referrals",
            "fk_referrals_referred_user",
            """
            ALTER TABLE referrals
            ADD CONSTRAINT fk_referrals_referred_user
            FOREIGN KEY (referred_user_id) REFERENCES users (id) ON DELETE CASCADE
            """,
        )
        await _add_constraint_if_missing(
            conn,
            "referrals",
            "fk_referrals_referrer_user",
            """
            ALTER TABLE referrals
            ADD CONSTRAINT fk_referrals_referrer_user
            FOREIGN KEY (referrer_user_id) REFERENCES users (id) ON DELETE CASCADE
            """,
        )

    if await _table_exists(conn, "notifications"):
        await _add_constraint_if_missing(
            conn,
            "notifications",
            "fk_notifications_user",
            """
            ALTER TABLE notifications
            ADD CONSTRAINT fk_notifications_user
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            """,
        )

    if await _table_exists(conn, "gift_usages"):
        await _add_constraint_if_missing(
            conn,
            "gift_usages",
            "fk_gift_usages_user",
            """
            ALTER TABLE gift_usages
            ADD CONSTRAINT fk_gift_usages_user
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            """,
        )

    if await _table_exists(conn, "coupon_usages"):
        await _add_constraint_if_missing(
            conn,
            "coupon_usages",
            "fk_coupon_usages_user",
            """
            ALTER TABLE coupon_usages
            ADD CONSTRAINT fk_coupon_usages_user
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            """,
        )

    for tbl in ("temporary_data", "manual_bans", "blocked_users"):
        if await _table_exists(conn, tbl):
            safe = re.sub(r"[^a-z_]", "_", tbl)
            await _add_constraint_if_missing(
                conn,
                tbl,
                f"fk_{safe}_user",
                f"""
                ALTER TABLE "{tbl}"
                ADD CONSTRAINT fk_{safe}_user
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
                """,
            )

    if await _table_exists(conn, "keys") and await _safe_set_not_null(conn, "keys", "user_id"):
        await _add_constraint_if_missing(
            conn,
            "keys",
            "fk_keys_user",
            """
            ALTER TABLE keys
            ADD CONSTRAINT fk_keys_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            """,
        )

    if await _table_exists(conn, "payments") and await _safe_set_not_null(conn, "payments", "user_id"):
        await _add_constraint_if_missing(
            conn,
            "payments",
            "fk_payments_user",
            """
            ALTER TABLE payments
            ADD CONSTRAINT fk_payments_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            """,
        )

    if await _table_exists(conn, "gifts"):
        if await _safe_set_not_null(conn, "gifts", "sender_user_id"):
            await _add_constraint_if_missing(
                conn,
                "gifts",
                "fk_gifts_sender_user",
                """
                ALTER TABLE gifts
                ADD CONSTRAINT fk_gifts_sender_user FOREIGN KEY (sender_user_id) REFERENCES users (id) ON DELETE CASCADE
                """,
            )
        await _add_constraint_if_missing(
            conn,
            "gifts",
            "fk_gifts_recipient_user",
            """
            ALTER TABLE gifts
            ADD CONSTRAINT fk_gifts_recipient_user FOREIGN KEY (recipient_user_id) REFERENCES users (id) ON DELETE SET NULL
            """,
        )

    if await _table_exists(conn, "scheduled_broadcasts"):
        await _add_constraint_if_missing(
            conn,
            "scheduled_broadcasts",
            "fk_scheduled_broadcasts_creator_user",
            """
            ALTER TABLE scheduled_broadcasts
            ADD CONSTRAINT fk_scheduled_broadcasts_creator_user
            FOREIGN KEY (created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
            """,
        )


async def _run_tg_mirror_backfill(conn: AsyncConnection, *, nulls_only: bool) -> None:
    null_filter = " AND k.tg_id IS NULL" if nulls_only else ""
    backfills = [
        (
            "keys",
            f"UPDATE keys k SET tg_id = u.tg_id FROM users u WHERE k.user_id = u.id{null_filter}",
        ),
        (
            "payments",
            f"UPDATE payments p SET tg_id = u.tg_id FROM users u "
            f"WHERE p.user_id = u.id{' AND p.tg_id IS NULL' if nulls_only else ''}",
        ),
        (
            "referrals",
            "UPDATE referrals r SET referred_tg_id = ur.tg_id, referrer_tg_id = ux.tg_id "
            "FROM users ur, users ux "
            "WHERE r.referred_user_id = ur.id AND r.referrer_user_id = ux.id"
            + (" AND (r.referred_tg_id IS NULL OR r.referrer_tg_id IS NULL)" if nulls_only else ""),
        ),
        (
            "notifications",
            f"UPDATE notifications n SET tg_id = u.tg_id FROM users u "
            f"WHERE n.user_id = u.id{' AND n.tg_id IS NULL' if nulls_only else ''}",
        ),
        (
            "gift_usages",
            f"UPDATE gift_usages gu SET tg_id = u.tg_id FROM users u "
            f"WHERE gu.user_id = u.id{' AND gu.tg_id IS NULL' if nulls_only else ''}",
        ),
        (
            "manual_bans",
            f"UPDATE manual_bans m SET tg_id = u.tg_id FROM users u "
            f"WHERE m.user_id = u.id{' AND m.tg_id IS NULL' if nulls_only else ''}",
        ),
        (
            "temporary_data",
            f"UPDATE temporary_data t SET tg_id = u.tg_id FROM users u "
            f"WHERE t.user_id = u.id{' AND t.tg_id IS NULL' if nulls_only else ''}",
        ),
        (
            "blocked_users",
            f"UPDATE blocked_users b SET tg_id = u.tg_id FROM users u "
            f"WHERE b.user_id = u.id{' AND b.tg_id IS NULL' if nulls_only else ''}",
        ),
        (
            "scheduled_broadcasts",
            "UPDATE scheduled_broadcasts s SET created_by_tg_id = u.tg_id FROM users u "
            "WHERE s.created_by_user_id = u.id" + (" AND s.created_by_tg_id IS NULL" if nulls_only else ""),
        ),
        (
            "coupon_usages",
            f"UPDATE coupon_usages c SET tg_id = u.tg_id FROM users u "
            f"WHERE c.user_id = u.id{' AND c.tg_id IS NULL' if nulls_only else ''}",
        ),
    ]

    for table, sql in backfills:
        if await _table_exists(conn, table):
            await conn.execute(text(sql))

    if await _table_exists(conn, "gifts"):
        sender_null = " AND g.sender_tg_id IS NULL" if nulls_only else ""
        recipient_null = " AND g.recipient_tg_id IS NULL" if nulls_only else ""
        await conn.execute(
            text(
                f"""
                UPDATE gifts g
                SET sender_tg_id = u.tg_id
                FROM users u
                WHERE g.sender_user_id = u.id{sender_null}
                """
            )
        )
        await conn.execute(
            text(
                f"""
                UPDATE gifts g
                SET recipient_tg_id = u.tg_id
                FROM users u
                WHERE g.recipient_user_id = u.id{recipient_null}
                """
            )
        )


async def _migration_v7_backfill_tg_mirrors(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v7: Backfill tg_id mirrors")
    await _run_tg_mirror_backfill(conn, nulls_only=False)


async def _migration_v8_fix_notification_timezone(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v8: Исправление timezone для last_notification_time")

    if not await _table_exists(conn, "notifications"):
        return

    r = await conn.execute(
        text(
            """
            SELECT data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'notifications'
              AND column_name = 'last_notification_time'
            """
        )
    )
    row = r.first()
    if not row:
        return

    current_type = row[0]
    if current_type == "timestamp with time zone":
        return

    try:
        await conn.execute(
            text(
                """
                ALTER TABLE notifications
                ALTER COLUMN last_notification_time
                TYPE TIMESTAMP WITH TIME ZONE
                USING last_notification_time AT TIME ZONE 'UTC'
                """
            )
        )
    except Exception as e:
        logger.warning(f"[schema_upgrade] v8: не удалось изменить тип колонки: {e}")


async def _migration_v9_cleanup_orphaned_records(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v5: Мягкий backfill user_id для legacy таблиц")

    tables_to_clean = [
        ("blocked_users", "user_id"),
        ("manual_bans", "user_id"),
        ("temporary_data", "user_id"),
    ]

    for table, user_col in tables_to_clean:
        if not await _table_exists(conn, table):
            continue

        if not await _column_exists(conn, table, user_col):
            continue

        result = await conn.execute(
            text(
                f"""
                UPDATE "{table}" AS t
                SET "{user_col}" = u.id
                FROM users AS u
                WHERE t."{user_col}" IS NULL
                  AND t.tg_id IS NOT NULL
                  AND t.tg_id = u.tg_id
                """
            )
        )
        updated = result.rowcount
        if updated > 0:
            _mig_out(f"[schema_upgrade] v5: заполнено {updated} записей в {table}")


async def _migration_v10_finalize_user_id_not_null(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v10: legacy таблицы сохраняют nullable user_id и PK по tg_id")


async def _migration_v11_finalize_legacy_tables(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v11: финализация legacy таблиц на user_id")

    for table in ("blocked_users", "manual_bans", "temporary_data"):
        if not await _table_exists(conn, table):
            continue
        if not await _column_exists(conn, table, "user_id"):
            continue

        await _backfill_users_from_table(conn, table, "tg_id")

        await conn.execute(
            text(
                f"""
                UPDATE "{table}" AS t
                SET "user_id" = u.id
                FROM users AS u
                WHERE t."user_id" IS NULL
                  AND t."tg_id" IS NOT NULL
                  AND t."tg_id" = u.tg_id
                """
            )
        )

        deleted = await conn.execute(text(f'DELETE FROM "{table}" WHERE "user_id" IS NULL'))
        if deleted.rowcount > 0:
            logger.warning(
                f"[schema_upgrade] v11: удалено {deleted.rowcount} записей из {table} "
                "без tg_id и user_id (невосстановимы)"
            )

        await _drop_pk(conn, table)
        if await _column_exists(conn, table, "tg_id"):
            await _exec_ignore(conn, f'ALTER TABLE "{table}" ALTER COLUMN "tg_id" DROP NOT NULL')
        if not await _safe_set_not_null(conn, table, "user_id"):
            continue
        await _exec_ignore(conn, f'ALTER TABLE "{table}" ADD PRIMARY KEY ("user_id")')
        await _exec_ignore(conn, f'DROP INDEX IF EXISTS "ix_{table}_user_id"')

        tg_index_name = f"ix_{table}_tg_id"
        if await _column_exists(conn, table, "tg_id") and not await _index_exists(conn, table, tg_index_name):
            await conn.execute(text(f'CREATE INDEX "{tg_index_name}" ON "{table}" ("tg_id")'))


async def _migration_v12_relax_legacy_tg_id_nullability(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v12: приведение tg_id к nullable в legacy таблицах")

    for table in ("blocked_users", "manual_bans", "temporary_data"):
        if not await _table_exists(conn, table):
            continue
        if not await _column_exists(conn, table, "tg_id"):
            continue
        await _exec_ignore(conn, f'ALTER TABLE "{table}" ALTER COLUMN "tg_id" DROP NOT NULL')


async def _migration_v13_add_web_page_variants(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v13: добавление таблиц вариантов web-страниц")

    await _exec_ignore(
        conn,
        """
        CREATE TABLE IF NOT EXISTS web_page_variants (
            id VARCHAR(36) PRIMARY KEY,
            page_slug VARCHAR(64) NOT NULL REFERENCES web_pages(slug) ON DELETE CASCADE,
            variant_key VARCHAR(64) NOT NULL,
            name VARCHAR(255) NOT NULL DEFAULT 'Default',
            is_active BOOLEAN NOT NULL DEFAULT FALSE,
            theme_tokens JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
    )
    await _exec_ignore(
        conn,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_web_page_variants_page_slug_variant_key
        ON web_page_variants (page_slug, variant_key)
        """,
    )
    await _exec_ignore(
        conn,
        """
        CREATE INDEX IF NOT EXISTS ix_web_page_variants_page_slug_is_active
        ON web_page_variants (page_slug, is_active)
        """,
    )
    await _exec_ignore(
        conn,
        """
        CREATE TABLE IF NOT EXISTS web_page_variant_blocks (
            id VARCHAR(36) PRIMARY KEY,
            variant_id VARCHAR(36) NOT NULL REFERENCES web_page_variants(id) ON DELETE CASCADE,
            "order" INTEGER NOT NULL DEFAULT 0,
            type VARCHAR(64) NOT NULL,
            data JSONB NOT NULL DEFAULT '{}'::jsonb
        )
        """,
    )
    await _exec_ignore(
        conn,
        """
        CREATE INDEX IF NOT EXISTS ix_web_page_variant_blocks_variant_id_order
        ON web_page_variant_blocks (variant_id, "order")
        """,
    )


async def _migration_v14_web_flow_graph_model(conn: AsyncConnection) -> None:
    """Переход web_flows со steps[] на nodes[] + edges[] (граф-модель)."""
    if not await _table_exists(conn, "web_flows"):
        return

    if await _column_exists(conn, "web_flows", "steps"):
        if not await _column_exists(conn, "web_flows", "nodes"):
            await _exec_ignore(conn, "ALTER TABLE web_flows RENAME COLUMN steps TO nodes")
        else:
            await _exec_ignore(conn, "ALTER TABLE web_flows DROP COLUMN steps")

    if not await _column_exists(conn, "web_flows", "edges"):
        await _exec_ignore(conn, "ALTER TABLE web_flows ADD COLUMN edges JSONB NOT NULL DEFAULT '[]'::jsonb")

    if not await _column_exists(conn, "web_flows", "entry_node_id"):
        await _exec_ignore(conn, "ALTER TABLE web_flows ADD COLUMN entry_node_id VARCHAR(64)")

    rows = (await conn.execute(text("SELECT id, nodes FROM web_flows"))).all()
    for row in rows:
        flow_id = row[0]
        raw_nodes = row[1]
        if not isinstance(raw_nodes, list) or len(raw_nodes) == 0:
            continue
        first = raw_nodes[0] if raw_nodes else {}
        if isinstance(first, dict) and "position" not in first:
            new_nodes = []
            new_edges = []
            entry_id = None
            for i, step in enumerate(raw_nodes):
                if not isinstance(step, dict):
                    continue
                node_id = step.get("id", f"node-{i}")
                if i == 0:
                    entry_id = node_id
                new_nodes.append({
                    **step,
                    "position": {"x": 300, "y": i * 180},
                })
                if i > 0:
                    prev_id = raw_nodes[i - 1].get("id", f"node-{i - 1}")
                    new_edges.append({
                        "id": f"edge-migrated-{i}",
                        "source": prev_id,
                        "target": node_id,
                    })
            import json

            await conn.execute(
                text("UPDATE web_flows SET nodes = :nodes, edges = :edges, entry_node_id = :entry WHERE id = :fid"),
                {"nodes": json.dumps(new_nodes), "edges": json.dumps(new_edges), "entry": entry_id, "fid": flow_id},
            )


async def _migration_v15_recover_orphan_users(conn: AsyncConnection) -> None:
    """Safety net для клиентов, прошедших v3/v11 со старой логикой.

    Обходит все таблицы, где может быть orphan tg_id, создаёт недостающих юзеров
    и повторно заполняет user_id. Идемпотентно: если orphan'ов нет — no-op.
    """
    _mig_out("[schema_upgrade] v15: Восстановление orphan tg_ids в users")

    if not await _table_exists(conn, "users") or not await _column_exists(conn, "users", "id"):
        return

    orphan_sources = [
        ("keys", "tg_id"),
        ("payments", "tg_id"),
        ("notifications", "tg_id"),
        ("scheduled_broadcasts", "created_by_tg_id"),
        ("gift_usages", "tg_id"),
        ("temporary_data", "tg_id"),
        ("manual_bans", "tg_id"),
        ("blocked_users", "tg_id"),
        ("referrals", "referred_tg_id"),
        ("referrals", "referrer_tg_id"),
        ("gifts", "sender_tg_id"),
        ("gifts", "recipient_tg_id"),
    ]

    total_created = 0
    for table, tg_col in orphan_sources:
        total_created += await _backfill_users_from_table(conn, table, tg_col)

    if total_created > 0:
        _mig_out(f"[schema_upgrade] v15: всего создано {total_created} orphan-юзеров")

    repopulate = [
        ("keys", "user_id", "tg_id"),
        ("payments", "user_id", "tg_id"),
        ("notifications", "user_id", "tg_id"),
        ("scheduled_broadcasts", "created_by_user_id", "created_by_tg_id"),
        ("gift_usages", "user_id", "tg_id"),
        ("temporary_data", "user_id", "tg_id"),
        ("manual_bans", "user_id", "tg_id"),
        ("blocked_users", "user_id", "tg_id"),
        ("referrals", "referred_user_id", "referred_tg_id"),
        ("referrals", "referrer_user_id", "referrer_tg_id"),
        ("gifts", "sender_user_id", "sender_tg_id"),
        ("gifts", "recipient_user_id", "recipient_tg_id"),
    ]

    for table, user_col, tg_col in repopulate:
        if not await _table_exists(conn, table):
            continue
        if not await _column_exists(conn, table, user_col):
            continue
        if not await _column_exists(conn, table, tg_col):
            continue
        result = await conn.execute(
            text(
                f"""
                UPDATE "{table}" t
                SET "{user_col}" = u.id
                FROM users u
                WHERE t."{user_col}" IS NULL
                  AND t."{tg_col}" IS NOT NULL
                  AND t."{tg_col}" = u.tg_id
                """
            )
        )
        if result.rowcount and result.rowcount > 0:
            _mig_out(f"[schema_upgrade] v15: повторно заполнено {result.rowcount} записей {table}.{user_col}")


async def _migration_v18_web_error_reports(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v18: таблица web_error_reports")
    await _exec_ignore(
        conn,
        """
        CREATE TABLE IF NOT EXISTS web_error_reports (
            id VARCHAR(36) PRIMARY KEY,
            signature VARCHAR(128) NOT NULL UNIQUE,
            error_name VARCHAR(255) NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            stack TEXT,
            url TEXT,
            user_agent TEXT,
            tag VARCHAR(64),
            last_identity_id VARCHAR(36),
            last_context JSONB,
            count INTEGER NOT NULL DEFAULT 1,
            resolved BOOLEAN NOT NULL DEFAULT FALSE,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
    )
    await _exec_ignore(
        conn,
        "CREATE INDEX IF NOT EXISTS ix_web_error_reports_signature ON web_error_reports (signature)",
    )
    await _exec_ignore(
        conn,
        "CREATE INDEX IF NOT EXISTS ix_web_error_reports_resolved_last ON web_error_reports (resolved, last_seen_at)",
    )


async def _migration_v16b_web_flow_events(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v17: таблица web_flow_events")

    await _exec_ignore(
        conn,
        """
        CREATE TABLE IF NOT EXISTS web_flow_events (
            id VARCHAR(36) PRIMARY KEY,
            flow_id VARCHAR(64) NOT NULL,
            node_id VARCHAR(64) NOT NULL,
            node_type VARCHAR(32) NOT NULL DEFAULT '',
            event_type VARCHAR(32) NOT NULL,
            ab_variant VARCHAR(16),
            device VARCHAR(16),
            locale VARCHAR(8),
            authenticated BOOLEAN,
            metadata JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
    )
    await _exec_ignore(
        conn,
        "CREATE INDEX IF NOT EXISTS ix_web_flow_events_flow_node ON web_flow_events (flow_id, node_id)",
    )
    await _exec_ignore(
        conn,
        "CREATE INDEX IF NOT EXISTS ix_web_flow_events_created ON web_flow_events (created_at)",
    )


async def _migration_v16_custom_element_builds(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v16: таблица web_custom_element_builds")

    await _exec_ignore(
        conn,
        """
        CREATE TABLE IF NOT EXISTS web_custom_element_builds (
            id VARCHAR(36) PRIMARY KEY,
            label VARCHAR(255) NOT NULL DEFAULT '',
            slug VARCHAR(128) NOT NULL DEFAULT '',
            runtime VARCHAR(32) NOT NULL DEFAULT 'react-component',
            source_kind VARCHAR(32) NOT NULL DEFAULT 'inline-code',
            source_value TEXT NOT NULL DEFAULT '',
            export_name VARCHAR(128) NOT NULL DEFAULT 'default',
            props_schema_text TEXT NOT NULL DEFAULT '',
            sample_props_text TEXT NOT NULL DEFAULT '',
            events_text TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            status VARCHAR(32) NOT NULL DEFAULT 'queued',
            summary TEXT NOT NULL DEFAULT '',
            next_steps JSONB NOT NULL DEFAULT '[]'::jsonb,
            artifact JSONB,
            upload_meta JSONB,
            worker_id VARCHAR(64),
            worker_claimed_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
    )
    await _exec_ignore(
        conn,
        "CREATE INDEX IF NOT EXISTS ix_web_custom_element_builds_status ON web_custom_element_builds (status)",
    )
    await _exec_ignore(
        conn,
        "CREATE INDEX IF NOT EXISTS ix_web_custom_element_builds_created ON web_custom_element_builds (created_at)",
    )


async def _migration_v19_keys_tg_id_nullable(conn: AsyncConnection) -> None:
    """Снимает NOT NULL с keys.tg_id и переносит PK на (user_id, client_id).

    Старый PK (tg_id, client_id) не позволяет создавать подписки для web-only
    пользователей, у которых tg_id=NULL. user_id у ключа есть всегда (FK на
    users.id), поэтому делаем его новым компонентом PK.
    """
    _mig_out("[schema_upgrade] v19: keys.tg_id nullable, PK на (user_id, client_id)")

    if not await _table_exists(conn, "keys"):
        return

    if not await _column_exists(conn, "keys", "user_id"):
        logger.warning("[schema_upgrade] v19: keys.user_id не найден, пропуск")
        return

    await _exec_ignore(
        conn,
        """
        UPDATE keys k SET user_id = u.id
        FROM users u
        WHERE k.user_id IS NULL AND k.tg_id IS NOT NULL AND u.tg_id = k.tg_id
        """,
    )

    if await _column_has_nulls(conn, "keys", "user_id"):
        logger.warning("[schema_upgrade] v19: в keys остались строки с user_id=NULL, пропуск смены PK")
        return

    await _drop_pk(conn, "keys")
    await _exec_ignore(conn, 'ALTER TABLE "keys" ALTER COLUMN "user_id" SET NOT NULL')
    await _exec_ignore(conn, 'ALTER TABLE "keys" ALTER COLUMN "tg_id" DROP NOT NULL')
    await _exec_ignore(conn, 'ALTER TABLE "keys" ADD PRIMARY KEY (user_id, client_id)')
    await _exec_ignore(conn, 'CREATE INDEX IF NOT EXISTS ix_keys_tg_id ON "keys" (tg_id)')


async def _migration_v20_add_identity_google_sub(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v20: identities.google_sub")
    if not await _table_exists(conn, "identities"):
        return
    if not await _column_exists(conn, "identities", "google_sub"):
        await _exec_ignore(conn, "ALTER TABLE identities ADD COLUMN google_sub VARCHAR(64)")
    await _exec_ignore(
        conn,
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_identities_google_sub ON identities (google_sub) WHERE google_sub IS NOT NULL",
    )


async def _migration_v21_add_identity_yandex_sub(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v21: identities.yandex_sub")
    if not await _table_exists(conn, "identities"):
        return
    if not await _column_exists(conn, "identities", "yandex_sub"):
        await _exec_ignore(conn, "ALTER TABLE identities ADD COLUMN yandex_sub VARCHAR(64)")
    await _exec_ignore(
        conn,
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_identities_yandex_sub ON identities (yandex_sub) WHERE yandex_sub IS NOT NULL",
    )


async def _migration_v22_add_identity_onboarding_completed_at(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v22: identities.onboarding_completed_at")
    if not await _table_exists(conn, "identities"):
        return
    if not await _column_exists(conn, "identities", "onboarding_completed_at"):
        await _exec_ignore(conn, "ALTER TABLE identities ADD COLUMN onboarding_completed_at TIMESTAMP")


async def _migration_v23_add_identity_onboarding_stage(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v23: identities.onboarding_stage")
    if not await _table_exists(conn, "identities"):
        return
    if not await _column_exists(conn, "identities", "onboarding_stage"):
        await _exec_ignore(conn, "ALTER TABLE identities ADD COLUMN onboarding_stage VARCHAR(32)")


async def _migration_v26_add_keys_indexes(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v26: индексы keys(expiry_time/server_id/tariff_id)")
    if not await _table_exists(conn, "keys"):
        return
    if not await _index_exists(conn, "keys", "ix_keys_expiry_time"):
        await _exec_ignore(conn, "CREATE INDEX ix_keys_expiry_time ON keys(expiry_time)")
    if not await _index_exists(conn, "keys", "ix_keys_server_id"):
        await _exec_ignore(conn, "CREATE INDEX ix_keys_server_id ON keys(server_id)")
    if not await _index_exists(conn, "keys", "ix_keys_tariff_id"):
        await _exec_ignore(conn, "CREATE INDEX ix_keys_tariff_id ON keys(tariff_id)")


async def _migration_v25_add_partners_indexes(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v25: индексы на partners(partner_tg_id/joined_tg_id)")
    if not await _table_exists(conn, "partners"):
        return
    if not await _index_exists(conn, "partners", "ix_partners_partner_tg_id"):
        await _exec_ignore(conn, "CREATE INDEX ix_partners_partner_tg_id ON partners(partner_tg_id)")
    if not await _index_exists(conn, "partners", "ix_partners_joined_tg_id"):
        await _exec_ignore(conn, "CREATE INDEX ix_partners_joined_tg_id ON partners(joined_tg_id)")


async def _migration_v27_add_admins_permissions(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v27: admins.permissions (JSONB)")
    if not await _table_exists(conn, "admins"):
        return
    if not await _column_exists(conn, "admins", "permissions"):
        await _exec_ignore(
            conn,
            "ALTER TABLE admins ADD COLUMN permissions JSONB NOT NULL DEFAULT '[]'::jsonb",
        )
        await _exec_ignore(
            conn,
            """
            UPDATE admins
               SET permissions = '["users","keys","broadcasting","coupons","gifts"]'::jsonb
             WHERE role = 'moderator' AND permissions = '[]'::jsonb
            """,
        )


async def _migration_v28_add_identity_notif_prefs(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v28: таблица identity_notif_prefs (toggle каналов уведомлений)")
    if not await _table_exists(conn, "identities"):
        return
    if not await _table_exists(conn, "identity_notif_prefs"):
        await _exec_ignore(
            conn,
            """
            CREATE TABLE identity_notif_prefs (
                identity_id VARCHAR(36) NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
                channel VARCHAR(32) NOT NULL,
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (identity_id, channel)
            )
            """,
        )


async def _migration_v24_add_identity_sessions(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v24: таблица identity_sessions + перенос существующих токенов")
    if not await _table_exists(conn, "identities"):
        return
    if not await _table_exists(conn, "identity_sessions"):
        await _exec_ignore(
            conn,
            """
            CREATE TABLE identity_sessions (
                id VARCHAR(36) PRIMARY KEY,
                identity_id VARCHAR(36) NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
                token_hash VARCHAR(64) NOT NULL UNIQUE,
                device_label VARCHAR(128),
                user_agent TEXT,
                ip VARCHAR(64),
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP
            )
            """,
        )
        await _exec_ignore(
            conn,
            "CREATE INDEX IF NOT EXISTS ix_identity_sessions_identity_id ON identity_sessions(identity_id)",
        )
        await _exec_ignore(
            conn,
            "CREATE INDEX IF NOT EXISTS ix_identity_sessions_identity_last_seen "
            "ON identity_sessions(identity_id, last_seen_at)",
        )
    if await _column_exists(conn, "identities", "api_token_hash"):
        await _exec_ignore(conn, "CREATE EXTENSION IF NOT EXISTS pgcrypto")
        await _exec_ignore(
            conn,
            """
            INSERT INTO identity_sessions (
                id, identity_id, token_hash, device_label, created_at, last_seen_at
            )
            SELECT
                gen_random_uuid()::text,
                id,
                api_token_hash,
                'legacy',
                COALESCE(token_issued_at, CURRENT_TIMESTAMP),
                COALESCE(token_issued_at, CURRENT_TIMESTAMP)
            FROM identities
            WHERE api_token_hash IS NOT NULL
              AND api_token_hash NOT IN (SELECT token_hash FROM identity_sessions)
            """,
        )


async def _migration_v29_add_scheduled_broadcasts_channel(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v29: scheduled_broadcasts.channel (bot/site/both)")
    if not await _table_exists(conn, "scheduled_broadcasts"):
        return
    if not await _column_exists(conn, "scheduled_broadcasts", "channel"):
        await conn.execute(
            text("ALTER TABLE scheduled_broadcasts ADD COLUMN channel VARCHAR(8) NOT NULL DEFAULT 'both'")
        )


async def _migration_v49_widen_scheduled_broadcasts_channel(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v49: scheduled_broadcasts.channel → VARCHAR(32)")
    if not await _table_exists(conn, "scheduled_broadcasts"):
        return
    await _exec_ignore(
        conn,
        "ALTER TABLE scheduled_broadcasts ALTER COLUMN channel TYPE VARCHAR(32)",
    )


async def _migration_v50_users_legal_accepted_at(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v50: users.legal_accepted_at (согласие с документами)")
    if not await _table_exists(conn, "users"):
        return
    if await _column_exists(conn, "users", "legal_accepted_at"):
        return
    await _exec_ignore(conn, "ALTER TABLE users ADD COLUMN legal_accepted_at TIMESTAMP")


async def _migration_v51_drop_tg_id_foreign_keys(conn: AsyncConnection) -> None:
    """Снимает внешние ключи, смотрящие на users.tg_id.

    Связь владельца и так держится на user_id. А эти ключи только мешали:
    обнулить users.tg_id при отвязке Telegram база не давала, пока на него
    ссылались, — приходилось руками гасить зеркала во всех таблицах.
    """
    _mig_out("[schema_upgrade] v51: снятие внешних ключей на users.tg_id")
    if not await _table_exists(conn, "users"):
        return
    result = await conn.execute(
        text(
            """
            SELECT con.conname, rel.relname AS src_table
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
            WHERE con.confrelid = 'users'::regclass
              AND con.contype = 'f'
              AND nsp.nspname = 'public'
              AND EXISTS (
                  SELECT 1
                  FROM unnest(con.confkey) AS target_attnum
                  JOIN pg_attribute att
                    ON att.attrelid = con.confrelid AND att.attnum = target_attnum
                  WHERE att.attname = 'tg_id'
              )
            """
        )
    )
    rows = result.all()
    for conname, src_table in rows:
        _mig_out(f"[schema_upgrade] v51: снимаю {src_table}.{conname}")
        await _exec_ignore(conn, f'ALTER TABLE "{src_table}" DROP CONSTRAINT IF EXISTS "{conname}"')
    if not rows:
        _mig_out("[schema_upgrade] v51: таких ключей нет, пропускаю")


async def _migration_v30_add_users_created_at_index(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v30: индекс users(created_at)")
    if not await _table_exists(conn, "users"):
        return
    if not await _column_exists(conn, "users", "created_at"):
        return
    if not await _index_exists(conn, "users", "ix_users_created_at"):
        await _exec_ignore(conn, "CREATE INDEX IF NOT EXISTS ix_users_created_at ON users (created_at)")


async def _migration_v31_repair_tg_mirror_nulls(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v31: Repair NULL tg_id mirrors")
    await _run_tg_mirror_backfill(conn, nulls_only=True)


async def _migration_v32_add_polls(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v32: таблицы опросов (polls + poll_messages + poll_votes)")
    if not await _table_exists(conn, "polls"):
        await _exec_ignore(
            conn,
            """
            CREATE TABLE polls (
                id VARCHAR(36) PRIMARY KEY,
                question TEXT NOT NULL,
                options JSONB NOT NULL,
                allows_multiple BOOLEAN NOT NULL DEFAULT FALSE,
                is_anonymous BOOLEAN NOT NULL DEFAULT FALSE,
                status VARCHAR(16) NOT NULL DEFAULT 'open',
                sent_count INTEGER NOT NULL DEFAULT 0,
                created_by_tg_id BIGINT REFERENCES users(tg_id) ON DELETE SET NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                closed_at TIMESTAMP
            )
            """,
        )
        await _exec_ignore(conn, "CREATE INDEX IF NOT EXISTS ix_polls_status ON polls (status)")
        await _exec_ignore(conn, "CREATE INDEX IF NOT EXISTS ix_polls_created_by_tg_id ON polls (created_by_tg_id)")
    if not await _table_exists(conn, "poll_messages"):
        await _exec_ignore(
            conn,
            """
            CREATE TABLE poll_messages (
                telegram_poll_id VARCHAR(64) PRIMARY KEY,
                poll_id VARCHAR(36) NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
                tg_id BIGINT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
        )
        await _exec_ignore(conn, "CREATE INDEX IF NOT EXISTS ix_poll_messages_poll_id ON poll_messages (poll_id)")
    if not await _table_exists(conn, "poll_votes"):
        await _exec_ignore(
            conn,
            """
            CREATE TABLE poll_votes (
                poll_id VARCHAR(36) NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
                tg_id BIGINT NOT NULL,
                option_ids JSONB NOT NULL,
                voted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (poll_id, tg_id)
            )
            """,
        )


async def _migration_v33_web_page_views(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v33: таблица web_page_views")

    await _exec_ignore(
        conn,
        """
        CREATE TABLE IF NOT EXISTS web_page_views (
            id VARCHAR(36) PRIMARY KEY,
            visitor_id VARCHAR(36) NOT NULL,
            page_slug VARCHAR(64) NOT NULL,
            referrer VARCHAR(255),
            utm_source VARCHAR(64),
            utm_medium VARCHAR(64),
            utm_campaign VARCHAR(64),
            device VARCHAR(16),
            locale VARCHAR(8),
            authenticated BOOLEAN,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
    )
    await _exec_ignore(
        conn,
        "CREATE INDEX IF NOT EXISTS ix_web_page_views_created ON web_page_views (created_at)",
    )
    await _exec_ignore(
        conn,
        "CREATE INDEX IF NOT EXISTS ix_web_page_views_slug_created ON web_page_views (page_slug, created_at)",
    )
    await _exec_ignore(
        conn,
        "CREATE INDEX IF NOT EXISTS ix_web_page_views_visitor ON web_page_views (visitor_id)",
    )


async def _migration_v34_web_page_views_source(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v34: web_page_views.source (web/webapp)")
    if not await _table_exists(conn, "web_page_views"):
        return
    if not await _column_exists(conn, "web_page_views", "source"):
        await _exec_ignore(conn, "ALTER TABLE web_page_views ADD COLUMN source VARCHAR(16)")


async def _migration_v36_web_page_views_ab_variant(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v36: web_page_views.ab_variant (A/B)")
    if not await _table_exists(conn, "web_page_views"):
        return
    if not await _column_exists(conn, "web_page_views", "ab_variant"):
        await _exec_ignore(conn, "ALTER TABLE web_page_views ADD COLUMN ab_variant VARCHAR(16)")


async def _migration_v37_rate_limit_counters(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v37: таблица rate_limit_counters (распределённый fallback)")
    await _exec_ignore(
        conn,
        """
        CREATE TABLE IF NOT EXISTS rate_limit_counters (
            bucket VARCHAR(255) NOT NULL,
            window_start BIGINT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (bucket, window_start)
        )
        """,
    )
    await _exec_ignore(
        conn,
        "CREATE INDEX IF NOT EXISTS ix_rate_limit_counters_window ON rate_limit_counters (window_start)",
    )


async def _migration_v35_key_traffic_history(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v35: таблица key_traffic_history (история использования)")
    await _exec_ignore(
        conn,
        """
        CREATE TABLE IF NOT EXISTS key_traffic_history (
            id SERIAL PRIMARY KEY,
            client_id VARCHAR(128) NOT NULL,
            tg_id BIGINT,
            used_gb DOUBLE PRECISION,
            limit_gb DOUBLE PRECISION,
            snapshot_date DATE NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_key_traffic_history_client_date UNIQUE (client_id, snapshot_date)
        )
        """,
    )
    await _exec_ignore(
        conn,
        "CREATE INDEX IF NOT EXISTS ix_key_traffic_history_client_date ON key_traffic_history (client_id, snapshot_date)",
    )
    await _exec_ignore(
        conn,
        "CREATE INDEX IF NOT EXISTS ix_key_traffic_history_date ON key_traffic_history (snapshot_date)",
    )


async def _migration_v38_subscription_events(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v38: таблица subscription_events (журнал жизненного цикла подписок)")
    await _exec_ignore(
        conn,
        """
        CREATE TABLE IF NOT EXISTS subscription_events (
            id BIGSERIAL PRIMARY KEY,
            event_type VARCHAR(24) NOT NULL,
            user_id BIGINT,
            tg_id BIGINT,
            client_id VARCHAR(128),
            tariff_id INTEGER,
            server_id VARCHAR,
            price_rub DOUBLE PRECISION,
            duration_days INTEGER,
            expiry_time BIGINT,
            was_expired BOOLEAN,
            source VARCHAR(32),
            metadata JSONB,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
    )
    await _exec_ignore(
        conn,
        "CREATE INDEX IF NOT EXISTS ix_subscription_events_type_created ON subscription_events (event_type, created_at)",
    )
    await _exec_ignore(
        conn, "CREATE INDEX IF NOT EXISTS ix_subscription_events_created ON subscription_events (created_at)"
    )
    await _exec_ignore(
        conn, "CREATE INDEX IF NOT EXISTS ix_subscription_events_client ON subscription_events (client_id)"
    )
    await _exec_ignore(conn, "CREATE INDEX IF NOT EXISTS ix_subscription_events_user ON subscription_events (user_id)")


async def _migration_v39_daily_subscription_metrics(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v39: таблица daily_subscription_metrics (дневные снапшоты подписок)")
    await _exec_ignore(
        conn,
        """
        CREATE TABLE IF NOT EXISTS daily_subscription_metrics (
            snapshot_date DATE PRIMARY KEY,
            active INTEGER NOT NULL DEFAULT 0,
            created INTEGER NOT NULL DEFAULT 0,
            renewed INTEGER NOT NULL DEFAULT 0,
            expired INTEGER NOT NULL DEFAULT 0,
            deleted INTEGER NOT NULL DEFAULT 0,
            revenue_rub DOUBLE PRECISION NOT NULL DEFAULT 0,
            by_tariff JSONB,
            by_server JSONB,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
    )


async def _migration_v40_tariff_cooldown_days(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v40: tariffs.cooldown_days (задержка между покупками тарифа, дней)")
    await _exec_ignore(
        conn,
        "ALTER TABLE tariffs ADD COLUMN IF NOT EXISTS cooldown_days INTEGER NOT NULL DEFAULT 0",
    )


async def _migration_v41_tariff_visibility_rules(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v41: tariffs.visibility_rules (условная видимость тарифа по признаку юзера)")
    await _exec_ignore(
        conn,
        "ALTER TABLE tariffs ADD COLUMN IF NOT EXISTS visibility_rules JSONB",
    )


async def _migration_v42_tariff_description(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v42: tariffs.description (описание состава тарифа)")
    await _exec_ignore(
        conn,
        "ALTER TABLE tariffs ADD COLUMN IF NOT EXISTS description VARCHAR",
    )


async def _migration_v43_tariff_subgroup_settings(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v43: tariff_subgroup_settings (текст подгруппы на экране выбора)")
    await _exec_ignore(
        conn,
        """
        CREATE TABLE IF NOT EXISTS tariff_subgroup_settings (
            id SERIAL PRIMARY KEY,
            group_code VARCHAR NOT NULL,
            subgroup_title VARCHAR NOT NULL,
            description VARCHAR,
            updated_at TIMESTAMP DEFAULT now(),
            CONSTRAINT uq_tariff_subgroup_setting UNIQUE (group_code, subgroup_title)
        )
        """,
    )


async def _migration_v44_key_traffic_hourly(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v44: key_traffic_hourly (почасовая история трафика)")
    await _exec_ignore(
        conn,
        """
        CREATE TABLE IF NOT EXISTS key_traffic_hourly (
            id SERIAL PRIMARY KEY,
            client_id VARCHAR(128) NOT NULL,
            tg_id BIGINT,
            used_gb DOUBLE PRECISION,
            snapshot_hour TIMESTAMP NOT NULL,
            CONSTRAINT uq_key_traffic_hourly_client_hour UNIQUE (client_id, snapshot_hour)
        )
        """,
    )
    await _exec_ignore(
        conn,
        "CREATE INDEX IF NOT EXISTS ix_key_traffic_hourly_client_hour ON key_traffic_hourly(client_id, snapshot_hour)",
    )
    await _exec_ignore(
        conn,
        "CREATE INDEX IF NOT EXISTS ix_key_traffic_hourly_hour ON key_traffic_hourly(snapshot_hour)",
    )


async def _migration_v45_add_tickets(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v45: таблицы тикетов (tickets + ticket_messages)")
    if not await _table_exists(conn, "tickets"):
        await _exec_ignore(
            conn,
            """
            CREATE TABLE tickets (
                id VARCHAR(36) PRIMARY KEY,
                identity_id VARCHAR(36) NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
                subject VARCHAR(255),
                category VARCHAR(64),
                priority VARCHAR(16) NOT NULL DEFAULT 'normal',
                status VARCHAR(16) NOT NULL DEFAULT 'open',
                source VARCHAR(16) NOT NULL DEFAULT 'web',
                assigned_agent_tg_id BIGINT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_message_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
        )
        await _exec_ignore(conn, "CREATE INDEX IF NOT EXISTS ix_tickets_identity_id ON tickets (identity_id)")
        await _exec_ignore(conn, "CREATE INDEX IF NOT EXISTS ix_tickets_status ON tickets (status)")
        await _exec_ignore(conn, "CREATE INDEX IF NOT EXISTS ix_tickets_category ON tickets (category)")
        await _exec_ignore(
            conn, "CREATE INDEX IF NOT EXISTS ix_tickets_assigned_agent_tg_id ON tickets (assigned_agent_tg_id)"
        )
        await _exec_ignore(conn, "CREATE INDEX IF NOT EXISTS ix_tickets_last_message_at ON tickets (last_message_at)")
    if not await _table_exists(conn, "ticket_messages"):
        await _exec_ignore(
            conn,
            """
            CREATE TABLE ticket_messages (
                id VARCHAR(36) PRIMARY KEY,
                ticket_id VARCHAR(36) NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
                author VARCHAR(16) NOT NULL,
                agent_tg_id BIGINT,
                body TEXT NOT NULL DEFAULT '',
                attachments JSONB,
                read_by_client BOOLEAN NOT NULL DEFAULT FALSE,
                read_by_agent BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
        )
        await _exec_ignore(
            conn, "CREATE INDEX IF NOT EXISTS ix_ticket_messages_ticket_id ON ticket_messages (ticket_id)"
        )
        await _exec_ignore(
            conn, "CREATE INDEX IF NOT EXISTS ix_ticket_messages_created_at ON ticket_messages (created_at)"
        )


async def _migration_v46_ticket_extras(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v46: tickets.rating/tags/ref_type/ref_id")
    if not await _table_exists(conn, "tickets"):
        return
    if not await _column_exists(conn, "tickets", "rating"):
        await _exec_ignore(conn, "ALTER TABLE tickets ADD COLUMN rating SMALLINT")
    if not await _column_exists(conn, "tickets", "tags"):
        await _exec_ignore(conn, "ALTER TABLE tickets ADD COLUMN tags JSONB")
    if not await _column_exists(conn, "tickets", "ref_type"):
        await _exec_ignore(conn, "ALTER TABLE tickets ADD COLUMN ref_type VARCHAR(16)")
    if not await _column_exists(conn, "tickets", "ref_id"):
        await _exec_ignore(conn, "ALTER TABLE tickets ADD COLUMN ref_id VARCHAR(64)")
    if not await _column_exists(conn, "tickets", "topic_id"):
        await _exec_ignore(conn, "ALTER TABLE tickets ADD COLUMN topic_id BIGINT")
        await _exec_ignore(conn, "CREATE INDEX IF NOT EXISTS ix_tickets_topic_id ON tickets (topic_id)")


async def _migration_v47_ticket_topic_id(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v47: tickets.topic_id (форум-темы)")
    if not await _table_exists(conn, "tickets"):
        return
    if not await _column_exists(conn, "tickets", "topic_id"):
        await _exec_ignore(conn, "ALTER TABLE tickets ADD COLUMN topic_id BIGINT")
        await _exec_ignore(conn, "CREATE INDEX IF NOT EXISTS ix_tickets_topic_id ON tickets (topic_id)")


async def _migration_v48_ticket_timestamptz(conn: AsyncConnection) -> None:
    _mig_out("[schema_upgrade] v48: tickets/ticket_messages timestamp → timestamptz (UTC)")
    targets = (
        ("tickets", ("created_at", "updated_at", "last_message_at")),
        ("ticket_messages", ("created_at",)),
    )
    for table, cols in targets:
        if not await _table_exists(conn, table):
            continue
        for col in cols:
            dtype = await conn.scalar(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=:t AND column_name=:c"
                ),
                {"t": table, "c": col},
            )
            if dtype == "timestamp without time zone":
                await _exec_ignore(
                    conn,
                    f"ALTER TABLE {table} ALTER COLUMN {col} TYPE TIMESTAMPTZ USING {col} AT TIME ZONE 'UTC'",
                )


async def _migration_v52_payments_status_created_index(conn: AsyncConnection) -> None:
    """Индексы на payments.status/created_at.

    По этой паре фильтруют и аналитика выручки, и уборка зависших платежей,
    а таблица растёт с каждой попыткой оплаты — без индекса каждый такой
    запрос читал её целиком.
    """
    _mig_out("[schema_upgrade] v52: индексы payments (status, created_at)")
    if not await _table_exists(conn, "payments"):
        return
    for index_name, columns in (
        ("ix_payments_status_created", "status, created_at"),
        ("ix_payments_created", "created_at"),
    ):
        if await _index_exists(conn, "payments", index_name):
            continue
        await conn.execute(text(f"CREATE INDEX {index_name} ON payments ({columns})"))
        _mig_out(f"[schema_upgrade] v52: создан {index_name}")


_MIGRATIONS = [
    (1, "Добавление users.id", _migration_v1_add_users_id),
    (2, "Добавление user_id колонок", _migration_v2_add_user_id_columns),
    (3, "Заполнение user_id из tg_id", _migration_v3_populate_user_ids),
    (4, "Добавление tg_id mirrors", _migration_v4_add_tg_id_mirrors),
    (5, "Очистка записей с NULL user_id", _migration_v9_cleanup_orphaned_records),
    (6, "Переключение PK на user_id", _migration_v5_switch_pks_to_user_id),
    (7, "Добавление foreign keys", _migration_v6_add_foreign_keys),
    (8, "Backfill tg_id mirrors", _migration_v7_backfill_tg_mirrors),
    (9, "Исправление timezone для notifications", _migration_v8_fix_notification_timezone),
    (10, "Финальная установка NOT NULL на user_id", _migration_v10_finalize_user_id_not_null),
    (11, "Финализация legacy таблиц на user_id", _migration_v11_finalize_legacy_tables),
    (12, "Снятие NOT NULL с tg_id в legacy таблицах", _migration_v12_relax_legacy_tg_id_nullability),
    (13, "Таблицы вариантов web-страниц", _migration_v13_add_web_page_variants),
    (14, "WebFlow граф-модель (nodes + edges)", _migration_v14_web_flow_graph_model),
    (15, "Восстановление orphan tg_ids в users", _migration_v15_recover_orphan_users),
    (16, "Таблица custom element builds", _migration_v16_custom_element_builds),
    (17, "Таблица flow analytics events", _migration_v16b_web_flow_events),
    (18, "Таблица web_error_reports", _migration_v18_web_error_reports),
    (19, "keys.tg_id nullable, PK на (user_id, client_id)", _migration_v19_keys_tg_id_nullable),
    (20, "identities.google_sub", _migration_v20_add_identity_google_sub),
    (21, "identities.yandex_sub", _migration_v21_add_identity_yandex_sub),
    (22, "identities.onboarding_completed_at", _migration_v22_add_identity_onboarding_completed_at),
    (23, "identities.onboarding_stage", _migration_v23_add_identity_onboarding_stage),
    (24, "таблица identity_sessions (мультидевайс)", _migration_v24_add_identity_sessions),
    (25, "индексы на partners(partner_tg_id/joined_tg_id)", _migration_v25_add_partners_indexes),
    (26, "индексы keys(expiry_time/server_id/tariff_id)", _migration_v26_add_keys_indexes),
    (27, "admins.permissions (JSONB per-admin permissions)", _migration_v27_add_admins_permissions),
    (28, "таблица identity_notif_prefs (toggle каналов)", _migration_v28_add_identity_notif_prefs),
    (29, "scheduled_broadcasts.channel (bot/site/both)", _migration_v29_add_scheduled_broadcasts_channel),
    (30, "индекс users(created_at)", _migration_v30_add_users_created_at_index),
    (31, "Repair NULL tg_id mirrors", _migration_v31_repair_tg_mirror_nulls),
    (32, "Таблицы опросов (polls/poll_messages/poll_votes)", _migration_v32_add_polls),
    (33, "Таблица web_page_views (аналитика посещений)", _migration_v33_web_page_views),
    (34, "web_page_views.source (web/webapp)", _migration_v34_web_page_views_source),
    (35, "Таблица key_traffic_history (история трафика)", _migration_v35_key_traffic_history),
    (36, "web_page_views.ab_variant (A/B)", _migration_v36_web_page_views_ab_variant),
    (37, "Таблица rate_limit_counters (распределённый fallback лимитера)", _migration_v37_rate_limit_counters),
    (38, "Таблица subscription_events (журнал жизненного цикла подписок)", _migration_v38_subscription_events),
    (39, "Таблица daily_subscription_metrics (дневные снапшоты)", _migration_v39_daily_subscription_metrics),
    (40, "tariffs.cooldown_days (задержка между покупками тарифа)", _migration_v40_tariff_cooldown_days),
    (41, "tariffs.visibility_rules (условная видимость тарифа)", _migration_v41_tariff_visibility_rules),
    (42, "tariffs.description (описание состава тарифа)", _migration_v42_tariff_description),
    (43, "tariff_subgroup_settings (текст подгруппы)", _migration_v43_tariff_subgroup_settings),
    (44, "key_traffic_hourly (почасовая история трафика)", _migration_v44_key_traffic_hourly),
    (45, "Таблицы тикетов (tickets/ticket_messages)", _migration_v45_add_tickets),
    (46, "tickets.rating/tags/ref (CSAT, теги, контекст)", _migration_v46_ticket_extras),
    (47, "tickets.topic_id (форум-темы)", _migration_v47_ticket_topic_id),
    (48, "tickets/ticket_messages → timestamptz (UTC)", _migration_v48_ticket_timestamptz),
    (49, "scheduled_broadcasts.channel → VARCHAR(32) (мультиканал)", _migration_v49_widen_scheduled_broadcasts_channel),
    (50, "users.legal_accepted_at (согласие с документами)", _migration_v50_users_legal_accepted_at),
    (51, "Снятие внешних ключей на users.tg_id", _migration_v51_drop_tg_id_foreign_keys),
    (52, "Индексы payments (status, created_at)", _migration_v52_payments_status_created_index),
]


async def apply_all_migrations(conn: AsyncConnection) -> None:
    if not _is_postgresql():
        return

    await _ensure_migrations_table(conn)
    current_version = await _get_current_version(conn)

    for version, description, migration_func in _MIGRATIONS:
        if version <= current_version:
            continue

        _mig_out(f"[schema_upgrade] Применение миграции v{version}: {description}")
        try:
            await migration_func(conn)
            await _mark_migration_applied(conn, version, description)
            _mig_out(f"[schema_upgrade] Миграция v{version} применена успешно")
        except Exception as e:
            logger.error(f"[schema_upgrade] Ошибка при применении миграции v{version}: {e}")
            raise

    _mig_out(
        f"[schema_upgrade] Все миграции применены, текущая версия: {await _get_current_version(conn)}",
        "green",
    )


async def apply_account_schema_if_needed(conn: AsyncConnection) -> None:
    await apply_all_migrations(conn)

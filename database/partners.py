from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from database.access.resolution import TelegramId, resolve_user_optional
from database.migrations.partners import PARTNER_OWNER_COLUMNS
from database.models import User
from utils.referral_codes import decode_partner_code, encode_partner_code


PARTNER_LINKS = """
    SELECT p.id, p.created_at,
        COALESCE(p.partner_user_id, pu.id) AS partner_user_id,
        COALESCE(p.joined_user_id, ju.id) AS joined_user_id,
        CASE WHEN p.partner_user_id IS NULL THEN p.partner_tg_id ELSE pu.tg_id END AS partner_tg_id,
        CASE WHEN p.joined_user_id IS NULL THEN p.joined_tg_id ELSE ju.tg_id END AS joined_tg_id
    FROM partners p
    LEFT JOIN users pu ON pu.id = p.partner_user_id OR (p.partner_user_id IS NULL AND pu.tg_id = p.partner_tg_id)
    LEFT JOIN users ju ON ju.id = p.joined_user_id OR (p.joined_user_id IS NULL AND ju.tg_id = p.joined_tg_id)
"""
PROFILE_COLUMNS = "id, tg_id, partner_code, COALESCE(partner_balance, 0) AS partner_balance, payout_method, card_number, partner_percent, partner_percent_custom"


async def partner_schema_available(session: AsyncSession) -> bool:
    """Проверяет наличие таблиц партнёрского дополнения."""

    def inspect_schema(sync):
        inspector = inspect(sync.connection())
        tables = set(inspector.get_table_names())
        optional = set(PARTNER_OWNER_COLUMNS) & tables
        if not optional:
            return False
        if not {"users", *PARTNER_OWNER_COLUMNS} <= tables:
            raise RuntimeError("Неполная схема партнёрского дополнения")
        for table, pairs in PARTNER_OWNER_COLUMNS.items():
            existing = {c["name"] for c in inspector.get_columns(table)}
            required = {column for pair in pairs for column in pair}
            if not required <= existing:
                raise RuntimeError("Партнёрская схема требует обновления идентификаторов")
            if table == "payout_requests" and "reserved_amount" not in existing:
                raise RuntimeError("Партнёрская схема требует обновления резервов выплат")
        columns = {c["name"] for c in inspector.get_columns("users")}
        required_profile = {
            "partner_balance",
            "partner_code",
            "payout_method",
            "card_number",
            "partner_percent",
            "partner_percent_custom",
        }
        if not required_profile <= columns:
            raise RuntimeError("Неполный профиль партнёрского дополнения")
        return True

    return await session.run_sync(inspect_schema)


async def get_partner_profile(session: AsyncSession, user_id: int, lock: bool = False) -> dict | None:
    """Возвращает партнёрский профиль по ID клиента."""
    if lock:
        await session.execute(select(User.id).where(User.id == int(user_id)).with_for_update())
    row = (
        (await session.execute(text(f"SELECT {PROFILE_COLUMNS} FROM users WHERE id = :id"), {"id": int(user_id)}))
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def resolve_partner_telegram(session: AsyncSession, tg_id: int) -> dict | None:
    """Находит партнёра только по настоящему Telegram-адресу."""
    user = await resolve_user_optional(session, TelegramId(tg_id))
    return await get_partner_profile(session, user.id) if user else None


async def find_partner_by_code(session: AsyncSession, code: str) -> dict | None:
    """Находит владельца партнёрского кода без смешения идентификаторов."""
    code = str(code or "").strip()
    if not code:
        return None
    row = (
        (
            await session.execute(
                text(
                    f"SELECT {PROFILE_COLUMNS} FROM users WHERE lower(COALESCE(partner_code, '')) = lower(:code) LIMIT 1"
                ),
                {"code": code},
            )
        )
        .mappings()
        .first()
    )
    if row:
        return dict(row)
    ref = decode_partner_code(code)
    user = await resolve_user_optional(session, ref) if ref is not None else None
    return await get_partner_profile(session, user.id) if user else None


async def ensure_partner_code(session: AsyncSession, user_id: int, raw_code: str | None) -> str:
    """Сохраняет непрозрачный код партнёра по ID клиента."""
    code = str(raw_code or "").strip()
    if code and not code.isdigit() and not code.startswith("r1_"):
        return code
    code = encode_partner_code(int(user_id))
    await session.execute(
        text("UPDATE users SET partner_code = :code WHERE id = :id"), {"code": code, "id": int(user_id)}
    )
    await session.flush()
    return code


async def get_partner_link(session: AsyncSession, joined_user_id: int) -> dict | None:
    """Возвращает пригласившего владельца по ID приглашённого."""
    row = (
        (
            await session.execute(
                text(
                    f"WITH links AS ({PARTNER_LINKS}) SELECT * FROM links WHERE joined_user_id = :id ORDER BY created_at, id LIMIT 1"
                ),
                {"id": int(joined_user_id)},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def create_partner_link(session: AsyncSession, partner_user_id: int, joined_user_id: int) -> dict:
    """Привязывает приглашённого к партнёру с блокировкой клиента."""
    if int(partner_user_id) == int(joined_user_id):
        raise ValueError("self")
    profiles = {}
    for user_id in sorted({int(partner_user_id), int(joined_user_id)}):
        profiles[user_id] = await get_partner_profile(session, user_id, lock=True)
    partner, joined = profiles[int(partner_user_id)], profiles[int(joined_user_id)]
    if not partner or not joined:
        raise ValueError("missing")
    if await get_partner_link(session, joined_user_id):
        raise ValueError("already")
    params = {
        "partner_user_id": int(partner_user_id),
        "joined_user_id": int(joined_user_id),
        "partner_tg_id": partner["tg_id"],
        "joined_tg_id": joined["tg_id"],
    }
    await session.execute(
        text(
            "INSERT INTO partners (partner_user_id, joined_user_id, partner_tg_id, joined_tg_id) VALUES (:partner_user_id, :joined_user_id, :partner_tg_id, :joined_tg_id)"
        ),
        params,
    )
    return params


async def delete_partner_link(session: AsyncSession, partner_user_id: int, joined_user_id: int) -> bool:
    """Удаляет конкретную партнёрскую связь по ID клиентов."""
    result = await session.execute(
        text(
            f"WITH links AS ({PARTNER_LINKS}) DELETE FROM partners WHERE id IN (SELECT id FROM links WHERE partner_user_id = :partner AND joined_user_id = :joined) RETURNING id"
        ),
        {"partner": int(partner_user_id), "joined": int(joined_user_id)},
    )
    return result.first() is not None


async def get_partner_invited(session: AsyncSession, user_id: int, limit: int | None = None) -> list[dict]:
    """Считает подписки и платежи приглашённых по каноническому владельцу."""
    tail = " LIMIT :limit" if limit is not None else ""
    rows = (
        (
            await session.execute(
                text(f"""
        WITH links AS ({PARTNER_LINKS})
        SELECT pr.joined_user_id AS user_id, pr.joined_tg_id AS tg_id, pr.created_at,
            COALESCE(u.balance, 0) AS balance,
            (SELECT COUNT(*) FROM keys k WHERE k.user_id = u.id OR (k.user_id IS NULL AND k.tg_id = u.tg_id)) AS keys_count,
            (SELECT COUNT(*) FROM payments pay WHERE (pay.user_id = u.id OR (pay.user_id IS NULL AND pay.tg_id = u.tg_id)) AND lower(pay.status) = 'success') AS payments_count
        FROM links pr LEFT JOIN users u ON u.id = pr.joined_user_id
        WHERE pr.partner_user_id = :id ORDER BY pr.created_at DESC, pr.id DESC{tail}
    """),
                {"id": int(user_id), "limit": limit},
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


async def get_partner_ranking(session: AsyncSession, user_id: int, limit: int) -> dict:
    """Возвращает рейтинг и позицию партнёра по ID клиента."""
    ranked = f"WITH links AS ({PARTNER_LINKS}), ranked AS (SELECT partner_user_id, COUNT(DISTINCT joined_user_id) AS referred_count FROM links WHERE partner_user_id IS NOT NULL GROUP BY partner_user_id)"
    count = await session.execute(
        text(f"{ranked} SELECT referred_count FROM ranked WHERE partner_user_id = :id"), {"id": int(user_id)}
    )
    referred_count = int(count.scalar() or 0)
    position = None
    if referred_count:
        position = int(
            (
                await session.execute(
                    text(f"{ranked} SELECT COUNT(*) + 1 FROM ranked WHERE referred_count > :count"),
                    {"count": referred_count},
                )
            ).scalar()
            or 1
        )
    top = (
        (
            await session.execute(
                text(
                    f"{ranked} SELECT partner_user_id, referred_count FROM ranked ORDER BY referred_count DESC, partner_user_id LIMIT :limit"
                ),
                {"limit": int(limit)},
            )
        )
        .mappings()
        .all()
    )
    return {"user_referred_count": referred_count, "user_position": position, "top": [dict(row) for row in top]}


async def get_partner_stats(session: AsyncSession) -> dict:
    """Собирает общие метрики партнёрской программы."""
    row = (
        (
            await session.execute(
                text(f"""
        WITH links AS ({PARTNER_LINKS}), ranked AS (
            SELECT partner_user_id, COUNT(DISTINCT joined_user_id) AS ref_count FROM links
            WHERE partner_user_id IS NOT NULL GROUP BY partner_user_id
        ) SELECT
            (SELECT COUNT(*) FROM ranked) AS total_partners,
            (SELECT COUNT(DISTINCT partner_user_id) FROM links WHERE DATE(created_at) = CURRENT_DATE) AS partners_today,
            (SELECT COUNT(DISTINCT joined_user_id) FROM links WHERE partner_user_id IS NOT NULL) AS total_referred,
            (SELECT COALESCE(SUM(u.partner_balance), 0) FROM users u WHERE u.id IN (SELECT partner_user_id FROM ranked)) AS total_balance,
            (SELECT u.tg_id FROM ranked r LEFT JOIN users u ON u.id = r.partner_user_id ORDER BY ref_count DESC, r.partner_user_id LIMIT 1) AS top_partner_tg_id,
            (SELECT partner_user_id FROM ranked ORDER BY ref_count DESC, partner_user_id LIMIT 1) AS top_partner_user_id,
            (SELECT ref_count FROM ranked ORDER BY ref_count DESC, partner_user_id LIMIT 1) AS top_partner_refs
    """)
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


async def list_partners(session: AsyncSession, limit: int, offset: int) -> tuple[int, list[dict]]:
    """Возвращает партнёров с канонической статистикой приглашений."""
    ranked = f"WITH links AS ({PARTNER_LINKS}), ranked AS (SELECT partner_user_id, COUNT(DISTINCT joined_user_id) AS referred_count FROM links WHERE partner_user_id IS NOT NULL GROUP BY partner_user_id)"
    total = int((await session.execute(text(f"{ranked} SELECT COUNT(*) FROM ranked"))).scalar() or 0)
    rows = (
        (
            await session.execute(
                text(
                    f"{ranked} SELECT u.*, r.referred_count FROM ranked r JOIN users u ON u.id = r.partner_user_id ORDER BY u.partner_balance DESC, u.id LIMIT :limit OFFSET :offset"
                ),
                {"limit": limit, "offset": offset},
            )
        )
        .mappings()
        .all()
    )
    return total, [dict(row) for row in rows]


async def update_partner_profile(session: AsyncSession, user_id: int, **values) -> bool:
    """Обновляет разрешённые поля партнёрского профиля по ID клиента."""
    allowed = {
        "partner_balance",
        "partner_code",
        "payout_method",
        "card_number",
        "partner_percent",
        "partner_percent_custom",
    }
    if not values or not set(values) <= allowed:
        raise ValueError("Unsupported partner fields")
    assignments = ", ".join(f"{key} = :{key}" for key in values)
    result = await session.execute(
        text(f"UPDATE users SET {assignments} WHERE id = :id"), {"id": int(user_id), **values}
    )
    return result.rowcount > 0


async def partner_code_taken(session: AsyncSession, user_id: int, code: str) -> bool:
    """Проверяет занятость кода у другого клиента."""
    return (
        await session.execute(
            text("SELECT 1 FROM users WHERE lower(partner_code) = lower(:code) AND id != :id LIMIT 1"),
            {"id": int(user_id), "code": code},
        )
    ).first() is not None


async def reset_payout_methods(session: AsyncSession, methods: list[str]) -> None:
    """Сбрасывает реквизиты отключённых способов вывода."""
    for method in methods:
        await session.execute(
            text("UPDATE users SET card_number = NULL WHERE payout_method = :method"), {"method": method}
        )


PAYOUT_OWNERS = """
    SELECT pr.*, COALESCE(pr.user_id, u.id) AS owner_id,
        CASE WHEN pr.user_id IS NULL THEN pr.tg_id ELSE u.tg_id END AS owner_tg_id,
        COALESCE(pr.method, u.payout_method) AS payout_method,
        COALESCE(pr.destination, u.card_number) AS payout_destination
    FROM payout_requests pr
    LEFT JOIN users u ON u.id = pr.user_id OR (pr.user_id IS NULL AND u.tg_id = pr.tg_id)
"""


async def list_partner_payouts(
    session: AsyncSession,
    *,
    user_id: int | None = None,
    statuses: tuple[str, ...] = (),
    limit: int = 20,
    offset: int = 0,
    ascending: bool = False,
    excluded_method: str | None = None,
) -> tuple[int, list[dict]]:
    """Возвращает заявки с точным каноническим владельцем."""
    filters, params = [], {"limit": limit, "offset": offset}
    if user_id is not None:
        filters.append("owner_id = :id")
        params["id"] = int(user_id)
    if statuses:
        filters.append("status IN (" + ", ".join(f":status_{i}" for i in range(len(statuses))) + ")")
        params.update({f"status_{i}": status for i, status in enumerate(statuses)})
    if excluded_method is not None:
        filters.append("(payout_method IS NULL OR payout_method != :excluded_method)")
        params["excluded_method"] = excluded_method
    where = " WHERE " + " AND ".join(filters) if filters else ""
    base = f"WITH payouts AS ({PAYOUT_OWNERS})"
    total = int((await session.execute(text(f"{base} SELECT COUNT(*) FROM payouts{where}"), params)).scalar() or 0)
    order = "ASC" if ascending else "DESC"
    rows = (
        (
            await session.execute(
                text(
                    f"{base} SELECT * FROM payouts{where} ORDER BY created_at {order}, id {order} LIMIT :limit OFFSET :offset"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return total, [dict(row) for row in rows]


async def create_partner_payout(
    session: AsyncSession,
    user_id: int,
    amount: float,
    method: str,
    destination: str | None,
    *,
    debit_amount: float | None = None,
    status: str = "pending",
) -> tuple[int, float]:
    """Резервирует баланс и создаёт заявку на вывод для ID клиента."""
    owner = await get_partner_profile(session, user_id, lock=True)
    if owner is None:
        raise ValueError("missing")
    debit = float(amount if debit_amount is None else debit_amount)
    if not 0 < debit <= 100_000_000 or not 0 <= float(amount) <= debit or status not in {"pending", "approved"}:
        raise ValueError("amount")
    result = await session.execute(
        text(
            "UPDATE users SET partner_balance = COALESCE(partner_balance, 0) - :amount WHERE id = :id AND COALESCE(partner_balance, 0) >= :amount RETURNING partner_balance"
        ),
        {"id": int(user_id), "amount": debit},
    )
    balance = result.scalar()
    if balance is None:
        raise ValueError("balance")
    payout_id = (
        await session.execute(
            text(
                "INSERT INTO payout_requests (user_id, tg_id, amount, reserved_amount, status, created_at, method, destination) VALUES (:user_id, :tg_id, :amount, :reserved_amount, :status, CURRENT_TIMESTAMP, :method, :destination) RETURNING id"
            ),
            {
                "user_id": int(user_id),
                "tg_id": owner["tg_id"],
                "amount": amount,
                "reserved_amount": debit,
                "status": status,
                "method": method,
                "destination": destination,
            },
        )
    ).scalar_one()
    return int(payout_id), float(balance)


async def decide_partner_payout(session: AsyncSession, payout_id: int, approve: bool) -> bool:
    """Обрабатывает заявку однократно и возвращает резерв при отказе."""
    row = (
        (
            await session.execute(
                text(f"WITH payouts AS ({PAYOUT_OWNERS}) SELECT * FROM payouts WHERE id = :id AND status = 'pending'"),
                {"id": payout_id},
            )
        )
        .mappings()
        .first()
    )
    if not row or row["owner_id"] is None:
        return False
    owner = await get_partner_profile(session, row["owner_id"], lock=True)
    if owner is None:
        return False
    updated = await session.execute(
        text(
            "UPDATE payout_requests SET status = :status, method = COALESCE(method, :method), "
            "destination = COALESCE(destination, :destination) WHERE id = :id AND status = 'pending' "
            "AND (user_id = :owner OR (user_id IS NULL AND tg_id = :tg)) RETURNING COALESCE(reserved_amount, amount)"
        ),
        {
            "id": payout_id,
            "owner": owner["id"],
            "tg": owner["tg_id"],
            "status": "approved" if approve else "rejected",
            "method": owner["payout_method"],
            "destination": owner["card_number"],
        },
    )
    amount = updated.scalar()
    if amount is None:
        return False
    if not approve:
        await session.execute(
            text("UPDATE users SET partner_balance = COALESCE(partner_balance, 0) + :amount WHERE id = :id"),
            {"id": owner["id"], "amount": float(amount)},
        )
    return True


async def freeze_partner_owners(session: AsyncSession, user_id: int) -> None:
    """Закрепляет старые TG-связи за текущим ID клиента до отвязки."""
    if not await partner_schema_available(session):
        return
    for table, pairs in PARTNER_OWNER_COLUMNS.items():
        for owner, mirror in pairs:
            await session.execute(
                text(
                    f'UPDATE "{table}" SET "{owner}" = :id WHERE "{owner}" IS NULL AND "{mirror}" = (SELECT tg_id FROM users WHERE id = :id)'
                ),
                {"id": int(user_id)},
            )


async def refresh_partner_tg_mirrors(session: AsyncSession, user_id: int) -> None:
    """Обновляет TG-зеркала партнёрских записей только по ID владельца."""
    if not await partner_schema_available(session):
        return
    for table, pairs in PARTNER_OWNER_COLUMNS.items():
        for owner, mirror in pairs:
            await session.execute(
                text(
                    f'UPDATE "{table}" SET "{mirror}" = (SELECT tg_id FROM users WHERE id = :id) WHERE "{owner}" = :id'
                ),
                {"id": int(user_id)},
            )


async def transfer_partner_user_data(session: AsyncSession, src_user_id: int, dst_user_id: int) -> None:
    """Переносит партнёрские связи и баланс между точными ID клиентов."""
    if int(src_user_id) == int(dst_user_id) or not await partner_schema_available(session):
        return
    src, dst = int(src_user_id), int(dst_user_id)
    profiles = {}
    for user_id in sorted((src, dst)):
        profiles[user_id] = await get_partner_profile(session, user_id, lock=True)
    source, target = profiles[src], profiles[dst]
    if not source or not target:
        raise ValueError("Partner account disappeared during merge")
    await freeze_partner_owners(session, src)
    await freeze_partner_owners(session, dst)
    await session.execute(
        text(
            "DELETE FROM partners WHERE (joined_user_id = :src AND partner_user_id = :dst) OR (joined_user_id = :dst AND partner_user_id = :src)"
        ),
        {"src": src, "dst": dst},
    )
    if await get_partner_link(session, dst):
        await session.execute(text("DELETE FROM partners WHERE joined_user_id = :src"), {"src": src})
    for table, pairs in PARTNER_OWNER_COLUMNS.items():
        for owner, _mirror in pairs:
            await session.execute(
                text(f'UPDATE "{table}" SET "{owner}" = :dst WHERE "{owner}" = :src'), {"src": src, "dst": dst}
            )
    await session.execute(text("DELETE FROM partners WHERE partner_user_id = joined_user_id"))
    values = {"partner_balance": float(target["partner_balance"] or 0) + float(source["partner_balance"] or 0)}
    await update_partner_profile(session, src, partner_balance=0)
    if source["partner_code"] and not target["partner_code"]:
        await update_partner_profile(session, src, partner_code=None)
        values["partner_code"] = source["partner_code"]
    if source["card_number"] and not target["card_number"]:
        values.update(payout_method=source["payout_method"], card_number=source["card_number"])
    if source["partner_percent_custom"] and not target["partner_percent_custom"]:
        values.update(partner_percent=source["partner_percent"], partner_percent_custom=True)
    await update_partner_profile(session, dst, **values)
    await refresh_partner_tg_mirrors(session, dst)


async def record_partner_transaction(
    session: AsyncSession,
    user_id: int,
    amount: float,
    op_type: str,
    *,
    actor_user_id: int | None = None,
    actor_tg_id: int | None = None,
) -> int:
    """Записывает партнёрскую операцию с каноническими владельцами."""
    owner = await get_partner_profile(session, user_id)
    actor = await get_partner_profile(session, actor_user_id) if actor_user_id is not None else None
    if owner is None or (actor_user_id is not None and actor is None):
        raise ValueError("Partner transaction owner is missing")
    row = await session.execute(
        text(
            "INSERT INTO partner_tx (user_id, tg_id, amount, op_type, actor_user_id, actor_tg_id) VALUES (:user_id, :tg_id, :amount, :op_type, :actor_user_id, :actor_tg_id) RETURNING id"
        ),
        {
            "user_id": int(user_id),
            "tg_id": owner["tg_id"],
            "amount": float(amount),
            "op_type": str(op_type),
            "actor_user_id": int(actor_user_id) if actor_user_id is not None else None,
            "actor_tg_id": actor["tg_id"] if actor else actor_tg_id,
        },
    )
    return int(row.scalar_one())


async def list_partner_transactions(session: AsyncSession, user_id: int, *, page: int = 1, page_size: int = 5) -> dict:
    """Возвращает страницу операций только выбранного ID клиента."""
    page, page_size = max(1, int(page)), max(1, int(page_size))
    owner = await get_partner_profile(session, user_id)
    if owner is None:
        return {"total": 0, "page": 1, "last_page": 1, "items": []}
    where = "WHERE user_id = :id OR (user_id IS NULL AND tg_id = :tg)"
    params = {"id": int(user_id), "tg": owner["tg_id"]}
    total = int((await session.execute(text(f"SELECT COUNT(*) FROM partner_tx {where}"), params)).scalar() or 0)
    last_page = max(1, (total + page_size - 1) // page_size)
    page = min(page, last_page)
    rows = (
        (
            await session.execute(
                text(f"SELECT * FROM partner_tx {where} ORDER BY created_at DESC, id DESC LIMIT :limit OFFSET :offset"),
                {**params, "limit": page_size, "offset": (page - 1) * page_size},
            )
        )
        .mappings()
        .all()
    )
    return {"total": total, "page": page, "last_page": last_page, "items": [dict(row) for row in rows]}


async def delete_partner_user_data(session: AsyncSession, user_id: int) -> None:
    """Снимает владельца истории и связи перед удалением клиента."""
    if not await partner_schema_available(session):
        return
    await freeze_partner_owners(session, user_id)
    await session.execute(
        text("DELETE FROM partners WHERE partner_user_id = :id OR joined_user_id = :id"), {"id": int(user_id)}
    )
    for table in ("payout_requests", "partner_tx"):
        await session.execute(
            text(f'UPDATE "{table}" SET user_id = NULL, tg_id = NULL WHERE user_id = :id'), {"id": int(user_id)}
        )
    await session.execute(
        text("UPDATE partner_tx SET actor_user_id = NULL, actor_tg_id = NULL WHERE actor_user_id = :id"),
        {"id": int(user_id)},
    )


async def credit_partner_balance(session: AsyncSession, user_id: int, amount: float) -> float:
    """Начисляет партнёрский бонус только каноническому владельцу."""
    result = await session.execute(
        text(
            "UPDATE users SET partner_balance = COALESCE(partner_balance, 0) + :amount WHERE id = :id RETURNING partner_balance"
        ),
        {"id": int(user_id), "amount": float(amount)},
    )
    balance = result.scalar()
    if balance is None:
        raise ValueError("Partner account disappeared during accrual")
    return float(balance)


async def count_partner_paid_payments(session: AsyncSession, user_id: int, excluded_providers: tuple[str, ...]) -> int:
    """Считает реальные оплаты клиента без захвата чужого TG-зеркала."""
    owner = await get_partner_profile(session, user_id)
    if owner is None:
        return 0
    params = {"id": int(user_id), "tg": owner["tg_id"]}
    exclusion = ""
    if excluded_providers:
        placeholders = ", ".join(f":excluded_{i}" for i in range(len(excluded_providers)))
        params.update({f"excluded_{i}": provider for i, provider in enumerate(excluded_providers)})
        exclusion = f" AND lower(COALESCE(payment_system, '')) NOT IN ({placeholders})"
    result = await session.execute(
        text(
            "SELECT COUNT(*) FROM payments WHERE (user_id = :id OR (user_id IS NULL AND tg_id = :tg)) AND status IN ('success', 'paid')"
            + exclusion
        ),
        params,
    )
    return int(result.scalar() or 0)


async def get_partner_payout(session: AsyncSession, payout_id: int) -> dict | None:
    """Возвращает заявку и её точного текущего владельца."""
    row = (
        (
            await session.execute(
                text(f"WITH payouts AS ({PAYOUT_OWNERS}) SELECT * FROM payouts WHERE id = :id"), {"id": int(payout_id)}
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def get_partner_top(session: AsyncSession, *, mode: str = "balance", limit: int = 5) -> list[dict]:
    """Возвращает топ партнёров по балансу или приглашениям."""
    order = (
        "referred_count DESC, partner_balance DESC, u.id"
        if mode == "referrals"
        else "partner_balance DESC, referred_count DESC, u.id"
    )
    rows = (
        (
            await session.execute(
                text(f"""
        WITH links AS ({PARTNER_LINKS}), ranked AS (
            SELECT partner_user_id, COUNT(DISTINCT joined_user_id) AS referred_count
            FROM links WHERE partner_user_id IS NOT NULL GROUP BY partner_user_id
        ) SELECT u.id AS user_id, u.tg_id, COALESCE(u.partner_balance, 0) AS partner_balance,
                 COALESCE(r.referred_count, 0) AS referred_count
        FROM users u LEFT JOIN ranked r ON r.partner_user_id = u.id
        WHERE COALESCE(u.partner_balance, 0) > 0 OR COALESCE(r.referred_count, 0) > 0
        ORDER BY {order} LIMIT :limit
    """),
                {"limit": int(limit)},
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


async def get_partner_summary(session: AsyncSession, user_id: int) -> dict:
    """Считает приглашённых и оплативших для сводки кабинета."""
    row = (
        (
            await session.execute(
                text(f"""
        WITH links AS ({PARTNER_LINKS})
        SELECT COUNT(DISTINCT pr.joined_user_id) AS referred_count,
            COUNT(DISTINCT CASE WHEN EXISTS (
                SELECT 1 FROM payments pay
                WHERE (pay.user_id = u.id OR (pay.user_id IS NULL AND pay.tg_id = u.tg_id))
                    AND lower(pay.status) = 'success'
            ) THEN pr.joined_user_id END) AS paid_count
        FROM links pr LEFT JOIN users u ON u.id = pr.joined_user_id
        WHERE pr.partner_user_id = :id
    """),
                {"id": int(user_id)},
            )
        )
        .mappings()
        .one()
    )
    return {"referred_count": int(row["referred_count"] or 0), "paid_count": int(row["paid_count"] or 0)}


async def get_partner_funnel(session: AsyncSession, user_id: int) -> dict:
    """Считает приглашённых, триалы и оплаты по каноническому владельцу."""
    row = (
        (
            await session.execute(
                text(f"""
        WITH links AS ({PARTNER_LINKS})
        SELECT COUNT(DISTINCT pr.joined_user_id) AS invited_count,
            COUNT(DISTINCT CASE WHEN u.trial > 0 THEN pr.joined_user_id END) AS trial_count,
            COUNT(DISTINCT CASE WHEN EXISTS (
                SELECT 1 FROM payments pay
                WHERE (pay.user_id = u.id OR (pay.user_id IS NULL AND pay.tg_id = u.tg_id))
                    AND lower(pay.status) IN ('success', 'paid')
            ) THEN pr.joined_user_id END) AS paid_count
        FROM links pr LEFT JOIN users u ON u.id = pr.joined_user_id
        WHERE pr.partner_user_id = :id
    """),
                {"id": int(user_id)},
            )
        )
        .mappings()
        .one()
    )
    return {key: int(row[key] or 0) for key in ("invited_count", "trial_count", "paid_count")}

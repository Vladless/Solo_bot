from datetime import datetime
import csv
from io import StringIO

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_session, verify_admin_token

try:
    from modules.partner_program.settings import PARTNER_BONUS_PERCENTAGES
except Exception:
    PARTNER_BONUS_PERCENTAGES = {1: 0.0}


router = APIRouter()


def _parse_percent(value: float) -> float | None:
    """Normalize percent input to 0-100 range."""
    try:
        val = float(value)
    except (TypeError, ValueError):
        return None

    if 0.0 <= val <= 1.0:
        val *= 100.0

    if 0.0 <= val <= 100.0:
        return val
    return None


def _default_partner_percent() -> float:
    try:
        return float(PARTNER_BONUS_PERCENTAGES.get(1, 0.0)) * 100.0
    except Exception:
        return 0.0


def _row_dt_iso(value) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return None


@router.get("/all")
async def get_all_partners(
    limit: int = Query(1000, ge=1, le=10000, description="Лимит результатов"),
    offset: int = Query(0, ge=0, description="Смещение"),
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    """Возвращает список всех партнёров со статистикой.

    Структура ответа:
    {
      "total": int,
      "items": [
        {
          "tg_id": int,
          "balance": float,
          "percent": float,
          "code": str | None,
          "method": str | None,
          "referred_count": int
        }
      ]
    }
    """

    partners_sql = text(
        """
        SELECT 
            p.partner_tg_id AS tg_id,
            COALESCE(u.partner_balance, 0) AS partner_balance,
            u.partner_percent,
            COALESCE(u.partner_percent_custom, false) AS partner_percent_custom,
            u.partner_code,
            u.payout_method,
            COUNT(p.joined_tg_id) as joined_count
        FROM partners p
        LEFT JOIN users u ON u.tg_id = p.partner_tg_id
        WHERE p.partner_tg_id IS NOT NULL
        GROUP BY p.partner_tg_id, u.partner_balance, u.partner_percent, u.partner_percent_custom, u.partner_code, u.payout_method
        ORDER BY partner_balance DESC
        LIMIT :limit OFFSET :offset
        """
    )

    count_sql = text(
        """
        SELECT COUNT(DISTINCT partner_tg_id) FROM partners
        WHERE partner_tg_id IS NOT NULL
        """
    )

    result = await session.execute(partners_sql, {"limit": limit, "offset": offset})
    partners = result.fetchall()

    count_result = await session.execute(count_sql)
    total = count_result.scalar() or 0

    partners_list = []
    default_percent = _default_partner_percent()
    for partner in partners:
        percent_value = partner[2]
        percent_custom = bool(partner[3])
        if percent_custom and percent_value is not None:
            percent = float(percent_value)
        else:
            percent = float(default_percent)

        partners_list.append(
            {
                "tg_id": int(partner[0]),
                "balance": float(partner[1] or 0),
                "percent": percent,
                "code": partner[4] or None,
                "method": partner[5] or None,
                "referred_count": int(partner[6] or 0),
            }
        )

    return JSONResponse(content={"total": total, "items": partners_list})


@router.get("/stats/all")
async def get_partners_stats(
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    """Возвращает общую статистику партнёрской программы.

    Структура ответа:
    {
      "total_partners": int,
            "partners_today": int,
      "total_referred": int,
      "total_balance": float,
      "top_partner_tg_id": int,
      "top_partner_refs": int
    }
    """

    stats_sql = text(
        """
        WITH partner_refs AS (
            SELECT partner_tg_id, COUNT(DISTINCT joined_tg_id) AS ref_count
            FROM partners
            WHERE partner_tg_id IS NOT NULL
            GROUP BY partner_tg_id
        )
        SELECT 
            (SELECT COUNT(*) FROM partner_refs) AS total_partners,
            (
                SELECT COUNT(DISTINCT partner_tg_id)
                FROM partners
                WHERE partner_tg_id IS NOT NULL
                  AND DATE(created_at) = CURRENT_DATE
            ) AS partners_today,
            (SELECT COUNT(DISTINCT joined_tg_id) FROM partners WHERE partner_tg_id IS NOT NULL) AS total_referred,
            (
                SELECT COALESCE(SUM(u.partner_balance), 0.0)
                FROM users u
                WHERE u.tg_id IN (SELECT partner_tg_id FROM partner_refs)
            ) AS total_balance,
            (SELECT partner_tg_id FROM partner_refs ORDER BY ref_count DESC LIMIT 1) AS top_partner_tg_id,
            (SELECT ref_count FROM partner_refs ORDER BY ref_count DESC LIMIT 1) AS top_partner_refs
        """
    )

    stats_result = await session.execute(stats_sql)
    stats_row = stats_result.fetchone()

    if stats_row:
        stats = {
            "total_partners": int(stats_row[0] or 0),
            "partners_today": int(stats_row[1] or 0),
            "total_referred": int(stats_row[2] or 0),
            "total_balance": float(stats_row[3] or 0.0),
            "top_partner_tg_id": int(stats_row[4] or 0),
            "top_partner_refs": int(stats_row[5] or 0),
        }
    else:
        stats = {
            "total_partners": 0,
            "partners_today": 0,
            "total_referred": 0,
            "total_balance": 0.0,
            "top_partner_tg_id": 0,
            "top_partner_refs": 0,
        }

    return JSONResponse(content=stats)


@router.patch("/{tg_id}")
async def update_partner(
    tg_id: int = Path(..., description="Telegram ID партнёра"),
    balance: float = Query(..., description="Новый баланс партнёра"),
    percent: float = Query(..., description="Новый процент партнёра"),
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    """Обновляет данные партнёра (баланс и процент).

    Структура ответа:
    {
      "success": bool,
      "message": str
    }
    """

    try:
        stmt = text(
            """
            UPDATE users 
            SET partner_balance = :balance, partner_percent = :percent 
            WHERE tg_id = :tg_id
            """
        )

        result = await session.execute(stmt, {"tg_id": tg_id, "balance": balance, "percent": percent})
        await session.commit()

        if result.rowcount > 0:
            return JSONResponse(
                content={"success": True, "message": f"Партнёр {tg_id} успешно обновлён"},
                status_code=200,
            )
        else:
            return JSONResponse(
                content={"success": False, "message": "Партнёр не найден"},
                status_code=404,
            )
    except Exception as e:
        await session.rollback()
        return JSONResponse(
            content={"success": False, "message": str(e)},
            status_code=500,
        )


@router.get("/{tg_id}")
async def get_partner_data(
    tg_id: int = Path(..., description="Telegram ID партнёра"),
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    """Возвращает партнёрские данные для указанного `tg_id`.

    Структура ответа:
    {
      "tg_id": int,
      "partner_balance": float,
      "partner_percent": float,
      "partner_code": str | None,
      "payout_method": str | None,
      "invited": [
        { "tg_id": int, "joined_at": str | None, "balance": float, "subs_count": int, "payments_count": int }
      ]
    }
    """

    meta_sql = text(
        """
        SELECT 
            COALESCE(u.partner_balance, 0) AS partner_balance,
            u.partner_percent,
            COALESCE(u.partner_percent_custom, false) AS partner_percent_custom,
            u.partner_code,
            u.payout_method
        FROM users u
        WHERE u.tg_id = :tg_id
        """
    )

    invited_sql = text(
        """
        SELECT 
            pr.joined_tg_id,
            pr.created_at,
            COALESCE(u.balance, 0) AS user_balance,
            (
                SELECT COUNT(*) FROM keys k 
                WHERE k.tg_id = pr.joined_tg_id
            ) AS subs_count,
            (
                SELECT COUNT(*) FROM payments pay 
                WHERE pay.tg_id = pr.joined_tg_id 
                  AND lower(pay.status) = 'success'
            ) AS payments_count
        FROM partners pr
        LEFT JOIN users u ON u.tg_id = pr.joined_tg_id
        WHERE pr.partner_tg_id = :tg_id
        ORDER BY pr.created_at DESC
        """
    )

    meta_res = await session.execute(meta_sql, {"tg_id": tg_id})
    meta_row = meta_res.fetchone()

    invited_res = await session.execute(invited_sql, {"tg_id": tg_id})
    invited_rows = invited_res.fetchall()

    default_percent = _default_partner_percent()
    percent = default_percent
    if meta_row:
        percent_value = meta_row[1]
        percent_custom = bool(meta_row[2])
        if percent_custom and percent_value is not None:
            percent = float(percent_value)

    response = {
        "tg_id": tg_id,
        "partner_balance": float(meta_row[0] or 0) if meta_row else 0.0,
        "partner_percent": percent,
        "partner_code": meta_row[3] if meta_row else None,
        "payout_method": meta_row[4] if meta_row else None,
        "invited": [
            {
                "tg_id": row[0],
                "joined_at": row[1].isoformat() if isinstance(row[1], datetime) else None,
                "balance": float(row[2] or 0),
                "subs_count": int(row[3] or 0),
                "payments_count": int(row[4] or 0),
            }
            for row in invited_rows
        ],
    }

    return JSONResponse(content=response)


@router.post("/{tg_id}/invited")
async def add_partner_invited(
    tg_id: int = Path(..., description="Telegram ID партнёра"),
    joined_tg_id: int = Query(..., description="Telegram ID приглашённого"),
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    """Добавляет приглашённого пользователю партнёра."""

    if joined_tg_id == tg_id:
        return JSONResponse(
            content={"success": False, "message": "Нельзя привязать пользователя к самому себе"},
            status_code=400,
        )

    try:
        partner_exists = await session.execute(
            text("SELECT 1 FROM users WHERE tg_id = :tg_id"),
            {"tg_id": tg_id},
        )
        if not partner_exists.scalar():
            return JSONResponse(
                content={"success": False, "message": "Партнёр не найден"},
                status_code=404,
            )

        invited_exists = await session.execute(
            text("SELECT 1 FROM users WHERE tg_id = :joined_tg_id"),
            {"joined_tg_id": joined_tg_id},
        )
        if not invited_exists.scalar():
            return JSONResponse(
                content={"success": False, "message": "Приглашённый пользователь не найден"},
                status_code=404,
            )

        existing = await session.execute(
            text("SELECT partner_tg_id FROM partners WHERE joined_tg_id = :joined_tg_id"),
            {"joined_tg_id": joined_tg_id},
        )
        existing_partner = existing.scalar()
        if existing_partner is not None:
            return JSONResponse(
                content={
                    "success": False,
                    "message": f"Пользователь уже привязан к партнёру {existing_partner}",
                },
                status_code=409,
            )

        await session.execute(
            text(
                """
                INSERT INTO partners (partner_tg_id, joined_tg_id)
                VALUES (:partner_tg_id, :joined_tg_id)
                """
            ),
            {"partner_tg_id": tg_id, "joined_tg_id": joined_tg_id},
        )
        await session.commit()
        return JSONResponse(
            content={
                "success": True,
                "message": "Приглашённый добавлен",
                "partner_tg_id": tg_id,
                "joined_tg_id": joined_tg_id,
            },
            status_code=201,
        )
    except Exception as e:
        await session.rollback()
        return JSONResponse(content={"success": False, "message": str(e)}, status_code=500)


@router.delete("/{tg_id}/invited/{joined_tg_id}")
async def delete_partner_invited(
    tg_id: int = Path(..., description="Telegram ID партнёра"),
    joined_tg_id: int = Path(..., description="Telegram ID приглашённого"),
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    """Удаляет приглашённого у партнёра."""

    try:
        result = await session.execute(
            text(
                """
                DELETE FROM partners
                WHERE partner_tg_id = :partner_tg_id
                  AND joined_tg_id = :joined_tg_id
                """
            ),
            {"partner_tg_id": tg_id, "joined_tg_id": joined_tg_id},
        )
        await session.commit()

        if result.rowcount > 0:
            return JSONResponse(
                content={
                    "success": True,
                    "message": "Приглашённый удалён",
                    "partner_tg_id": tg_id,
                    "joined_tg_id": joined_tg_id,
                },
                status_code=200,
            )
        return JSONResponse(
            content={"success": False, "message": "Связка партнёр-приглашённый не найдена"},
            status_code=404,
        )
    except Exception as e:
        await session.rollback()
        return JSONResponse(content={"success": False, "message": str(e)}, status_code=500)


@router.patch("/{tg_id}/percent")
async def update_partner_percent(
    tg_id: int = Path(..., description="Telegram ID партнёра"),
    percent: float = Query(..., description="Новый персональный процент (0-100 или 0.0-1.0)"),
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    """Обновляет персональный процент партнёра."""

    normalized = _parse_percent(percent)
    if normalized is None:
        return JSONResponse(
            content={"success": False, "message": "Неверный процент. Допустимо 0-100 или 0.0-1.0"},
            status_code=400,
        )

    try:
        result = await session.execute(
            text(
                """
                UPDATE users
                SET partner_percent = :percent, partner_percent_custom = true
                WHERE tg_id = :tg_id
                """
            ),
            {"tg_id": tg_id, "percent": normalized},
        )
        await session.commit()

        if result.rowcount > 0:
            return JSONResponse(
                content={"success": True, "message": "Процент обновлён", "percent": normalized},
                status_code=200,
            )
        return JSONResponse(content={"success": False, "message": "Партнёр не найден"}, status_code=404)
    except Exception as e:
        await session.rollback()
        return JSONResponse(content={"success": False, "message": str(e)}, status_code=500)


@router.patch("/{tg_id}/balance")
async def update_partner_balance(
    tg_id: int = Path(..., description="Telegram ID партнёра"),
    amount: float = Query(..., description="Сумма операции"),
    mode: str = Query("set", description="Режим: set, add, subtract"),
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    """Изменяет баланс партнёрской программы."""

    mode_normalized = (mode or "set").strip().lower()
    if mode_normalized not in {"set", "add", "subtract"}:
        return JSONResponse(
            content={"success": False, "message": "Неверный режим. Используйте set, add или subtract"},
            status_code=400,
        )

    try:
        amount_val = float(amount)
    except (TypeError, ValueError):
        return JSONResponse(
            content={"success": False, "message": "Неверная сумма"},
            status_code=400,
        )

    if amount_val < 0:
        return JSONResponse(
            content={"success": False, "message": "Сумма не может быть отрицательной"},
            status_code=400,
        )

    try:
        current_res = await session.execute(
            text("SELECT partner_balance FROM users WHERE tg_id = :tg_id"),
            {"tg_id": tg_id},
        )
        current_balance = current_res.scalar()
        if current_balance is None:
            return JSONResponse(content={"success": False, "message": "Партнёр не найден"}, status_code=404)

        current_balance = float(current_balance or 0.0)

        if mode_normalized == "set":
            new_balance = amount_val
        elif mode_normalized == "add":
            new_balance = current_balance + amount_val
        else:
            if current_balance < amount_val:
                return JSONResponse(
                    content={"success": False, "message": "Недостаточно средств"},
                    status_code=400,
                )
            new_balance = current_balance - amount_val

        await session.execute(
            text("UPDATE users SET partner_balance = :balance WHERE tg_id = :tg_id"),
            {"tg_id": tg_id, "balance": new_balance},
        )
        await session.commit()

        return JSONResponse(
            content={"success": True, "message": "Баланс обновлён", "balance": new_balance},
            status_code=200,
        )
    except Exception as e:
        await session.rollback()
        return JSONResponse(content={"success": False, "message": str(e)}, status_code=500)


@router.get("/{tg_id}/invited")
async def get_partner_invited(
    tg_id: int = Path(..., description="Telegram ID партнёра"),
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    """Возвращает список приглашённых пользователей конкретного партнёра.

    Структура ответа:
    [
      { "tg_id": int, "joined_at": str | None, "balance": float, "subs_count": int, "payments_count": int }
    ]
    """

    invited_sql = text(
        """
        SELECT 
            pr.joined_tg_id,
            pr.created_at,
            COALESCE(u.balance, 0) AS user_balance,
            (
                SELECT COUNT(*) FROM keys k 
                WHERE k.tg_id = pr.joined_tg_id
            ) AS subs_count,
            (
                SELECT COUNT(*) FROM payments pay 
                WHERE pay.tg_id = pr.joined_tg_id 
                  AND lower(pay.status) = 'success'
            ) AS payments_count
        FROM partners pr
        LEFT JOIN users u ON u.tg_id = pr.joined_tg_id
        WHERE pr.partner_tg_id = :tg_id
        ORDER BY pr.created_at DESC
        """
    )

    invited_res = await session.execute(invited_sql, {"tg_id": tg_id})
    invited_rows = invited_res.fetchall()

    invited_list = [
        {
            "tg_id": row[0],
            "joined_at": row[1].isoformat() if isinstance(row[1], datetime) else None,
            "balance": float(row[2] or 0),
            "subs_count": int(row[3] or 0),
            "payments_count": int(row[4] or 0),
        }
        for row in invited_rows
    ]

    return JSONResponse(content=invited_list)


@router.get("/payouts/pending")
async def get_partner_payouts_pending(
    limit: int = Query(50, ge=1, le=200, description="Лимит результатов"),
    offset: int = Query(0, ge=0, description="Смещение"),
    partner_tg_id: int | None = Query(None, description="Фильтр по TG ID партнёра"),
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    """Возвращает список ожидающих заявок на вывод."""

    where_clause = "WHERE pr.status = 'pending'"
    params = {"limit": limit, "offset": offset}
    if partner_tg_id is not None:
        where_clause += " AND pr.tg_id = :partner_tg_id"
        params["partner_tg_id"] = partner_tg_id

    count_sql = text(f"SELECT COUNT(*) FROM payout_requests pr {where_clause}")
    rows_sql = text(
        f"""
        SELECT
            pr.id,
            pr.tg_id,
            pr.amount,
            pr.status,
            pr.created_at,
            COALESCE(pr.method, u.payout_method) AS method,
            COALESCE(pr.destination, u.card_number) AS destination
        FROM payout_requests pr
        LEFT JOIN users u ON u.tg_id = pr.tg_id
        {where_clause}
        ORDER BY pr.created_at ASC, pr.id ASC
        LIMIT :limit OFFSET :offset
        """
    )

    total = await session.scalar(count_sql) or 0
    result = await session.execute(rows_sql, params)
    items = []
    for row in result.fetchall():
        items.append(
            {
                "id": int(row[0]),
                "tg_id": int(row[1]),
                "amount": float(row[2] or 0.0),
                "status": row[3] or "pending",
                "created_at": _row_dt_iso(row[4]),
                "method": row[5] or None,
                "destination": row[6] or None,
            }
        )

    return JSONResponse(content={"total": int(total), "items": items})


@router.get("/payouts/history")
async def get_partner_payouts_history(
    limit: int = Query(50, ge=1, le=200, description="Лимит результатов"),
    offset: int = Query(0, ge=0, description="Смещение"),
    partner_tg_id: int | None = Query(None, description="Фильтр по TG ID партнёра"),
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    """Возвращает историю выплат (approved/rejected)."""

    where_clause = "WHERE pr.status IN ('approved','rejected')"
    params = {"limit": limit, "offset": offset}
    if partner_tg_id is not None:
        where_clause += " AND pr.tg_id = :partner_tg_id"
        params["partner_tg_id"] = partner_tg_id

    count_sql = text(f"SELECT COUNT(*) FROM payout_requests pr {where_clause}")
    rows_sql = text(
        f"""
        SELECT
            pr.id,
            pr.tg_id,
            pr.amount,
            pr.status,
            pr.created_at,
            COALESCE(pr.method, u.payout_method) AS method,
            COALESCE(pr.destination, u.card_number) AS destination
        FROM payout_requests pr
        LEFT JOIN users u ON u.tg_id = pr.tg_id
        {where_clause}
        ORDER BY pr.created_at DESC, pr.id DESC
        LIMIT :limit OFFSET :offset
        """
    )

    total = await session.scalar(count_sql) or 0
    result = await session.execute(rows_sql, params)
    items = []
    for row in result.fetchall():
        items.append(
            {
                "id": int(row[0]),
                "tg_id": int(row[1]),
                "amount": float(row[2] or 0.0),
                "status": row[3] or "—",
                "created_at": _row_dt_iso(row[4]),
                "method": row[5] or None,
                "destination": row[6] or None,
            }
        )

    return JSONResponse(content={"total": int(total), "items": items})


@router.post("/payouts/{payout_id}/approve")
async def approve_partner_payout(
    payout_id: int = Path(..., description="ID заявки"),
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    """Одобряет заявку на вывод."""

    req_row = await session.execute(
        text("SELECT id, tg_id, amount FROM payout_requests WHERE id = :id AND status = 'pending'"),
        {"id": payout_id},
    )
    req = req_row.fetchone()
    if not req:
        return JSONResponse(
            content={"success": False, "message": "Заявка не найдена или уже обработана"},
            status_code=404,
        )

    user_row = await session.execute(
        text("SELECT payout_method, card_number FROM users WHERE tg_id = :tg_id"),
        {"tg_id": req[1]},
    )
    user = user_row.fetchone()
    payout_method = (user[0] if user else None) or "card"
    destination = (user[1] if user else None) or None
    destination = (destination or "").strip() or None

    await session.execute(
        text(
            """
            UPDATE payout_requests
            SET status = 'approved', method = :method, destination = :destination
            WHERE id = :id
            """
        ),
        {"id": payout_id, "method": payout_method, "destination": destination},
    )
    await session.commit()

    return JSONResponse(content={"success": True, "message": "Заявка одобрена"}, status_code=200)


@router.post("/payouts/{payout_id}/reject")
async def reject_partner_payout(
    payout_id: int = Path(..., description="ID заявки"),
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    """Отклоняет заявку на вывод и возвращает сумму на баланс."""

    req_row = await session.execute(
        text("SELECT id, tg_id, amount FROM payout_requests WHERE id = :id AND status = 'pending'"),
        {"id": payout_id},
    )
    req = req_row.fetchone()
    if not req:
        return JSONResponse(
            content={"success": False, "message": "Заявка не найдена или уже обработана"},
            status_code=404,
        )

    user_row = await session.execute(
        text("SELECT payout_method, card_number, partner_balance FROM users WHERE tg_id = :tg_id"),
        {"tg_id": req[1]},
    )
    user = user_row.fetchone()
    payout_method = (user[0] if user else None) or "card"
    destination = (user[1] if user else None) or None
    destination = (destination or "").strip() or None

    await session.execute(
        text(
            """
            UPDATE payout_requests
            SET status = 'rejected', method = :method, destination = :destination
            WHERE id = :id
            """
        ),
        {"id": payout_id, "method": payout_method, "destination": destination},
    )

    if user is not None:
        current_balance = float(user[2] or 0.0)
        await session.execute(
            text("UPDATE users SET partner_balance = :balance WHERE tg_id = :tg_id"),
            {"balance": current_balance + float(req[2] or 0.0), "tg_id": req[1]},
        )

    await session.commit()

    return JSONResponse(content={"success": True, "message": "Заявка отклонена"}, status_code=200)


@router.patch("/{tg_id}/percent/reset")
async def reset_partner_percent(
    tg_id: int = Path(..., description="Telegram ID партнёра"),
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    """Сбрасывает персональный процент партнёра к дефолту."""

    result = await session.execute(
        text(
            """
            UPDATE users
            SET partner_percent = NULL, partner_percent_custom = false
            WHERE tg_id = :tg_id
            """
        ),
        {"tg_id": tg_id},
    )
    await session.commit()

    if result.rowcount > 0:
        return JSONResponse(content={"success": True, "message": "Процент сброшен"}, status_code=200)
    return JSONResponse(content={"success": False, "message": "Партнёр не найден"}, status_code=404)


@router.patch("/{tg_id}/code")
async def update_partner_code(
    tg_id: int = Path(..., description="Telegram ID партнёра"),
    code: str = Query(..., description="Новый код партнёра (латиница/цифры/_)"),
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    """Обновляет код партнёрской ссылки."""

    raw = (code or "").strip().lower()
    if not raw:
        return JSONResponse(content={"success": False, "message": "Код не может быть пустым"}, status_code=400)

    import re

    if not re.fullmatch(r"[a-z0-9_]{3,32}", raw):
        return JSONResponse(
            content={"success": False, "message": "Неверный код. Разрешены a-z, 0-9, _ (3-32 символа)"},
            status_code=400,
        )

    exists = await session.execute(
        text("SELECT 1 FROM users WHERE partner_code = :code AND tg_id != :tg_id"),
        {"code": raw, "tg_id": tg_id},
    )
    if exists.first():
        return JSONResponse(content={"success": False, "message": "Такой код уже занят"}, status_code=409)

    result = await session.execute(
        text("UPDATE users SET partner_code = :code WHERE tg_id = :tg_id"),
        {"code": raw, "tg_id": tg_id},
    )
    await session.commit()

    if result.rowcount > 0:
        return JSONResponse(content={"success": True, "message": "Код обновлён", "code": raw}, status_code=200)
    return JSONResponse(content={"success": False, "message": "Партнёр не найден"}, status_code=404)


@router.post("/reset-disabled-methods")
async def reset_disabled_payout_methods(
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    """Сбрасывает реквизиты для отключённых способов вывода."""

    try:
        from modules.partner_program.settings import (
            ENABLE_PAYOUT_CARD,
            ENABLE_PAYOUT_SBP,
            ENABLE_PAYOUT_TON,
            ENABLE_PAYOUT_USDT,
        )
        from modules.partner_program import buttons as B
    except Exception:
        ENABLE_PAYOUT_CARD = True
        ENABLE_PAYOUT_USDT = True
        ENABLE_PAYOUT_TON = True
        ENABLE_PAYOUT_SBP = True
        B = None

    disabled = []
    if not ENABLE_PAYOUT_CARD and B:
        disabled.append(B.METHOD_CARD)
    if not ENABLE_PAYOUT_USDT and B:
        disabled.append(B.METHOD_USDT)
    if not ENABLE_PAYOUT_TON and B:
        disabled.append(B.METHOD_TON)
    if not ENABLE_PAYOUT_SBP and B:
        disabled.append(B.METHOD_SBP)

    if not disabled:
        return JSONResponse(content={"success": True, "message": "Отключённых методов нет"}, status_code=200)

    await session.execute(
        text(
            """
            UPDATE users
            SET card_number = NULL
            WHERE payout_method = ANY(:methods)
            """
        ),
        {"methods": disabled},
    )
    await session.commit()

    return JSONResponse(content={"success": True, "message": "Отключённые методы сброшены"}, status_code=200)


@router.get("/{tg_id}/export")
async def export_partner_invites_csv(
    tg_id: int = Path(..., description="Telegram ID партнёра"),
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    """Экспортирует приглашённых партнёром в CSV."""

    rows = await session.execute(
        text(
            """
            SELECT joined_tg_id, created_at
            FROM partners
            WHERE partner_tg_id = :tg_id
            ORDER BY created_at ASC
            """
        ),
        {"tg_id": tg_id},
    )
    data = rows.fetchall()

    if not data:
        return JSONResponse(content={"success": False, "message": "Нет приглашённых"}, status_code=404)

    buffer = StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["joined_tg_id", "created_at"])
    for joined_tg_id, created_at in data:
        writer.writerow([int(joined_tg_id), created_at.isoformat() if created_at else ""])

    content = buffer.getvalue().encode("utf-8-sig")
    filename = f"partner_invites_{tg_id}.csv"

    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )

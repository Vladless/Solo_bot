from datetime import datetime

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_session, verify_admin_token


router = APIRouter()


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
            COALESCE(u.partner_percent, 0) AS partner_percent,
            u.partner_code,
            u.payout_method,
            COUNT(p.joined_tg_id) as joined_count
        FROM partners p
        LEFT JOIN users u ON u.tg_id = p.partner_tg_id
        WHERE p.partner_tg_id IS NOT NULL
        GROUP BY p.partner_tg_id, u.partner_balance, u.partner_percent, u.partner_code, u.payout_method
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

    partners_list = [
        {
            "tg_id": int(partner[0]),
            "balance": float(partner[1] or 0),
            "percent": float(partner[2] or 0),
            "code": partner[3] or None,
            "method": partner[4] or None,
            "referred_count": int(partner[5] or 0),
        }
        for partner in partners
    ]

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
            "total_referred": int(stats_row[1] or 0),
            "total_balance": float(stats_row[2] or 0.0),
            "top_partner_tg_id": int(stats_row[3] or 0),
            "top_partner_refs": int(stats_row[4] or 0),
        }
    else:
        stats = {
            "total_partners": 0,
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
            COALESCE(u.partner_percent, 0) AS partner_percent,
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

    response = {
        "tg_id": tg_id,
        "partner_balance": float(meta_row[0] or 0) if meta_row else 0.0,
        "partner_percent": float(meta_row[1] or 0) if meta_row else 0.0,
        "partner_code": meta_row[2] if meta_row else None,
        "payout_method": meta_row[3] if meta_row else None,
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

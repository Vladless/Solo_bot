import csv
import math
import re

from io import StringIO

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import ORJSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_session, verify_admin_token
from api.shared.partners import default_partner_percent, parse_percent, row_dt_iso
from database import partners as pdb
from database.access.resolution import public_tg_id


async def ensure_partner_available(session: AsyncSession = Depends(get_session)) -> None:
    """Проверяет доступность партнёрских данных для старого API."""
    if not await pdb.partner_schema_available(session):
        raise HTTPException(status_code=404, detail="Партнёрская программа недоступна")


def _invited_entry(row: dict) -> dict:
    """Сохраняет поля старого API без подмены Telegram ID."""
    return {
        "tg_id": public_tg_id(row["tg_id"]),
        "joined_at": row_dt_iso(row["created_at"]),
        "balance": float(row["balance"] or 0),
        "subs_count": int(row["keys_count"] or 0),
        "payments_count": int(row["payments_count"] or 0),
    }


def _payout_entry(row: dict, fallback_status: str) -> dict:
    """Сохраняет формат заявки и допускает отсутствие Telegram."""
    return {
        "id": int(row["id"]),
        "tg_id": public_tg_id(row["owner_tg_id"]),
        "amount": float(row["amount"] or 0),
        "status": row["status"] or fallback_status,
        "created_at": row_dt_iso(row["created_at"]),
        "method": row["payout_method"] or None,
        "destination": row["payout_destination"] or None,
    }


router = APIRouter(dependencies=[Depends(ensure_partner_available)])


@router.get("/all")
async def get_all_partners(
    limit: int = Query(1000, ge=1, le=10000, description="Лимит результатов"),
    offset: int = Query(0, ge=0, description="Смещение"),
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    """Возвращает список партнёров со статистикой."""

    total, rows = await pdb.list_partners(session, limit, offset)
    default_percent = default_partner_percent()
    items = [
        {
            "tg_id": public_tg_id(row["tg_id"]),
            "balance": float(row["partner_balance"] or 0),
            "percent": float(row["partner_percent"])
            if row["partner_percent_custom"] and row["partner_percent"] is not None
            else default_percent,
            "code": row["partner_code"] or None,
            "method": row["payout_method"] or None,
            "referred_count": int(row["referred_count"] or 0),
        }
        for row in rows
    ]
    return ORJSONResponse(content={"total": total, "items": items})


@router.get("/stats/all")
async def get_partners_stats(
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    """Возвращает общую статистику партнёрской программы."""

    stats = await pdb.get_partner_stats(session)
    return ORJSONResponse(
        content={
            "total_partners": int(stats["total_partners"] or 0),
            "partners_today": int(stats["partners_today"] or 0),
            "total_referred": int(stats["total_referred"] or 0),
            "total_balance": float(stats["total_balance"] or 0),
            "top_partner_tg_id": public_tg_id(stats["top_partner_tg_id"]) or 0,
            "top_partner_refs": int(stats["top_partner_refs"] or 0),
        }
    )


@router.patch("/{tg_id}")
async def update_partner(
    tg_id: int = Path(..., description="Telegram ID партнёра"),
    balance: float = Query(..., description="Новый баланс партнёра"),
    percent: float = Query(..., description="Новый процент партнёра"),
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    """Обновляет баланс и процент партнёра."""

    try:
        profile = await pdb.resolve_partner_telegram(session, tg_id)
        if profile is None:
            return ORJSONResponse(content={"success": False, "message": "Партнёр не найден"}, status_code=404)
        await pdb.update_partner_profile(session, profile["id"], partner_balance=balance, partner_percent=percent)
        return ORJSONResponse(content={"success": True, "message": f"Партнёр {tg_id} успешно обновлён"})
    except Exception as exc:
        await session.rollback()
        return ORJSONResponse(content={"success": False, "message": str(exc)}, status_code=500)


@router.get("/{tg_id}")
async def get_partner_data(
    tg_id: int = Path(..., description="Telegram ID партнёра"),
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    """Возвращает данные партнёра и приглашённых по Telegram ID."""

    profile = await pdb.resolve_partner_telegram(session, tg_id)
    rows = await pdb.get_partner_invited(session, profile["id"]) if profile else []
    percent = (
        float(profile["partner_percent"])
        if profile and profile["partner_percent_custom"] and profile["partner_percent"] is not None
        else default_partner_percent()
    )
    return ORJSONResponse(
        content={
            "tg_id": tg_id,
            "partner_balance": float(profile["partner_balance"] or 0) if profile else 0.0,
            "partner_percent": percent,
            "partner_code": profile["partner_code"] if profile else None,
            "payout_method": profile["payout_method"] if profile else None,
            "invited": [_invited_entry(row) for row in rows],
        }
    )


@router.post("/{tg_id}/invited")
async def add_partner_invited(
    tg_id: int = Path(..., description="Telegram ID партнёра"),
    joined_tg_id: int = Query(..., description="Telegram ID приглашённого"),
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    """Добавляет приглашённого пользователю партнёра."""

    if joined_tg_id == tg_id:
        return ORJSONResponse(
            content={"success": False, "message": "Нельзя привязать пользователя к самому себе"}, status_code=400
        )
    try:
        partner = await pdb.resolve_partner_telegram(session, tg_id)
        if partner is None:
            return ORJSONResponse(content={"success": False, "message": "Партнёр не найден"}, status_code=404)
        joined = await pdb.resolve_partner_telegram(session, joined_tg_id)
        if joined is None:
            return ORJSONResponse(
                content={"success": False, "message": "Приглашённый пользователь не найден"}, status_code=404
            )
        existing = await pdb.get_partner_link(session, joined["id"])
        if existing:
            existing_tg = public_tg_id(existing["partner_tg_id"])
            message = (
                f"Пользователь уже привязан к партнёру {existing_tg}"
                if existing_tg is not None
                else "Пользователь уже привязан к партнёру"
            )
            return ORJSONResponse(content={"success": False, "message": message}, status_code=409)
        try:
            await pdb.create_partner_link(session, partner["id"], joined["id"])
        except ValueError as exc:
            if str(exc) != "already":
                raise
            return ORJSONResponse(
                content={"success": False, "message": "Пользователь уже привязан к партнёру"}, status_code=409
            )
        return ORJSONResponse(
            content={
                "success": True,
                "message": "Приглашённый добавлен",
                "partner_tg_id": tg_id,
                "joined_tg_id": joined_tg_id,
            },
            status_code=201,
        )
    except Exception as exc:
        await session.rollback()
        return ORJSONResponse(content={"success": False, "message": str(exc)}, status_code=500)


@router.delete("/{tg_id}/invited/{joined_tg_id}")
async def delete_partner_invited(
    tg_id: int = Path(..., description="Telegram ID партнёра"),
    joined_tg_id: int = Path(..., description="Telegram ID приглашённого"),
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    """Удаляет приглашённого у партнёра."""

    try:
        partner = await pdb.resolve_partner_telegram(session, tg_id)
        joined = await pdb.resolve_partner_telegram(session, joined_tg_id)
        deleted = partner and joined and await pdb.delete_partner_link(session, partner["id"], joined["id"])
        if deleted:
            return ORJSONResponse(
                content={
                    "success": True,
                    "message": "Приглашённый удалён",
                    "partner_tg_id": tg_id,
                    "joined_tg_id": joined_tg_id,
                }
            )
        return ORJSONResponse(
            content={"success": False, "message": "Связка партнёр-приглашённый не найдена"}, status_code=404
        )
    except Exception as exc:
        await session.rollback()
        return ORJSONResponse(content={"success": False, "message": str(exc)}, status_code=500)


@router.patch("/{tg_id}/percent")
async def update_partner_percent(
    tg_id: int = Path(..., description="Telegram ID партнёра"),
    percent: float = Query(..., description="Новый персональный процент (0-100 или 0.0-1.0)"),
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    """Обновляет персональный процент партнёра."""

    normalized = parse_percent(percent)
    if normalized is None:
        return ORJSONResponse(
            content={
                "success": False,
                "message": "Неверный процент. Допустимо 0-100 или 0.0-1.0",
            },
            status_code=400,
        )
    try:
        profile = await pdb.resolve_partner_telegram(session, tg_id)
        if profile is None:
            return ORJSONResponse(content={"success": False, "message": "Партнёр не найден"}, status_code=404)
        await pdb.update_partner_profile(
            session, profile["id"], partner_percent=normalized, partner_percent_custom=True
        )
        return ORJSONResponse(content={"success": True, "message": "Процент обновлён", "percent": normalized})
    except Exception as exc:
        await session.rollback()
        return ORJSONResponse(content={"success": False, "message": str(exc)}, status_code=500)


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
        return ORJSONResponse(
            content={
                "success": False,
                "message": "Неверный режим. Используйте set, add или subtract",
            },
            status_code=400,
        )
    try:
        amount_val = float(amount)
    except (TypeError, ValueError):
        return ORJSONResponse(content={"success": False, "message": "Неверная сумма"}, status_code=400)
    if not math.isfinite(amount_val):
        return ORJSONResponse(content={"success": False, "message": "Неверная сумма"}, status_code=400)
    if amount_val < 0:
        return ORJSONResponse(
            content={"success": False, "message": "Сумма не может быть отрицательной"}, status_code=400
        )
    try:
        profile = await pdb.resolve_partner_telegram(session, tg_id)
        if profile is None:
            return ORJSONResponse(content={"success": False, "message": "Партнёр не найден"}, status_code=404)
        profile = await pdb.get_partner_profile(session, profile["id"], lock=True)
        if profile is None:
            return ORJSONResponse(content={"success": False, "message": "Партнёр не найден"}, status_code=404)
        current = float(profile["partner_balance"] or 0)
        if mode_normalized == "subtract" and current < amount_val:
            return ORJSONResponse(content={"success": False, "message": "Недостаточно средств"}, status_code=400)
        balance = (
            amount_val
            if mode_normalized == "set"
            else current + (amount_val if mode_normalized == "add" else -amount_val)
        )
        await pdb.update_partner_profile(session, profile["id"], partner_balance=balance)
        return ORJSONResponse(content={"success": True, "message": "Баланс обновлён", "balance": balance})
    except Exception as exc:
        await session.rollback()
        return ORJSONResponse(content={"success": False, "message": str(exc)}, status_code=500)


@router.get("/{tg_id}/invited")
async def get_partner_invited(
    tg_id: int = Path(..., description="Telegram ID партнёра"),
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    """Возвращает приглашённых пользователей партнёра."""

    profile = await pdb.resolve_partner_telegram(session, tg_id)
    rows = await pdb.get_partner_invited(session, profile["id"]) if profile else []
    return ORJSONResponse(content=[_invited_entry(row) for row in rows])


@router.get("/payouts/pending")
async def get_partner_payouts_pending(
    limit: int = Query(50, ge=1, le=200, description="Лимит результатов"),
    offset: int = Query(0, ge=0, description="Смещение"),
    partner_tg_id: int | None = Query(None, description="Фильтр по TG ID партнёра"),
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    """Возвращает список ожидающих заявок на вывод."""

    profile = await pdb.resolve_partner_telegram(session, partner_tg_id) if partner_tg_id is not None else None
    if partner_tg_id is not None and profile is None:
        return ORJSONResponse(content={"total": 0, "items": []})
    total, rows = await pdb.list_partner_payouts(
        session,
        user_id=profile["id"] if profile else None,
        statuses=("pending",),
        limit=limit,
        offset=offset,
        ascending=True,
    )
    return ORJSONResponse(content={"total": total, "items": [_payout_entry(row, "pending") for row in rows]})


@router.get("/payouts/history")
async def get_partner_payouts_history(
    limit: int = Query(50, ge=1, le=200, description="Лимит результатов"),
    offset: int = Query(0, ge=0, description="Смещение"),
    partner_tg_id: int | None = Query(None, description="Фильтр по TG ID партнёра"),
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    """Возвращает историю выплат (approved/rejected)."""

    profile = await pdb.resolve_partner_telegram(session, partner_tg_id) if partner_tg_id is not None else None
    if partner_tg_id is not None and profile is None:
        return ORJSONResponse(content={"total": 0, "items": []})
    total, rows = await pdb.list_partner_payouts(
        session,
        user_id=profile["id"] if profile else None,
        statuses=("approved", "rejected"),
        limit=limit,
        offset=offset,
    )
    return ORJSONResponse(content={"total": total, "items": [_payout_entry(row, "—") for row in rows]})


@router.post("/payouts/{payout_id}/approve")
async def approve_partner_payout(
    payout_id: int = Path(..., description="ID заявки"),
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    """Одобряет заявку на вывод."""

    changed = await pdb.decide_partner_payout(session, payout_id, True)
    return ORJSONResponse(
        content={
            "success": changed,
            "message": "Заявка одобрена" if changed else "Заявка не найдена или уже обработана",
        },
        status_code=200 if changed else 404,
    )


@router.post("/payouts/{payout_id}/reject")
async def reject_partner_payout(
    payout_id: int = Path(..., description="ID заявки"),
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    """Отклоняет заявку на вывод и возвращает сумму на баланс."""

    changed = await pdb.decide_partner_payout(session, payout_id, False)
    return ORJSONResponse(
        content={
            "success": changed,
            "message": "Заявка отклонена" if changed else "Заявка не найдена или уже обработана",
        },
        status_code=200 if changed else 404,
    )


@router.patch("/{tg_id}/percent/reset")
async def reset_partner_percent(
    tg_id: int = Path(..., description="Telegram ID партнёра"),
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    """Сбрасывает персональный процент партнёра к дефолту."""

    profile = await pdb.resolve_partner_telegram(session, tg_id)
    if profile is None:
        return ORJSONResponse(content={"success": False, "message": "Партнёр не найден"}, status_code=404)
    await pdb.update_partner_profile(session, profile["id"], partner_percent=None, partner_percent_custom=False)
    return ORJSONResponse(content={"success": True, "message": "Процент сброшен"})


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
        return ORJSONResponse(content={"success": False, "message": "Код не может быть пустым"}, status_code=400)
    if not re.fullmatch(r"[a-z0-9_]{3,32}", raw):
        return ORJSONResponse(
            content={
                "success": False,
                "message": "Неверный код. Разрешены a-z, 0-9, _ (3-32 символа)",
            },
            status_code=400,
        )
    profile = await pdb.resolve_partner_telegram(session, tg_id)
    if profile is None:
        return ORJSONResponse(content={"success": False, "message": "Партнёр не найден"}, status_code=404)
    if await pdb.partner_code_taken(session, profile["id"], raw):
        return ORJSONResponse(content={"success": False, "message": "Такой код уже занят"}, status_code=409)
    await pdb.update_partner_profile(session, profile["id"], partner_code=raw)
    return ORJSONResponse(content={"success": True, "message": "Код обновлён", "code": raw})


@router.post("/reset-disabled-methods")
async def reset_disabled_payout_methods(
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    """Сбрасывает реквизиты для отключённых способов вывода."""

    try:
        from modules.partner_program import buttons as B
        from modules.partner_program.settings import (
            ENABLE_PAYOUT_CARD,
            ENABLE_PAYOUT_SBP,
            ENABLE_PAYOUT_TON,
            ENABLE_PAYOUT_USDT,
        )
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
        return ORJSONResponse(content={"success": True, "message": "Отключённых методов нет"}, status_code=200)

    await pdb.reset_payout_methods(session, disabled)

    return ORJSONResponse(content={"success": True, "message": "Отключённые методы сброшены"}, status_code=200)


@router.get("/{tg_id}/export")
async def export_partner_invites_csv(
    tg_id: int = Path(..., description="Telegram ID партнёра"),
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    """Экспортирует приглашённых партнёром в CSV."""

    profile = await pdb.resolve_partner_telegram(session, tg_id)
    data = await pdb.get_partner_invited(session, profile["id"]) if profile else []
    if not data:
        return ORJSONResponse(content={"success": False, "message": "Нет приглашённых"}, status_code=404)
    buffer = StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["joined_tg_id", "created_at"])
    for row in reversed(data):
        writer.writerow([public_tg_id(row["tg_id"]) or "", row_dt_iso(row["created_at"]) or ""])
    content = buffer.getvalue().encode("utf-8-sig")
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=partner_invites_{tg_id}.csv",
        },
    )

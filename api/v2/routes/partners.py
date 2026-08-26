import csv
import re

from base64 import b64encode
from datetime import datetime
from io import BytesIO, StringIO
from urllib.parse import urlsplit

import qrcode

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import ORJSONResponse, StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_request_actor, get_session, verify_identity_admin, verify_identity_token
from api.v2.schemas.web_public import (
    PartnerApplyRequest,
    PartnerApplyResponse,
    PartnerConditionsResponse,
    PartnerInvitedEntry,
    PartnerInvitedResponse,
    PartnerPayoutEntryResponse,
    PartnerPayoutHistoryResponse,
    PartnerPayoutMethodOption,
    PartnerPayoutMethodState,
    PartnerPayoutMethodUpdate,
    PartnerPayoutRequestCreate,
    PartnerPayoutRequestResponse,
    PartnerQrResponse,
    PartnerTopEntryResponse,
    PartnerTopResponse,
)
from database import identities as idb
from utils.referral_codes import decode_partner_code, encode_partner_code


try:
    from modules.partner_program.settings import PARTNER_BONUS_PERCENTAGES
except Exception:
    PARTNER_BONUS_PERCENTAGES = {1: 0.0}


_PARTNERS_SCHEMA_READY = False


async def partners_table_exists(session: AsyncSession) -> bool:
    global _PARTNERS_SCHEMA_READY
    if _PARTNERS_SCHEMA_READY:
        return True
    try:
        row = await session.execute(text("SELECT to_regclass('public.partners')"))
        if row.scalar() is None:
            return False
        col = await session.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'partner_balance'"
            )
        )
        ready = col.scalar() is not None
    except Exception:
        return False
    if ready:
        _PARTNERS_SCHEMA_READY = True
    return ready


async def ensure_partner_available(session: AsyncSession = Depends(get_session)) -> None:
    if not await partners_table_exists(session):
        raise HTTPException(status_code=404, detail="Партнёрская программа недоступна")


router = APIRouter(dependencies=[Depends(ensure_partner_available)])

stats_router = APIRouter()


@stats_router.get("/stats")
async def partner_stats(
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Метрики партнёрской программы: партнёры, рефералы, баланс, топ-партнёр."""
    row = (
        await session.execute(
            text(
                """
                WITH partner_refs AS (
                    SELECT partner_tg_id, COUNT(DISTINCT joined_tg_id) AS ref_count
                    FROM partners WHERE partner_tg_id IS NOT NULL GROUP BY partner_tg_id
                )
                SELECT
                    (SELECT COUNT(*) FROM partner_refs) AS total_partners,
                    (SELECT COUNT(DISTINCT partner_tg_id) FROM partners
                        WHERE partner_tg_id IS NOT NULL AND DATE(created_at) = CURRENT_DATE) AS partners_today,
                    (SELECT COUNT(DISTINCT joined_tg_id) FROM partners WHERE partner_tg_id IS NOT NULL) AS total_referred,
                    (SELECT COALESCE(SUM(u.partner_balance), 0.0) FROM users u
                        WHERE u.tg_id IN (SELECT partner_tg_id FROM partner_refs)) AS total_balance,
                    (SELECT partner_tg_id FROM partner_refs ORDER BY ref_count DESC LIMIT 1) AS top_partner_tg_id,
                    (SELECT ref_count FROM partner_refs ORDER BY ref_count DESC LIMIT 1) AS top_partner_refs
                """
            )
        )
    ).fetchone()
    if not row:
        return {
            "total_partners": 0,
            "partners_today": 0,
            "total_referred": 0,
            "total_balance": 0.0,
            "top_partner_tg_id": 0,
            "top_partner_refs": 0,
        }
    return {
        "total_partners": int(row[0] or 0),
        "partners_today": int(row[1] or 0),
        "total_referred": int(row[2] or 0),
        "total_balance": float(row[3] or 0.0),
        "top_partner_tg_id": int(row[4] or 0),
        "top_partner_refs": int(row[5] or 0),
    }


def _parse_percent(value: float) -> float | None:
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


def _resolve_public_base_url(request: Request) -> str:
    origin = str(request.headers.get("origin") or "").strip()
    if origin.startswith(("http://", "https://")):
        return origin.rstrip("/")
    referer = str(request.headers.get("referer") or request.headers.get("referrer") or "").strip()
    if referer.startswith(("http://", "https://")):
        parsed = urlsplit(referer)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    forwarded_host = str(request.headers.get("x-forwarded-host") or "").strip()
    host = forwarded_host or str(request.headers.get("host") or "").strip()
    forwarded_proto = str(request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip().lower()
    scheme = forwarded_proto if forwarded_proto in {"http", "https"} else request.url.scheme
    if host:
        return f"{scheme}://{host}".rstrip("/")
    return str(request.base_url).rstrip("/")


async def _ensure_partner_code(session: AsyncSession, user_id: int, raw_code: str | None) -> str:
    code = str(raw_code or "").strip()
    if code and not code.isdigit() and not code.startswith("r1_"):
        return code
    generated = encode_partner_code(int(user_id))
    try:
        await session.execute(
            text("UPDATE users SET partner_code = :code WHERE id = :id"),
            {"code": generated, "id": int(user_id)},
        )
        await session.flush()
    except Exception:
        pass
    return generated


async def _resolve_partner_user(session: AsyncSession, request: Request, identity) -> tuple[int, int]:
    actor = get_request_actor(request)
    billing_user_id = actor.billing_user_id if actor and actor.billing_user_id is not None else None
    if billing_user_id is None:
        billing_user_id = await idb.ensure_billing_user_for_identity(session, identity)
    row = (
        await session.execute(
            text("SELECT id, tg_id FROM users WHERE id = :user_id LIMIT 1"),
            {"user_id": int(billing_user_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=400, detail="Партнерский профиль недоступен")
    if row[1] is None:
        synthetic = -int(row[0])
        await session.execute(
            text("UPDATE users SET tg_id = :tg_id WHERE id = :user_id"),
            {"tg_id": synthetic, "user_id": int(row[0])},
        )
        return int(row[0]), synthetic
    return int(row[0]), int(row[1])


async def _resolve_referrer_by_partner_code(session: AsyncSession, partner_code: str) -> tuple[int, int] | None:
    code = str(partner_code or "").strip()
    if not code:
        return None
    by_code_row = (
        await session.execute(
            text(
                """
                SELECT id, tg_id
                FROM users
                WHERE lower(COALESCE(partner_code, '')) = lower(:code)
                LIMIT 1
                """
            ),
            {"code": code},
        )
    ).first()
    if by_code_row is not None:
        user_id = int(by_code_row[0])
        tg_id = int(by_code_row[1] if by_code_row[1] is not None else by_code_row[0])
        return user_id, tg_id
    decoded = decode_partner_code(code)
    if decoded is None:
        return None
    by_id_row = (
        await session.execute(
            text("SELECT id, tg_id FROM users WHERE id = :id LIMIT 1"),
            {"id": int(decoded)},
        )
    ).first()
    if by_id_row is not None:
        user_id = int(by_id_row[0])
        tg_id = int(by_id_row[1] if by_id_row[1] is not None else by_id_row[0])
        return user_id, tg_id
    by_tg_row = (
        await session.execute(
            text("SELECT id, tg_id FROM users WHERE tg_id = :tg_id LIMIT 1"),
            {"tg_id": int(decoded)},
        )
    ).first()
    if by_tg_row is not None:
        user_id = int(by_tg_row[0])
        tg_id = int(by_tg_row[1] if by_tg_row[1] is not None else by_tg_row[0])
        return user_id, tg_id
    return None


@router.post("/apply", response_model=PartnerApplyResponse)
async def partner_apply(
    body: PartnerApplyRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    joined_user_id, joined_tg_id = await _resolve_partner_user(session, request, identity)
    code_value = str(body.partner_code or "").strip()
    referrer_user_id: int | None = None
    referrer_tg_id: int | None = None
    if code_value:
        resolved = await _resolve_referrer_by_partner_code(session, code_value)
        if resolved is not None:
            referrer_user_id, referrer_tg_id = resolved
    if referrer_tg_id is None and body.partner_tg_id is not None:
        referrer_user_id_row = (
            await session.execute(
                text("SELECT id FROM users WHERE tg_id = :tg_id LIMIT 1"),
                {"tg_id": int(body.partner_tg_id)},
            )
        ).first()
        if referrer_user_id_row is not None:
            referrer_tg_id = int(body.partner_tg_id)
            referrer_user_id = int(referrer_user_id_row[0])
    if referrer_tg_id is None:
        raise HTTPException(status_code=400, detail="Партнерский код не найден")
    if int(referrer_tg_id) == int(joined_tg_id):
        raise HTTPException(status_code=400, detail="Нельзя применить свой партнерский код")
    already_row = (
        await session.execute(
            text("SELECT partner_tg_id FROM partners WHERE joined_tg_id = :joined_tg_id LIMIT 1"),
            {"joined_tg_id": int(joined_tg_id)},
        )
    ).first()
    if already_row is not None and already_row[0] is not None:
        raise HTTPException(status_code=409, detail="Партнер уже привязан")
    await session.execute(
        text(
            """
            INSERT INTO partners (partner_tg_id, joined_tg_id)
            VALUES (:partner_tg_id, :joined_tg_id)
            """
        ),
        {"partner_tg_id": int(referrer_tg_id), "joined_tg_id": int(joined_tg_id)},
    )
    try:
        from database.web_notifications import notify_web

        await notify_web(
            session,
            tg_id=int(referrer_tg_id),
            type="partner_joined",
            title="К вам присоединился партнёр",
            message="Новый пользователь перешёл по вашей партнёрской ссылке.",
            data={"joined_tg_id": int(joined_tg_id), "joined_user_id": int(joined_user_id)},
        )
    except Exception:
        pass
    return PartnerApplyResponse(
        ok=True,
        message="Партнерский код применен",
        partner_code=code_value,
        partner_user_id=int(referrer_user_id or 0),
        partner_tg_id=int(referrer_tg_id),
        joined_user_id=int(joined_user_id),
        joined_tg_id=int(joined_tg_id),
    )


@router.get("/invited/me", response_model=PartnerInvitedResponse)
async def partner_me_invited(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    _, tg_id = await _resolve_partner_user(session, request, identity)
    invited_sql = text(
        """
        SELECT pr.joined_tg_id, pr.created_at, COALESCE(u.balance, 0),
            (SELECT COUNT(*) FROM keys k WHERE k.tg_id = pr.joined_tg_id),
            (SELECT COUNT(*) FROM payments pay WHERE pay.tg_id = pr.joined_tg_id AND lower(pay.status) = 'success')
        FROM partners pr
        LEFT JOIN users u ON u.tg_id = pr.joined_tg_id
        WHERE pr.partner_tg_id = :tg_id
        ORDER BY pr.created_at DESC
        LIMIT :limit
        """
    )
    result = await session.execute(invited_sql, {"tg_id": tg_id, "limit": limit})
    rows = result.fetchall()
    items = [
        PartnerInvitedEntry(
            tg_id=int(row[0]),
            joined_at=row[1].isoformat() if isinstance(row[1], datetime) else None,
            balance=float(row[2] or 0),
            keys_count=int(row[3] or 0),
            payments_count=int(row[4] or 0),
        )
        for row in rows
    ]
    return PartnerInvitedResponse(total=len(items), items=items)


@router.get("/qr", response_model=PartnerQrResponse)
async def partner_qr(
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    user_id, _ = await _resolve_partner_user(session, request, identity)
    code_row = (
        await session.execute(
            text("SELECT partner_code FROM users WHERE id = :id LIMIT 1"),
            {"id": int(user_id)},
        )
    ).first()
    partner_code = await _ensure_partner_code(session, int(user_id), code_row[0] if code_row else None)
    base_url = _resolve_public_base_url(request)
    partner_link = f"{base_url}/partner/{partner_code}"
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(partner_link)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    png_buffer = BytesIO()
    image.save(png_buffer, format="PNG")
    image_data = b64encode(png_buffer.getvalue()).decode("ascii")
    return PartnerQrResponse(
        ok=True,
        link=partner_link,
        image_data_url=f"data:image/png;base64,{image_data}",
    )


@router.get("/top", response_model=PartnerTopResponse)
async def partner_top(
    request: Request,
    limit: int = Query(5, ge=1, le=20),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    _, joined_tg_id = await _resolve_partner_user(session, request, identity)
    user_referred_count_row = (
        await session.execute(
            text(
                """
                SELECT COUNT(DISTINCT joined_tg_id)
                FROM partners
                WHERE partner_tg_id = :partner_tg_id
                """
            ),
            {"partner_tg_id": int(joined_tg_id)},
        )
    ).first()
    user_referred_count = int(user_referred_count_row[0] or 0) if user_referred_count_row else 0
    user_position: int | None = None
    if user_referred_count > 0:
        user_position_row = (
            await session.execute(
                text(
                    """
                    SELECT COUNT(*) + 1
                    FROM (
                        SELECT partner_tg_id, COUNT(DISTINCT joined_tg_id) AS referred_count
                        FROM partners
                        WHERE partner_tg_id IS NOT NULL
                        GROUP BY partner_tg_id
                    ) ranked
                    WHERE ranked.referred_count > :referred_count
                    """
                ),
                {"referred_count": int(user_referred_count)},
            )
        ).first()
        user_position = int(user_position_row[0] or 1) if user_position_row else 1
    top_rows = (
        await session.execute(
            text(
                """
                SELECT
                    COALESCE(u.id, 0) AS partner_user_id,
                    p.partner_tg_id AS partner_tg_id,
                    COUNT(DISTINCT p.joined_tg_id) AS referred_count
                FROM partners p
                LEFT JOIN users u ON u.tg_id = p.partner_tg_id
                WHERE p.partner_tg_id IS NOT NULL
                GROUP BY p.partner_tg_id, u.id
                ORDER BY referred_count DESC, p.partner_tg_id ASC
                LIMIT :limit
                """
            ),
            {"limit": int(limit)},
        )
    ).all()
    top: list[PartnerTopEntryResponse] = []
    for index, row in enumerate(top_rows, 1):
        partner_user_id = int(row[0] or 0)
        partner_tg_id = int(row[1] or 0)
        referred_count = int(row[2] or 0)
        if partner_user_id > 0:
            display_id = encode_partner_code(partner_user_id)
        else:
            tg_tail = str(partner_tg_id)
            display_id = f"p_{tg_tail[:2]}***{tg_tail[-2:]}" if tg_tail else "p_***"
        top.append(
            PartnerTopEntryResponse(
                position=index,
                partner_user_id=partner_user_id,
                referred_count=referred_count,
                display_id=display_id,
            )
        )
    return PartnerTopResponse(
        user_referred_count=user_referred_count,
        user_position=user_position,
        top=top,
    )


@router.get("/conditions", response_model=PartnerConditionsResponse)
async def partner_conditions(
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    try:
        from modules.partner_program import settings as partner_settings
    except Exception:
        partner_settings = None
    mode = str(getattr(partner_settings, "REFERRAL_REWARD_MODE", "percent_only") or "percent_only")
    percent_levels_raw = getattr(partner_settings, "PARTNER_BONUS_PERCENTAGES", {}) or {}
    flat_levels_raw = getattr(partner_settings, "PARTNER_FLAT_BONUSES", {}) or {}
    min_payout = float(getattr(partner_settings, "MIN_PARTNER_PAYOUT", 0) or 0)
    custom_amount_enabled = bool(getattr(partner_settings, "ENABLE_CUSTOM_WITHDRAW_AMOUNT", False))
    method_map = [
        ("ENABLE_PAYOUT_CARD", "Карта"),
        ("ENABLE_PAYOUT_SBP", "СБП"),
        ("ENABLE_PAYOUT_USDT", "USDT"),
        ("ENABLE_PAYOUT_TON", "TON"),
    ]
    payout_methods = (
        [title for key, title in method_map if bool(getattr(partner_settings, key, False))] if partner_settings else []
    )

    def _by_level(raw: dict) -> dict[int, float]:
        """Ключи уровней приводятся к int: из настроек они могут прийти строками."""
        result: dict[int, float] = {}
        for key, value in (raw or {}).items():
            if not str(key).isdigit():
                continue
            try:
                result[int(key)] = float(value)
            except (TypeError, ValueError):
                continue
        return result

    percent_levels = _by_level(percent_levels_raw) if mode in {"percent_only", "flat_plus_percent"} else {}
    flat_levels = _by_level(flat_levels_raw) if mode in {"flat_only", "flat_plus_percent"} else {}

    level_lines: list[str] = []
    for level in sorted({*percent_levels, *flat_levels}):
        parts: list[str] = []
        if percent_levels.get(level):
            parts.append(f"{percent_levels[level] * 100:.0f}%")
        if flat_levels.get(level):
            parts.append(f"{flat_levels[level]:.0f} RUB")
        if parts:
            level_lines.append(f"{level} уровень: {' + '.join(parts)}")
    if not level_lines:
        level_lines = ["1 уровень: бонус определяется настройками проекта"]
    mode_labels = {
        "percent_only": "Процент с каждого пополнения приглашенного",
        "flat_only": "Фиксированный бонус за первую оплату приглашенного",
        "flat_plus_percent": "Фиксированный бонус за первую оплату и процент с пополнений",
    }
    rules = [
        "Вознаграждение начисляется только после успешной оплаты приглашенного пользователя.",
        "Самореферал и самопартнерство недоступны.",
        f"Минимальная сумма вывода: {min_payout:.0f} RUB." if min_payout > 0 else "Вывод доступен по правилам проекта.",
    ]
    if payout_methods:
        rules.append(f"Доступные способы вывода: {', '.join(payout_methods)}.")
    examples = [
        "Пример: приглашенный пополнил на 1000 RUB, а ставка 15% — вы получаете 150 RUB.",
        "Пример: приглашенный сделал несколько пополнений, бонус считается по каждой успешной операции.",
    ]
    return PartnerConditionsResponse(
        title="Условия партнерской программы",
        summary="Актуальные условия и режим начислений для партнеров.",
        bonus_mode=mode,
        bonus_mode_label=mode_labels.get(mode, mode_labels["percent_only"]),
        level_lines=level_lines,
        rules=rules,
        examples=examples,
        min_payout_rub=min_payout,
        payout_methods=payout_methods,
        custom_amount_enabled=custom_amount_enabled,
    )


@router.get("/payouts/me", response_model=PartnerPayoutHistoryResponse)
async def partner_payouts_me(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    _, tg_id = await _resolve_partner_user(session, request, identity)
    count_sql = text("SELECT COUNT(*) FROM payout_requests WHERE tg_id = :tg_id")
    rows_sql = text(
        """
        SELECT id, amount, status, created_at, method, destination
        FROM payout_requests
        WHERE tg_id = :tg_id
        ORDER BY created_at DESC, id DESC
        LIMIT :limit OFFSET :offset
        """
    )
    total = int((await session.scalar(count_sql, {"tg_id": tg_id})) or 0)
    rows = (await session.execute(rows_sql, {"tg_id": tg_id, "limit": int(limit), "offset": int(offset)})).fetchall()
    items = [
        PartnerPayoutEntryResponse(
            id=int(row[0]),
            amount_rub=float(row[1] or 0.0),
            status=str(row[2] or ""),
            created_at=_row_dt_iso(row[3]),
            method=row[4] or None,
            destination=row[5] or None,
        )
        for row in rows
    ]
    return PartnerPayoutHistoryResponse(total=total, items=items)


def _payout_method_options() -> list[PartnerPayoutMethodOption]:
    try:
        from modules.partner_program import (
            buttons as B,
            settings as S,
        )
    except Exception:
        return []
    defs = [
        (B.METHOD_CARD, B.BTN_METHOD_CARD, bool(getattr(S, "ENABLE_PAYOUT_CARD", False)), "16 цифр номера карты"),
        (
            B.METHOD_SBP,
            B.BTN_METHOD_SBP,
            bool(getattr(S, "ENABLE_PAYOUT_SBP", False)),
            "Номер телефона и название банка",
        ),
        (
            B.METHOD_USDT,
            B.BTN_METHOD_USDT,
            bool(getattr(S, "ENABLE_PAYOUT_USDT", False)),
            "USDT-адрес сети TRC20 (начинается с T)",
        ),
        (B.METHOD_TON, B.BTN_METHOD_TON, bool(getattr(S, "ENABLE_PAYOUT_TON", False)), "Адрес TON-кошелька"),
    ]
    return [PartnerPayoutMethodOption(key=key, label=label, hint=hint) for key, label, enabled, hint in defs if enabled]


@router.get("/payout-method/me", response_model=PartnerPayoutMethodState)
async def partner_payout_method_me(
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    user_id, _ = await _resolve_partner_user(session, request, identity)
    row = (
        await session.execute(
            text("SELECT payout_method, card_number FROM users WHERE id = :id"),
            {"id": user_id},
        )
    ).first()
    method = (row[0] if row else None) or None
    card = (row[1] if row else None) or None
    configured = bool(card and str(card).strip())
    from modules.partner_program.handlers.utils import mask_requisites, method_label

    return PartnerPayoutMethodState(
        configured=configured,
        method=method if configured else None,
        method_label=method_label(method) if configured else None,
        masked=mask_requisites(method, card) if configured else None,
        methods=_payout_method_options(),
    )


@router.put("/payout-method/me", response_model=PartnerPayoutMethodState)
async def partner_set_payout_method(
    body: PartnerPayoutMethodUpdate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    user_id, _ = await _resolve_partner_user(session, request, identity)
    from modules.partner_program.handlers.utils import (
        _method_enabled,
        mask_requisites,
        method_label,
        validate_requisites,
    )

    method = body.method.strip()
    if not _method_enabled(method):
        raise HTTPException(status_code=400, detail="Способ вывода недоступен")
    requisites = body.requisites.strip()
    if not validate_requisites(method, requisites):
        raise HTTPException(status_code=400, detail="Некорректные реквизиты для выбранного способа")
    await session.execute(
        text("UPDATE users SET payout_method = :m, card_number = :c WHERE id = :id"),
        {"m": method, "c": requisites, "id": user_id},
    )
    return PartnerPayoutMethodState(
        configured=True,
        method=method,
        method_label=method_label(method),
        masked=mask_requisites(method, requisites),
        methods=_payout_method_options(),
    )


@router.post("/payouts/me", response_model=PartnerPayoutRequestResponse)
async def partner_create_payout_request(
    body: PartnerPayoutRequestCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    user_id, tg_id = await _resolve_partner_user(session, request, identity)
    row = (
        await session.execute(
            text("SELECT COALESCE(partner_balance, 0), payout_method, card_number FROM users WHERE id = :id"),
            {"id": user_id},
        )
    ).first()
    balance = float(row[0] or 0.0) if row else 0.0
    requested = float(body.amount_rub)
    if requested <= 0:
        raise HTTPException(status_code=400, detail="Сумма должна быть больше нуля")
    try:
        from modules.partner_program.settings import ENABLE_CUSTOM_WITHDRAW_AMOUNT, MIN_PARTNER_PAYOUT
    except Exception:
        ENABLE_CUSTOM_WITHDRAW_AMOUNT = True
        MIN_PARTNER_PAYOUT = 0
    min_payout = float(MIN_PARTNER_PAYOUT or 0)
    if requested < min_payout:
        raise HTTPException(status_code=400, detail=f"Минимальная сумма вывода — {min_payout:.0f} RUB")
    if not bool(ENABLE_CUSTOM_WITHDRAW_AMOUNT):
        requested = balance
    if requested > balance:
        raise HTTPException(status_code=400, detail="Недостаточно партнерского баланса")
    if requested <= 0:
        raise HTTPException(status_code=400, detail="Недостаточно средств для заявки")
    payout_method = (row[1] if row else None) or "card"
    destination = (row[2] if row else None) or None
    if not (destination and str(destination).strip()):
        raise HTTPException(status_code=400, detail="Сначала укажите способ вывода и реквизиты")
    debited = (
        await session.execute(
            text(
                "UPDATE users SET partner_balance = COALESCE(partner_balance, 0) - :amount "
                "WHERE id = :id AND COALESCE(partner_balance, 0) >= :amount "
                "RETURNING COALESCE(partner_balance, 0)"
            ),
            {"amount": float(requested), "id": int(user_id)},
        )
    ).scalar()
    if debited is None:
        raise HTTPException(status_code=400, detail="Недостаточно партнерского баланса")
    new_balance = float(debited)
    inserted = (
        await session.execute(
            text(
                """
                INSERT INTO payout_requests (tg_id, amount, status, created_at, method, destination)
                VALUES (:tg_id, :amount, 'pending', NOW(), :method, :destination)
                RETURNING id
                """
            ),
            {
                "tg_id": int(tg_id),
                "amount": float(requested),
                "method": payout_method,
                "destination": destination,
            },
        )
    ).scalar()
    return PartnerPayoutRequestResponse(
        ok=True,
        message="Заявка на вывод создана",
        request_id=int(inserted) if inserted is not None else None,
        amount_rub=float(requested),
        status="pending",
        balance_rub=float(new_balance),
    )


@router.get("/all")
async def get_all_partners(
    limit: int = Query(1000, ge=1, le=10000, description="Лимит результатов"),
    offset: int = Query(0, ge=0, description="Смещение"),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Список всех партнёров со статистикой. Требуется админ (X-Identity-Id + X-Token)."""
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
    default_percent = _default_partner_percent()
    partners_list = []
    for partner in partners:
        percent_value = partner[2]
        percent_custom = bool(partner[3])
        percent = float(percent_value) if (percent_custom and percent_value is not None) else float(default_percent)
        partners_list.append({
            "tg_id": int(partner[0]),
            "balance": float(partner[1] or 0),
            "percent": percent,
            "code": partner[4] or None,
            "method": partner[5] or None,
            "referred_count": int(partner[6] or 0),
        })
    return ORJSONResponse(content={"total": total, "items": partners_list})


@router.patch("/{tg_id}")
async def update_partner(
    tg_id: int = Path(..., description="Telegram ID партнёра"),
    balance: float = Query(..., description="Новый баланс партнёра"),
    percent: float = Query(..., description="Новый процент партнёра"),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Обновляет данные партнёра (баланс и процент)."""
    try:
        stmt = text(
            """
            UPDATE users 
            SET partner_balance = :balance, partner_percent = :percent 
            WHERE tg_id = :tg_id
            """
        )
        result = await session.execute(stmt, {"tg_id": tg_id, "balance": balance, "percent": percent})
        if result.rowcount > 0:
            return ORJSONResponse(
                content={"success": True, "message": f"Партнёр {tg_id} успешно обновлён"}, status_code=200
            )
        return ORJSONResponse(content={"success": False, "message": "Партнёр не найден"}, status_code=404)
    except Exception as e:
        await session.rollback()
        return ORJSONResponse(content={"success": False, "message": str(e)}, status_code=500)


@router.get("/{tg_id}")
async def get_partner_data(
    tg_id: int = Path(..., description="Telegram ID партнёра"),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Партнёрские данные по tg_id и список приглашённых."""
    meta_sql = text(
        """
        SELECT COALESCE(u.partner_balance, 0), u.partner_percent, COALESCE(u.partner_percent_custom, false), u.partner_code, u.payout_method
        FROM users u WHERE u.tg_id = :tg_id
        """
    )
    invited_sql = text(
        """
        SELECT pr.joined_tg_id, pr.created_at, COALESCE(u.balance, 0),
            (SELECT COUNT(*) FROM keys k WHERE k.tg_id = pr.joined_tg_id),
            (SELECT COUNT(*) FROM payments pay WHERE pay.tg_id = pr.joined_tg_id AND lower(pay.status) = 'success')
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
        percent_value, percent_custom = meta_row[1], bool(meta_row[2])
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
    return ORJSONResponse(content=response)


@router.post("/{tg_id}/invited")
async def add_partner_invited(
    tg_id: int = Path(..., description="Telegram ID партнёра"),
    joined_tg_id: int = Query(..., description="Telegram ID приглашённого"),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Добавляет приглашённого пользователя партнёру."""
    if joined_tg_id == tg_id:
        return ORJSONResponse(
            content={"success": False, "message": "Нельзя привязать пользователя к самому себе"}, status_code=400
        )
    try:
        partner_exists = await session.execute(text("SELECT 1 FROM users WHERE tg_id = :tg_id"), {"tg_id": tg_id})
        if not partner_exists.scalar():
            return ORJSONResponse(content={"success": False, "message": "Партнёр не найден"}, status_code=404)
        invited_exists = await session.execute(
            text("SELECT 1 FROM users WHERE tg_id = :joined_tg_id"), {"joined_tg_id": joined_tg_id}
        )
        if not invited_exists.scalar():
            return ORJSONResponse(
                content={"success": False, "message": "Приглашённый пользователь не найден"}, status_code=404
            )
        existing = await session.execute(
            text("SELECT partner_tg_id FROM partners WHERE joined_tg_id = :joined_tg_id"),
            {"joined_tg_id": joined_tg_id},
        )
        existing_partner = existing.scalar()
        if existing_partner is not None:
            return ORJSONResponse(
                content={"success": False, "message": f"Пользователь уже привязан к партнёру {existing_partner}"},
                status_code=409,
            )
        await session.execute(
            text("INSERT INTO partners (partner_tg_id, joined_tg_id) VALUES (:partner_tg_id, :joined_tg_id)"),
            {"partner_tg_id": tg_id, "joined_tg_id": joined_tg_id},
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
    except Exception as e:
        await session.rollback()
        return ORJSONResponse(content={"success": False, "message": str(e)}, status_code=500)


@router.delete("/{tg_id}/invited/{joined_tg_id}")
async def delete_partner_invited(
    tg_id: int = Path(..., description="Telegram ID партнёра"),
    joined_tg_id: int = Path(..., description="Telegram ID приглашённого"),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Удаляет приглашённого у партнёра."""
    try:
        result = await session.execute(
            text("DELETE FROM partners WHERE partner_tg_id = :partner_tg_id AND joined_tg_id = :joined_tg_id"),
            {"partner_tg_id": tg_id, "joined_tg_id": joined_tg_id},
        )
        if result.rowcount > 0:
            return ORJSONResponse(
                content={
                    "success": True,
                    "message": "Приглашённый удалён",
                    "partner_tg_id": tg_id,
                    "joined_tg_id": joined_tg_id,
                },
                status_code=200,
            )
        return ORJSONResponse(
            content={"success": False, "message": "Связка партнёр-приглашённый не найдена"}, status_code=404
        )
    except Exception as e:
        await session.rollback()
        return ORJSONResponse(content={"success": False, "message": str(e)}, status_code=500)


@router.patch("/{tg_id}/percent")
async def update_partner_percent(
    tg_id: int = Path(..., description="Telegram ID партнёра"),
    percent: float = Query(..., description="Новый персональный процент (0-100 или 0.0-1.0)"),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Обновляет персональный процент партнёра."""
    normalized = _parse_percent(percent)
    if normalized is None:
        return ORJSONResponse(
            content={"success": False, "message": "Неверный процент. Допустимо 0-100 или 0.0-1.0"}, status_code=400
        )
    try:
        result = await session.execute(
            text("UPDATE users SET partner_percent = :percent, partner_percent_custom = true WHERE tg_id = :tg_id"),
            {"tg_id": tg_id, "percent": normalized},
        )
        if result.rowcount > 0:
            return ORJSONResponse(
                content={"success": True, "message": "Процент обновлён", "percent": normalized}, status_code=200
            )
        return ORJSONResponse(content={"success": False, "message": "Партнёр не найден"}, status_code=404)
    except Exception as e:
        await session.rollback()
        return ORJSONResponse(content={"success": False, "message": str(e)}, status_code=500)


@router.patch("/{tg_id}/balance")
async def update_partner_balance(
    tg_id: int = Path(..., description="Telegram ID партнёра"),
    amount: float = Query(..., description="Сумма операции"),
    mode: str = Query("set", description="Режим: set, add, subtract"),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Изменяет баланс партнёрской программы."""
    mode_normalized = (mode or "set").strip().lower()
    if mode_normalized not in {"set", "add", "subtract"}:
        return ORJSONResponse(
            content={"success": False, "message": "Неверный режим. Используйте set, add или subtract"}, status_code=400
        )
    try:
        amount_val = float(amount)
    except (TypeError, ValueError):
        return ORJSONResponse(content={"success": False, "message": "Неверная сумма"}, status_code=400)
    if amount_val < 0:
        return ORJSONResponse(
            content={"success": False, "message": "Сумма не может быть отрицательной"}, status_code=400
        )
    if amount_val > 100_000_000:
        return ORJSONResponse(content={"success": False, "message": "Слишком большая сумма"}, status_code=400)
    try:
        current_res = await session.execute(
            text("SELECT partner_balance FROM users WHERE tg_id = :tg_id"), {"tg_id": tg_id}
        )
        current_balance = current_res.scalar()
        if current_balance is None:
            return ORJSONResponse(content={"success": False, "message": "Партнёр не найден"}, status_code=404)
        current_balance = float(current_balance or 0.0)
        if mode_normalized == "set":
            new_balance = amount_val
        elif mode_normalized == "add":
            new_balance = current_balance + amount_val
        else:
            if current_balance < amount_val:
                return ORJSONResponse(content={"success": False, "message": "Недостаточно средств"}, status_code=400)
            new_balance = current_balance - amount_val
        await session.execute(
            text("UPDATE users SET partner_balance = :balance WHERE tg_id = :tg_id"),
            {"tg_id": tg_id, "balance": new_balance},
        )
        return ORJSONResponse(
            content={"success": True, "message": "Баланс обновлён", "balance": new_balance}, status_code=200
        )
    except Exception as e:
        await session.rollback()
        return ORJSONResponse(content={"success": False, "message": str(e)}, status_code=500)


@router.get("/{tg_id}/invited")
async def get_partner_invited(
    tg_id: int = Path(..., description="Telegram ID партнёра"),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Список приглашённых партнёра."""
    invited_sql = text(
        """
        SELECT pr.joined_tg_id, pr.created_at, COALESCE(u.balance, 0),
            (SELECT COUNT(*) FROM keys k WHERE k.tg_id = pr.joined_tg_id),
            (SELECT COUNT(*) FROM payments pay WHERE pay.tg_id = pr.joined_tg_id AND lower(pay.status) = 'success')
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
    return ORJSONResponse(content=invited_list)


@router.get("/payouts/pending")
async def get_partner_payouts_pending(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    partner_tg_id: int | None = Query(None),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Ожидающие заявки на вывод."""
    where_clause = "WHERE pr.status = 'pending'"
    params = {"limit": limit, "offset": offset}
    if partner_tg_id is not None:
        where_clause += " AND pr.tg_id = :partner_tg_id"
        params["partner_tg_id"] = partner_tg_id
    count_sql = text(f"SELECT COUNT(*) FROM payout_requests pr {where_clause}")
    rows_sql = text(
        f"""
        SELECT pr.id, pr.tg_id, pr.amount, pr.status, pr.created_at,
               COALESCE(pr.method, u.payout_method) AS method, COALESCE(pr.destination, u.card_number) AS destination
        FROM payout_requests pr
        LEFT JOIN users u ON u.tg_id = pr.tg_id
        {where_clause}
        ORDER BY pr.created_at ASC, pr.id ASC
        LIMIT :limit OFFSET :offset
        """
    )
    total = await session.scalar(count_sql) or 0
    result = await session.execute(rows_sql, params)
    items = [
        {
            "id": int(row[0]),
            "tg_id": int(row[1]),
            "amount": float(row[2] or 0.0),
            "status": row[3] or "pending",
            "created_at": _row_dt_iso(row[4]),
            "method": row[5] or None,
            "destination": row[6] or None,
        }
        for row in result.fetchall()
    ]
    return ORJSONResponse(content={"total": int(total), "items": items})


@router.get("/payouts/history")
async def get_partner_payouts_history(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    partner_tg_id: int | None = Query(None),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """История выплат (approved/rejected)."""
    where_clause = "WHERE pr.status IN ('approved','rejected')"
    params = {"limit": limit, "offset": offset}
    if partner_tg_id is not None:
        where_clause += " AND pr.tg_id = :partner_tg_id"
        params["partner_tg_id"] = partner_tg_id
    count_sql = text(f"SELECT COUNT(*) FROM payout_requests pr {where_clause}")
    rows_sql = text(
        f"""
        SELECT pr.id, pr.tg_id, pr.amount, pr.status, pr.created_at,
               COALESCE(pr.method, u.payout_method) AS method, COALESCE(pr.destination, u.card_number) AS destination
        FROM payout_requests pr
        LEFT JOIN users u ON u.tg_id = pr.tg_id
        {where_clause}
        ORDER BY pr.created_at DESC, pr.id DESC
        LIMIT :limit OFFSET :offset
        """
    )
    total = await session.scalar(count_sql) or 0
    result = await session.execute(rows_sql, params)
    items = [
        {
            "id": int(row[0]),
            "tg_id": int(row[1]),
            "amount": float(row[2] or 0.0),
            "status": row[3] or "—",
            "created_at": _row_dt_iso(row[4]),
            "method": row[5] or None,
            "destination": row[6] or None,
        }
        for row in result.fetchall()
    ]
    return ORJSONResponse(content={"total": int(total), "items": items})


@router.post("/payouts/{payout_id}/approve")
async def approve_partner_payout(
    payout_id: int = Path(..., description="ID заявки"),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Одобряет заявку на вывод."""
    req_row = await session.execute(
        text("SELECT id, tg_id, amount FROM payout_requests WHERE id = :id AND status = 'pending'"),
        {"id": payout_id},
    )
    req = req_row.fetchone()
    if not req:
        return ORJSONResponse(
            content={"success": False, "message": "Заявка не найдена или уже обработана"}, status_code=404
        )
    user_row = await session.execute(
        text("SELECT payout_method, card_number FROM users WHERE tg_id = :tg_id"), {"tg_id": req[1]}
    )
    user = user_row.fetchone()
    payout_method = (user[0] if user else None) or "card"
    destination = (user[1] if user else None) or None
    destination = (destination or "").strip() or None
    updated = await session.execute(
        text(
            "UPDATE payout_requests SET status = 'approved', method = :method, destination = :destination "
            "WHERE id = :id AND status = 'pending' RETURNING id"
        ),
        {"id": payout_id, "method": payout_method, "destination": destination},
    )
    if updated.fetchone() is None:
        return ORJSONResponse(
            content={"success": False, "message": "Заявка не найдена или уже обработана"}, status_code=404
        )
    return ORJSONResponse(content={"success": True, "message": "Заявка одобрена"}, status_code=200)


@router.post("/payouts/{payout_id}/reject")
async def reject_partner_payout(
    payout_id: int = Path(..., description="ID заявки"),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Отклоняет заявку на вывод и возвращает сумму на баланс."""
    req_row = await session.execute(
        text("SELECT id, tg_id, amount FROM payout_requests WHERE id = :id AND status = 'pending'"),
        {"id": payout_id},
    )
    req = req_row.fetchone()
    if not req:
        return ORJSONResponse(
            content={"success": False, "message": "Заявка не найдена или уже обработана"}, status_code=404
        )
    user_row = await session.execute(
        text("SELECT payout_method, card_number FROM users WHERE tg_id = :tg_id"), {"tg_id": req[1]}
    )
    user = user_row.fetchone()
    payout_method = (user[0] if user else None) or "card"
    destination = (user[1] if user else None) or None
    destination = (destination or "").strip() or None
    updated = await session.execute(
        text(
            "UPDATE payout_requests SET status = 'rejected', method = :method, destination = :destination "
            "WHERE id = :id AND status = 'pending' RETURNING tg_id, amount"
        ),
        {"id": payout_id, "method": payout_method, "destination": destination},
    )
    rejected = updated.fetchone()
    if rejected is None:
        return ORJSONResponse(
            content={"success": False, "message": "Заявка не найдена или уже обработана"}, status_code=404
        )
    await session.execute(
        text("UPDATE users SET partner_balance = partner_balance + :amount WHERE tg_id = :tg_id"),
        {"amount": float(rejected[1] or 0.0), "tg_id": rejected[0]},
    )
    return ORJSONResponse(content={"success": True, "message": "Заявка отклонена"}, status_code=200)


@router.patch("/{tg_id}/percent/reset")
async def reset_partner_percent(
    tg_id: int = Path(..., description="Telegram ID партнёра"),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Сбрасывает персональный процент партнёра к дефолту."""
    result = await session.execute(
        text("UPDATE users SET partner_percent = NULL, partner_percent_custom = false WHERE tg_id = :tg_id"),
        {"tg_id": tg_id},
    )
    if result.rowcount > 0:
        return ORJSONResponse(content={"success": True, "message": "Процент сброшен"}, status_code=200)
    return ORJSONResponse(content={"success": False, "message": "Партнёр не найден"}, status_code=404)


@router.patch("/{tg_id}/code")
async def update_partner_code(
    tg_id: int = Path(..., description="Telegram ID партнёра"),
    code: str = Query(..., description="Новый код партнёра (латиница/цифры/_)"),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Обновляет код партнёрской ссылки."""
    raw = (code or "").strip().lower()
    if not raw:
        return ORJSONResponse(content={"success": False, "message": "Код не может быть пустым"}, status_code=400)
    if not re.fullmatch(r"[a-z0-9_]{3,32}", raw):
        return ORJSONResponse(
            content={"success": False, "message": "Неверный код. Разрешены a-z, 0-9, _ (3-32 символа)"}, status_code=400
        )
    exists = await session.execute(
        text("SELECT 1 FROM users WHERE partner_code = :code AND tg_id != :tg_id"), {"code": raw, "tg_id": tg_id}
    )
    if exists.first():
        return ORJSONResponse(content={"success": False, "message": "Такой код уже занят"}, status_code=409)
    result = await session.execute(
        text("UPDATE users SET partner_code = :code WHERE tg_id = :tg_id"), {"code": raw, "tg_id": tg_id}
    )
    if result.rowcount > 0:
        return ORJSONResponse(content={"success": True, "message": "Код обновлён", "code": raw}, status_code=200)
    return ORJSONResponse(content={"success": False, "message": "Партнёр не найден"}, status_code=404)


@router.post("/reset-disabled-methods")
async def reset_disabled_payout_methods(
    identity=Depends(verify_identity_admin),
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
    await session.execute(
        text("UPDATE users SET card_number = NULL WHERE payout_method = ANY(:methods)"), {"methods": disabled}
    )
    return ORJSONResponse(content={"success": True, "message": "Отключённые методы сброшены"}, status_code=200)


@router.get("/{tg_id}/export")
async def export_partner_invites_csv(
    tg_id: int = Path(..., description="Telegram ID партнёра"),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Экспорт приглашённых партнёром в CSV."""
    rows = await session.execute(
        text("SELECT joined_tg_id, created_at FROM partners WHERE partner_tg_id = :tg_id ORDER BY created_at ASC"),
        {"tg_id": tg_id},
    )
    data = rows.fetchall()
    if not data:
        return ORJSONResponse(content={"success": False, "message": "Нет приглашённых"}, status_code=404)
    buffer = StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["joined_tg_id", "created_at"])
    for joined_tg_id, created_at in data:
        writer.writerow([int(joined_tg_id), created_at.isoformat() if created_at else ""])
    content = buffer.getvalue().encode("utf-8-sig")
    filename = f"partner_invites_{tg_id}.csv"
    return StreamingResponse(
        iter([content]), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

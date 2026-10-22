import csv
import re

from base64 import b64encode
from io import BytesIO, StringIO

import qrcode

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import ORJSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_request_actor, get_session, verify_identity_admin, verify_identity_token
from api.shared.http import resolve_public_base_url
from api.shared.partners import default_partner_percent, parse_percent, row_dt_iso
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
from database import (
    identities as idb,
    partners as pdb,
)
from database.access.resolution import UserId, public_tg_id
from database.web_notifications import notify_web
from utils.referral_codes import encode_partner_code


async def partners_table_exists(session: AsyncSession) -> bool:
    """Проверяет доступность сохранённой партнёрской схемы."""
    return await pdb.partner_schema_available(session)


async def ensure_partner_available(session: AsyncSession = Depends(get_session)) -> None:
    if not await partners_table_exists(session):
        raise HTTPException(status_code=404, detail="Партнёрская программа недоступна")


router = APIRouter(dependencies=[Depends(ensure_partner_available)])
stats_router = APIRouter(dependencies=[Depends(ensure_partner_available)])


@stats_router.get("/stats")
async def partner_stats(identity=Depends(verify_identity_admin), session: AsyncSession = Depends(get_session)):
    """Возвращает общие метрики партнёрской программы."""
    stats = await pdb.get_partner_stats(session)
    stats["top_partner_tg_id"] = public_tg_id(stats["top_partner_tg_id"])
    return {key: float(value or 0) if key == "total_balance" else int(value or 0) for key, value in stats.items()}


async def _resolve_partner_user(session: AsyncSession, request: Request, identity) -> tuple[int, int | None]:
    actor = get_request_actor(request)
    user_id = actor.billing_user_id if actor and actor.billing_user_id is not None else None
    if user_id is None:
        user_id = await idb.ensure_billing_user_for_identity(session, identity)
    profile = await pdb.get_partner_profile(session, user_id)
    if profile is None:
        raise HTTPException(status_code=400, detail="Партнерский профиль недоступен")
    return int(profile["id"]), profile["tg_id"]


async def _admin_partner(session: AsyncSession, ref: str | int) -> dict | None:
    """Различает каноническую ссылку u и прежний Telegram ID."""
    raw = str(ref)
    if raw.startswith("u") and raw[1:].isdigit():
        return await pdb.get_partner_profile(session, int(raw[1:]))
    try:
        return await pdb.resolve_partner_telegram(session, int(raw))
    except ValueError:
        return None


def _invited_entry(row: dict, *, admin: bool = False) -> dict:
    """Формирует данные приглашённого без выдуманного Telegram ID."""
    return {
        "user_id": row["user_id"],
        "tg_id": public_tg_id(row["tg_id"]),
        "joined_at": row_dt_iso(row["created_at"]),
        "balance": float(row["balance"] or 0),
        "subs_count" if admin else "keys_count": int(row["keys_count"] or 0),
        "payments_count": int(row["payments_count"] or 0),
    }


@router.post("/apply", response_model=PartnerApplyResponse)
async def partner_apply(
    body: PartnerApplyRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    user_id, tg_id = await _resolve_partner_user(session, request, identity)
    code_value = str(body.partner_code or "").strip()
    referrer = await pdb.find_partner_by_code(session, code_value) if code_value else None
    if referrer is None and body.partner_tg_id is not None:
        referrer = await pdb.resolve_partner_telegram(session, body.partner_tg_id)
    if referrer is None:
        raise HTTPException(status_code=400, detail="Партнерский код не найден")
    try:
        await pdb.create_partner_link(session, referrer["id"], user_id)
    except ValueError as exc:
        if str(exc) == "already":
            raise HTTPException(status_code=409, detail="Партнер уже привязан") from exc
        if str(exc) == "self":
            raise HTTPException(status_code=400, detail="Нельзя применить свой партнерский код") from exc
        raise HTTPException(status_code=400, detail="Партнерский код не найден") from exc
    await notify_web(
        session,
        user_ref=UserId(referrer["id"]),
        type="partner_joined",
        title="К вам присоединился партнёр",
        message="Новый пользователь перешёл по вашей партнёрской ссылке.",
        data={"joined_tg_id": public_tg_id(tg_id), "joined_user_id": user_id},
    )
    return PartnerApplyResponse(
        ok=True,
        message="Партнерский код применен",
        partner_code=code_value,
        partner_user_id=referrer["id"],
        partner_tg_id=public_tg_id(referrer["tg_id"]),
        joined_user_id=user_id,
        joined_tg_id=public_tg_id(tg_id),
    )


@router.get("/invited/me", response_model=PartnerInvitedResponse)
async def partner_me_invited(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    user_id, _ = await _resolve_partner_user(session, request, identity)
    rows = await pdb.get_partner_invited(session, user_id, limit)
    return PartnerInvitedResponse(total=len(rows), items=[PartnerInvitedEntry(**_invited_entry(row)) for row in rows])


@router.get("/qr", response_model=PartnerQrResponse)
async def partner_qr(
    request: Request, session: AsyncSession = Depends(get_session), identity=Depends(verify_identity_token)
):
    user_id, _ = await _resolve_partner_user(session, request, identity)
    profile = await pdb.get_partner_profile(session, user_id)
    partner_code = await pdb.ensure_partner_code(session, user_id, profile["partner_code"])
    partner_link = f"{resolve_public_base_url(request)}/partner/{partner_code}"
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(partner_link)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    png_buffer = BytesIO()
    image.save(png_buffer, format="PNG")
    image_data = b64encode(png_buffer.getvalue()).decode("ascii")
    return PartnerQrResponse(ok=True, link=partner_link, image_data_url=f"data:image/png;base64,{image_data}")


@router.get("/top", response_model=PartnerTopResponse)
async def partner_top(
    request: Request,
    limit: int = Query(5, ge=1, le=20),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    user_id, _ = await _resolve_partner_user(session, request, identity)
    result = await pdb.get_partner_ranking(session, user_id, limit)
    return PartnerTopResponse(
        user_referred_count=result["user_referred_count"],
        user_position=result["user_position"],
        top=[
            PartnerTopEntryResponse(
                position=i,
                partner_user_id=row["partner_user_id"],
                referred_count=row["referred_count"],
                display_id=encode_partner_code(row["partner_user_id"]),
            )
            for i, row in enumerate(result["top"], 1)
        ],
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
        """Приводит номера уровней в настройках к целым числам."""
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
    user_id, _ = await _resolve_partner_user(session, request, identity)
    total, rows = await pdb.list_partner_payouts(session, user_id=user_id, limit=limit, offset=offset)
    return PartnerPayoutHistoryResponse(
        total=total,
        items=[
            PartnerPayoutEntryResponse(
                id=row["id"],
                amount_rub=row["amount"],
                status=row["status"],
                created_at=row_dt_iso(row["created_at"]),
                method=row["method"],
                destination=row["destination"],
            )
            for row in rows
        ],
    )


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
    request: Request, session: AsyncSession = Depends(get_session), identity=Depends(verify_identity_token)
):
    user_id, _ = await _resolve_partner_user(session, request, identity)
    profile = await pdb.get_partner_profile(session, user_id)
    method, card = profile["payout_method"], profile["card_number"]
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

    method, requisites = body.method.strip(), body.requisites.strip()
    if not _method_enabled(method):
        raise HTTPException(status_code=400, detail="Способ вывода недоступен")
    if not validate_requisites(method, requisites):
        raise HTTPException(status_code=400, detail="Некорректные реквизиты для выбранного способа")
    await pdb.update_partner_profile(session, user_id, payout_method=method, card_number=requisites)
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
    user_id, _ = await _resolve_partner_user(session, request, identity)
    profile = await pdb.get_partner_profile(session, user_id, lock=True)
    balance, requested = float(profile["partner_balance"]), float(body.amount_rub)
    if requested <= 0:
        raise HTTPException(status_code=400, detail="Сумма должна быть больше нуля")
    try:
        from modules.partner_program.settings import (
            ENABLE_CUSTOM_WITHDRAW_AMOUNT,
            MIN_PARTNER_PAYOUT,
            PARTNER_PAYOUT_FEE_RATE,
        )
    except Exception:
        ENABLE_CUSTOM_WITHDRAW_AMOUNT, MIN_PARTNER_PAYOUT, PARTNER_PAYOUT_FEE_RATE = True, 0, 0.0
    min_payout = float(MIN_PARTNER_PAYOUT or 0)
    if not bool(ENABLE_CUSTOM_WITHDRAW_AMOUNT):
        requested = balance
    if requested > balance:
        raise HTTPException(status_code=400, detail="Недостаточно партнерского баланса")
    if requested <= 0:
        raise HTTPException(status_code=400, detail="Недостаточно средств для заявки")
    if requested < min_payout:
        raise HTTPException(status_code=400, detail=f"Минимальная сумма вывода — {min_payout:.0f} RUB")
    method = str(profile["payout_method"] or "card").strip()
    if method not in {option.key for option in _payout_method_options()}:
        raise HTTPException(status_code=400, detail="Способ вывода недоступен")
    destination = profile["card_number"]
    if not (destination and str(destination).strip()):
        raise HTTPException(status_code=400, detail="Сначала укажите способ вывода и реквизиты")
    fee_rate = float(PARTNER_PAYOUT_FEE_RATE or 0.0)
    fee = round(requested * fee_rate, 2) if fee_rate > 0 else 0.0
    net_amount = requested - fee
    if not 0 < net_amount <= requested:
        raise HTTPException(status_code=400, detail="Недостаточно средств для заявки")
    try:
        payout_id, new_balance = await pdb.create_partner_payout(
            session, user_id, net_amount, method, destination, debit_amount=requested
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Недостаточно партнерского баланса") from exc
    return PartnerPayoutRequestResponse(
        ok=True,
        message="Заявка на вывод создана",
        request_id=payout_id,
        amount_rub=requested,
        status="pending",
        balance_rub=new_balance,
    )


@router.get("/all")
async def get_all_partners(
    limit: int = Query(1000, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    total, rows = await pdb.list_partners(session, limit, offset)
    default_percent = default_partner_percent()
    items = [
        {
            "user_id": row["id"],
            "tg_id": public_tg_id(row["tg_id"]),
            "user_ref": f"u{row['id']}",
            "balance": float(row["partner_balance"] or 0),
            "percent": float(row["partner_percent"])
            if row["partner_percent_custom"] and row["partner_percent"] is not None
            else default_percent,
            "code": row["partner_code"],
            "method": row["payout_method"],
            "referred_count": row["referred_count"],
        }
        for row in rows
    ]
    return ORJSONResponse(content={"total": total, "items": items})


@router.patch("/{tg_id}")
async def update_partner(
    tg_id: str = Path(...),
    balance: float = Query(...),
    percent: float = Query(...),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    profile = await _admin_partner(session, tg_id)
    if profile is None:
        return ORJSONResponse(content={"success": False, "message": "Партнёр не найден"}, status_code=404)
    await pdb.update_partner_profile(session, profile["id"], partner_balance=balance, partner_percent=percent)
    return ORJSONResponse(content={"success": True, "message": f"Партнёр {tg_id} успешно обновлён"})


@router.get("/{tg_id}")
async def get_partner_data(
    tg_id: str = Path(...), identity=Depends(verify_identity_admin), session: AsyncSession = Depends(get_session)
):
    profile = await _admin_partner(session, tg_id)
    if profile is None:
        return ORJSONResponse(content={"success": False, "message": "Партнёр не найден"}, status_code=404)
    rows = await pdb.get_partner_invited(session, profile["id"])
    percent = (
        float(profile["partner_percent"])
        if profile["partner_percent_custom"] and profile["partner_percent"] is not None
        else default_partner_percent()
    )
    return ORJSONResponse(
        content={
            "user_id": profile["id"],
            "tg_id": public_tg_id(profile["tg_id"]),
            "partner_balance": float(profile["partner_balance"]),
            "partner_percent": percent,
            "partner_code": profile["partner_code"],
            "payout_method": profile["payout_method"],
            "invited": [_invited_entry(row, admin=True) for row in rows],
        }
    )


@router.post("/{tg_id}/invited")
async def add_partner_invited(
    tg_id: str = Path(...),
    joined_tg_id: str = Query(...),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    partner, joined = await _admin_partner(session, tg_id), await _admin_partner(session, joined_tg_id)
    if partner is None or joined is None:
        return ORJSONResponse(
            content={
                "success": False,
                "message": "Партнёр не найден" if partner is None else "Приглашённый пользователь не найден",
            },
            status_code=404,
        )
    try:
        result = await pdb.create_partner_link(session, partner["id"], joined["id"])
    except ValueError as exc:
        return ORJSONResponse(
            content={
                "success": False,
                "message": "Нельзя привязать пользователя к самому себе"
                if str(exc) == "self"
                else "Пользователь уже привязан к партнёру",
            },
            status_code=400 if str(exc) == "self" else 409,
        )
    return ORJSONResponse(content={"success": True, "message": "Приглашённый добавлен", **result}, status_code=201)


@router.delete("/{tg_id}/invited/{joined_tg_id}")
async def delete_partner_invited(
    tg_id: str = Path(...),
    joined_tg_id: str = Path(...),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    partner, joined = await _admin_partner(session, tg_id), await _admin_partner(session, joined_tg_id)
    deleted = partner and joined and await pdb.delete_partner_link(session, partner["id"], joined["id"])
    return ORJSONResponse(
        content={
            "success": bool(deleted),
            "message": "Приглашённый удалён" if deleted else "Связка партнёр-приглашённый не найдена",
        },
        status_code=200 if deleted else 404,
    )


@router.patch("/{tg_id}/percent")
async def update_partner_percent(
    tg_id: str = Path(...),
    percent: float = Query(...),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    normalized = parse_percent(percent)
    if normalized is None:
        return ORJSONResponse(
            content={"success": False, "message": "Неверный процент. Допустимо 0-100 или 0.0-1.0"}, status_code=400
        )
    profile = await _admin_partner(session, tg_id)
    if profile is None:
        return ORJSONResponse(content={"success": False, "message": "Партнёр не найден"}, status_code=404)
    await pdb.update_partner_profile(session, profile["id"], partner_percent=normalized, partner_percent_custom=True)
    return ORJSONResponse(content={"success": True, "message": "Процент обновлён", "percent": normalized})


@router.patch("/{tg_id}/balance")
async def update_partner_balance(
    tg_id: str = Path(...),
    amount: float = Query(...),
    mode: str = Query("set"),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    mode = (mode or "set").strip().lower()
    if mode not in {"set", "add", "subtract"}:
        return ORJSONResponse(
            content={"success": False, "message": "Неверный режим. Используйте set, add или subtract"}, status_code=400
        )
    if not 0 <= amount <= 100_000_000:
        return ORJSONResponse(content={"success": False, "message": "Неверная сумма"}, status_code=400)
    profile = await _admin_partner(session, tg_id)
    if profile is None:
        return ORJSONResponse(content={"success": False, "message": "Партнёр не найден"}, status_code=404)
    profile = await pdb.get_partner_profile(session, profile["id"], lock=True)
    current = float(profile["partner_balance"])
    if mode == "subtract" and current < amount:
        return ORJSONResponse(content={"success": False, "message": "Недостаточно средств"}, status_code=400)
    balance = amount if mode == "set" else current + (amount if mode == "add" else -amount)
    await pdb.update_partner_profile(session, profile["id"], partner_balance=balance)
    return ORJSONResponse(content={"success": True, "message": "Баланс обновлён", "balance": balance})


@router.get("/{tg_id}/invited")
async def get_partner_invited(
    tg_id: str = Path(...), identity=Depends(verify_identity_admin), session: AsyncSession = Depends(get_session)
):
    profile = await _admin_partner(session, tg_id)
    rows = await pdb.get_partner_invited(session, profile["id"]) if profile else []
    return ORJSONResponse(content=[_invited_entry(row, admin=True) for row in rows])


def _payout_entry(row: dict) -> dict:
    """Формирует заявку с отдельными ID клиента и Telegram."""
    return {
        "id": row["id"],
        "user_id": row["owner_id"],
        "tg_id": public_tg_id(row["owner_tg_id"]),
        "amount": float(row["amount"]),
        "status": row["status"],
        "created_at": row_dt_iso(row["created_at"]),
        "method": row["payout_method"],
        "destination": row["payout_destination"],
    }


@router.get("/payouts/pending")
async def get_partner_payouts_pending(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    partner_tg_id: int | None = Query(None),
    partner_user_id: int | None = Query(None, ge=1),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    profile = await pdb.resolve_partner_telegram(session, partner_tg_id) if partner_tg_id is not None else None
    if partner_tg_id is not None and profile is None:
        return ORJSONResponse(content={"total": 0, "items": []})
    total, rows = await pdb.list_partner_payouts(
        session,
        user_id=partner_user_id if partner_user_id is not None else profile["id"] if profile else None,
        statuses=("pending",),
        limit=limit,
        offset=offset,
        ascending=True,
    )
    return ORJSONResponse(content={"total": total, "items": [_payout_entry(row) for row in rows]})


@router.get("/payouts/history")
async def get_partner_payouts_history(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    partner_tg_id: int | None = Query(None),
    partner_user_id: int | None = Query(None, ge=1),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    profile = await pdb.resolve_partner_telegram(session, partner_tg_id) if partner_tg_id is not None else None
    if partner_tg_id is not None and profile is None:
        return ORJSONResponse(content={"total": 0, "items": []})
    total, rows = await pdb.list_partner_payouts(
        session,
        user_id=partner_user_id if partner_user_id is not None else profile["id"] if profile else None,
        statuses=("approved", "rejected"),
        limit=limit,
        offset=offset,
    )
    return ORJSONResponse(content={"total": total, "items": [_payout_entry(row) for row in rows]})


@router.post("/payouts/{payout_id}/approve")
async def approve_partner_payout(
    payout_id: int = Path(...), identity=Depends(verify_identity_admin), session: AsyncSession = Depends(get_session)
):
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
    payout_id: int = Path(...), identity=Depends(verify_identity_admin), session: AsyncSession = Depends(get_session)
):
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
    tg_id: str = Path(...), identity=Depends(verify_identity_admin), session: AsyncSession = Depends(get_session)
):
    profile = await _admin_partner(session, tg_id)
    if profile is None:
        return ORJSONResponse(content={"success": False, "message": "Партнёр не найден"}, status_code=404)
    await pdb.update_partner_profile(session, profile["id"], partner_percent=None, partner_percent_custom=False)
    return ORJSONResponse(content={"success": True, "message": "Процент сброшен"})


@router.patch("/{tg_id}/code")
async def update_partner_code(
    tg_id: str = Path(...),
    code: str = Query(...),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    raw = (code or "").strip().lower()
    if not raw or not re.fullmatch(r"[a-z0-9_]{3,32}", raw):
        return ORJSONResponse(
            content={"success": False, "message": "Неверный код. Разрешены a-z, 0-9, _ (3-32 символа)"}, status_code=400
        )
    profile = await _admin_partner(session, tg_id)
    if profile is None:
        return ORJSONResponse(content={"success": False, "message": "Партнёр не найден"}, status_code=404)
    if await pdb.partner_code_taken(session, profile["id"], raw):
        return ORJSONResponse(content={"success": False, "message": "Такой код уже занят"}, status_code=409)
    await pdb.update_partner_profile(session, profile["id"], partner_code=raw)
    return ORJSONResponse(content={"success": True, "message": "Код обновлён", "code": raw})


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
    await pdb.reset_payout_methods(session, disabled)
    return ORJSONResponse(content={"success": True, "message": "Отключённые методы сброшены"}, status_code=200)


@router.get("/{tg_id}/export")
async def export_partner_invites_csv(
    tg_id: str = Path(...), identity=Depends(verify_identity_admin), session: AsyncSession = Depends(get_session)
):
    profile = await _admin_partner(session, tg_id)
    data = await pdb.get_partner_invited(session, profile["id"]) if profile else []
    if not data:
        return ORJSONResponse(content={"success": False, "message": "Нет приглашённых"}, status_code=404)
    buffer = StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["joined_user_id", "joined_tg_id", "created_at"])
    for row in reversed(data):
        writer.writerow([row["user_id"], public_tg_id(row["tg_id"]) or "", row_dt_iso(row["created_at"]) or ""])
    content = buffer.getvalue().encode("utf-8-sig")
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=partner_invites_{tg_id}.csv"},
    )

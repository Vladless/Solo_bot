from base64 import b64encode
from io import BytesIO
from urllib.parse import urlsplit

import qrcode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_session, verify_identity_token
from api.v2.schemas.web_public import (
    ReferralApplyRequest,
    ReferralApplyResponse,
    ReferralConditionsResponse,
    ReferralListEntry,
    ReferralListResponse,
    ReferralQrResponse,
    ReferralTopEntryResponse,
    ReferralTopResponse,
)
from core.bootstrap import BUTTONS_CONFIG
from database import (
    add_referral,
    get_referral_by_referred_id,
    get_user_referral_count,
    identities as idb,
)
from database.access.resolution import resolve_user_optional
from database.models import Referral
from database.referrals import get_referral_position, get_top_referrals
from settings.config import (
    CHECK_REFERRAL_REWARD_ISSUED,
    REFERRAL_BONUS_PERCENTAGES,
    REFERRAL_BUTTON,
    REFERRAL_QR,
    TOP_REFERRAL_BUTTON,
)
from utils.referral_codes import decode_referral_code, encode_referral_code


router = APIRouter()


def _normalize_referrer_code(value: str | None, fallback_tg_id: int | None) -> int | None:
    raw = str(value or "").strip()
    if raw:
        if "/referral/" in raw:
            raw = raw.split("/referral/", 1)[-1]
        if "start=referral_" in raw:
            raw = raw.split("start=referral_", 1)[-1]
        raw = raw.split("?", 1)[0].split("#", 1)[0].strip()
        parsed = decode_referral_code(raw)
        if parsed is not None:
            return parsed
    if fallback_tg_id is not None and int(fallback_tg_id) > 0:
        return int(fallback_tg_id)
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


@router.post("/apply", response_model=ReferralApplyResponse, tags=["Referrals"])
async def apply_referral(
    body: ReferralApplyRequest,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    if not bool(BUTTONS_CONFIG.get("REFERRAL_BUTTON_ENABLED", REFERRAL_BUTTON)):
        raise HTTPException(status_code=403, detail="Реферальная программа отключена")
    billing_uid = await idb.ensure_billing_user_for_identity(session, identity)
    referrer_legacy = _normalize_referrer_code(body.referrer_code, body.referrer_tg_id)
    if referrer_legacy is None:
        raise HTTPException(status_code=400, detail="Приглашение недействительно")
    referrer_u = await resolve_user_optional(session, referrer_legacy)
    if referrer_u is None:
        raise HTTPException(status_code=400, detail="Приглашение недействительно")
    if billing_uid == referrer_u.id:
        raise HTTPException(status_code=400, detail="Нельзя использовать собственную ссылку")
    if await get_referral_by_referred_id(session, billing_uid):
        raise HTTPException(status_code=409, detail="Реферальная связь уже сохранена")
    await add_referral(session, billing_uid, referrer_u.id)
    referred_u = await resolve_user_optional(session, billing_uid)
    if referrer_u.tg_id is not None:
        try:
            from database.web_notifications import notify_web

            await notify_web(
                session,
                tg_id=int(referrer_u.tg_id),
                type="referral_joined",
                title="Ваш реферал присоединился",
                message="Новый пользователь зарегистрировался по вашей реферальной ссылке.",
                data={
                    "referred_tg_id": int(referred_u.tg_id) if referred_u and referred_u.tg_id else None,
                    "referred_user_id": int(billing_uid),
                },
            )
        except Exception:
            pass
    return ReferralApplyResponse(
        ok=True,
        message="Приглашение применено",
        referrer_code=str(referrer_u.id),
        referrer_user_id=int(referrer_u.id),
        referrer_tg_id=referrer_u.tg_id,
        referred_user_id=int(billing_uid),
        referred_tg_id=referred_u.tg_id if referred_u is not None else None,
    )


@router.get("/top", response_model=ReferralTopResponse, tags=["Referrals"])
async def referral_top(
    limit: int = Query(5, ge=1, le=20),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    if not bool(BUTTONS_CONFIG.get("REFERRAL_BUTTON_ENABLED", REFERRAL_BUTTON)):
        raise HTTPException(status_code=403, detail="Реферальная программа отключена")
    if not bool(BUTTONS_CONFIG.get("TOP_REFERRAL_BUTTON_ENABLE", TOP_REFERRAL_BUTTON)):
        raise HTTPException(status_code=403, detail="Топ рефералов отключен в настройках")
    billing_uid = await idb.ensure_billing_user_for_identity(session, identity)
    user_referral_count = int(await get_user_referral_count(session, billing_uid))
    user_position = int(await get_referral_position(session, user_referral_count)) if user_referral_count > 0 else None
    top_rows = await get_top_referrals(session, limit=limit)
    top: list[ReferralTopEntryResponse] = []
    for index, row in enumerate(top_rows, 1):
        referrer_user_id = int(row.get("referrer_user_id") or 0)
        referrals_count = int(row.get("referral_count") or 0)
        display_id = encode_referral_code(referrer_user_id)
        top.append(
            ReferralTopEntryResponse(
                position=index,
                referrer_user_id=referrer_user_id,
                referrals_count=referrals_count,
                display_id=display_id,
            )
        )
    return ReferralTopResponse(
        user_referrals_count=user_referral_count,
        user_position=user_position,
        top=top,
    )


@router.get("/list", response_model=ReferralListResponse, tags=["Referrals"])
async def referral_list(
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    if not bool(BUTTONS_CONFIG.get("REFERRAL_BUTTON_ENABLED", REFERRAL_BUTTON)):
        raise HTTPException(status_code=403, detail="Реферальная программа отключена")
    billing_uid = await idb.ensure_billing_user_for_identity(session, identity)
    rows_stmt = select(Referral).where(Referral.referrer_user_id == int(billing_uid)).limit(limit)
    result = await session.execute(rows_stmt)
    rows = result.scalars().all()
    items = [
        ReferralListEntry(
            referred_user_id=int(r.referred_user_id),
            referred_tg_id=int(r.referred_tg_id) if r.referred_tg_id is not None else None,
            display_id=encode_referral_code(int(r.referred_user_id)),
            reward_issued=bool(r.reward_issued),
        )
        for r in rows
    ]
    return ReferralListResponse(total=len(items), items=items)


@router.get("/qr", response_model=ReferralQrResponse, tags=["Referrals"])
async def referral_qr(
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    if not bool(BUTTONS_CONFIG.get("REFERRAL_BUTTON_ENABLED", REFERRAL_BUTTON)):
        raise HTTPException(status_code=403, detail="Реферальная программа отключена")
    if not bool(BUTTONS_CONFIG.get("REFERRAL_QR_BUTTON_ENABLE", REFERRAL_QR)):
        raise HTTPException(status_code=403, detail="QR реферальной ссылки отключен в настройках")
    billing_uid = await idb.ensure_billing_user_for_identity(session, identity)
    base_url = _resolve_public_base_url(request)
    referral_link = f"{base_url}/referral/{encode_referral_code(int(billing_uid))}"
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(referral_link)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    image_data = b64encode(buffer.getvalue()).decode("ascii")
    return ReferralQrResponse(
        ok=True,
        link=referral_link,
        image_data_url=f"data:image/png;base64,{image_data}",
    )


@router.get("/conditions", response_model=ReferralConditionsResponse, tags=["Referrals"])
async def referral_conditions(
    identity=Depends(verify_identity_token),
):
    if not bool(BUTTONS_CONFIG.get("REFERRAL_BUTTON_ENABLED", REFERRAL_BUTTON)):
        raise HTTPException(status_code=403, detail="Реферальная программа отключена")
    del identity
    level_lines: list[str] = []
    for level in sorted(REFERRAL_BONUS_PERCENTAGES.keys()):
        value = REFERRAL_BONUS_PERCENTAGES[level]
        if isinstance(value, float):
            label = f"{int(value * 100)}% от суммы оплаты"
        else:
            label = f"{float(value):g} RUB"
        level_lines.append(f"{level} уровень: {label}")
    one_time_mode = bool(CHECK_REFERRAL_REWARD_ISSUED)
    bonus_mode = "one_time" if one_time_mode else "each_payment"
    bonus_mode_label = (
        "Бонус за первую успешную оплату реферала" if one_time_mode else "Бонус за каждую успешную оплату реферала"
    )
    rules = [
        "Бонус начисляется только за реальных приглашённых пользователей.",
        "Нельзя использовать собственную реферальную ссылку.",
        "Реферальную связь можно применить только один раз.",
        "Размер бонуса зависит от уровня реферальной программы.",
    ]
    return ReferralConditionsResponse(
        title="Условия реферальной программы",
        summary=f"Режим начисления: {bonus_mode_label}.",
        bonus_mode=bonus_mode,
        bonus_mode_label=bonus_mode_label,
        level_lines=level_lines,
        rules=rules,
    )

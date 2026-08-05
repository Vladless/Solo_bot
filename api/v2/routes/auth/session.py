import re

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import String, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import (
    AUTH_COOKIE_NAME,
    bind_identity_actor,
    clear_auth_cookie,
    get_request_actor,
    get_session,
    hash_token,
    resolve_identity_role,
    verify_identity_token,
)
from api.v2.routes.auth._common import _resolve_partner_snapshot
from api.v2.schemas.identities import (
    ChangePasswordRequest,
    IdentityResponse,
    IdentitySessionItem,
    IdentitySessionsResponse,
    SetPasswordRequest,
)
from api.v2.schemas.web_public import (
    AccountSearchHit,
    AccountSearchResponse,
    AccountSummaryResponse,
)
from database import (
    get_balance,
    get_keys,
    get_trial,
    identities as idb,
    identity_sessions as idsess,
)
from database.models import CouponUsage, Gift, GiftUsage, IdentityNotifPref, Key, Payment, SubscriptionEvent, WebNotification
from database.referrals import get_referral_stats
from database.web_notifications import count_unread_for_identity
from logger import logger
from utils.referral_codes import encode_referral_code


router = APIRouter()


@router.get("/me", response_model=IdentityResponse)
async def me(
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    """Текущая идентичность по HttpOnly cookie `auth_token`."""
    data = IdentityResponse.model_validate(identity)
    data.role = await resolve_identity_role(session, identity)
    return data


def _current_token_hash(request: Request) -> str | None:
    raw = request.cookies.get(AUTH_COOKIE_NAME)
    if not raw or not raw.strip():
        return None
    return hash_token(raw.strip())


@router.get("/sessions", response_model=IdentitySessionsResponse)
async def list_my_sessions(
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    """Возвращает активные сессии текущей identity (все устройства)."""
    current_hash = _current_token_hash(request)
    rows = await idsess.list_sessions_for_identity(session, identity.id)
    items = [
        IdentitySessionItem(
            id=row.id,
            device_label=row.device_label,
            ip=row.ip,
            created_at=row.created_at,
            last_seen_at=row.last_seen_at,
            expires_at=row.expires_at,
            is_current=bool(current_hash and row.token_hash == current_hash),
        )
        for row in rows
    ]
    return IdentitySessionsResponse(sessions=items)


@router.delete("/sessions/{session_id}")
async def revoke_my_session(
    session_id: str,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    """Удаляет одну сессию текущей identity. Если удалена текущая — очищаем cookie."""
    ok = await idsess.delete_session_by_id(session, session_id=session_id, identity_id=identity.id)
    if not ok:
        raise HTTPException(status_code=404, detail="Сессия не найдена")
    current_hash = _current_token_hash(request)
    rows = await idsess.list_sessions_for_identity(session, identity.id)
    if current_hash and not any(r.token_hash == current_hash for r in rows):
        clear_auth_cookie(response, request)
    return {"ok": True}


@router.post("/sessions/revoke-others")
async def revoke_other_sessions(
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    """Удаляет все сессии текущей identity кроме текущей."""
    current_hash = _current_token_hash(request)
    if not current_hash:
        raise HTTPException(status_code=400, detail="Текущая сессия не определена")
    removed = await idsess.delete_other_sessions(session, identity_id=identity.id, keep_token_hash=current_hash)
    return {"ok": True, "removed": removed}


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    """Удаляет текущую сессию из БД и очищает auth cookie. Не требует валидной сессии."""
    raw = request.cookies.get(AUTH_COOKIE_NAME)
    if raw and raw.strip():
        try:
            await idsess.delete_session_by_token_hash(session, hash_token(raw.strip()))
        except Exception:
            pass
    clear_auth_cookie(response, request)
    return {"ok": True}


@router.post("/me/onboarding/complete", response_model=IdentityResponse)
async def onboarding_complete(
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    """Отмечает, что админ прошёл/скипнул онбординг-тур."""
    from datetime import datetime as _dt

    if identity.onboarding_completed_at is None:
        identity.onboarding_completed_at = _dt.utcnow()
    return IdentityResponse.model_validate(identity)


@router.post("/me/onboarding/reset", response_model=IdentityResponse)
async def onboarding_reset(
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    """Сбрасывает флаг онбординга — туториал запустится снова."""
    identity.onboarding_completed_at = None
    identity.onboarding_stage = "landing"
    return IdentityResponse.model_validate(identity)


_ONBOARDING_STAGES = {"landing", "header", "cabinet", "flow", "elements", "done"}


@router.post("/me/onboarding/stage", response_model=IdentityResponse)
async def onboarding_set_stage(
    body: dict,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    """Переводит админа на указанный этап онбординга."""
    from datetime import datetime as _dt

    stage = str(body.get("stage") or "").strip()
    if stage not in _ONBOARDING_STAGES:
        raise HTTPException(status_code=400, detail="Неизвестный этап онбординга")
    identity.onboarding_stage = stage
    if stage == "done" and identity.onboarding_completed_at is None:
        identity.onboarding_completed_at = _dt.utcnow()
    return IdentityResponse.model_validate(identity)


@router.get("/summary", response_model=AccountSummaryResponse)
async def auth_summary(
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    actor = get_request_actor(request)
    billing_user_id = actor.billing_user_id if actor and actor.billing_user_id is not None else None
    if billing_user_id is None:
        try:
            async with session.begin_nested():
                billing_user_id = await idb.ensure_billing_user_for_identity(session, identity)
        except Exception as exc:
            logger.warning("[auth_summary] billing_user_id не определён: {}", exc)
            billing_user_id = None

    async def _safe(factory, default):
        try:
            async with session.begin_nested():
                return await factory()
        except Exception as exc:
            logger.warning("[auth_summary] пропущена агрегация: {}", exc)
            return default

    async def _count(model, *conds) -> int:
        r = await session.execute(select(func.count()).select_from(model).where(*conds))
        return int(r.scalar_one() or 0)

    balance = float(await _safe(lambda: get_balance(session, billing_user_id), 0.0) or 0.0)
    trial_status = int(await _safe(lambda: get_trial(session, billing_user_id), 0) or 0)
    keys = await _safe(lambda: get_keys(session, billing_user_id), None)
    keys_total = len(keys) if keys else 0
    from core.redis_cache import cache_get, cache_key, cache_set

    counters_key = cache_key("summary_counters", billing_user_id) if billing_user_id is not None else None
    counters = await cache_get(counters_key) if counters_key else None
    if not isinstance(counters, dict):
        gifts_sent = await _safe(lambda: _count(Gift, Gift.sender_user_id == billing_user_id), 0)
        gifts_claimed = await _safe(lambda: _count(GiftUsage, GiftUsage.user_id == billing_user_id), 0)
        coupons_used = await _safe(lambda: _count(CouponUsage, CouponUsage.user_id == billing_user_id), 0)
        partner = await _safe(lambda: _resolve_partner_snapshot(session, int(billing_user_id)), {}) or {}
        counters = {
            "gifts_sent": int(gifts_sent),
            "gifts_claimed": int(gifts_claimed),
            "coupons_used": int(coupons_used),
            "partner": partner,
        }
        if counters_key:
            await cache_set(counters_key, counters, 60)
    gifts_sent = counters["gifts_sent"]
    gifts_claimed = counters["gifts_claimed"]
    coupons_used = counters["coupons_used"]
    partner = counters.get("partner") or {}
    ref = await _safe(lambda: get_referral_stats(session, billing_user_id), {}) or {}
    unread_notifications = int(await _safe(lambda: count_unread_for_identity(session, identity.id), 0) or 0)
    return AccountSummaryResponse(
        identity_id=identity.id,
        email=identity.email,
        tg_id=identity.tg_id,
        linked_telegram=identity.tg_id is not None,
        created_at=identity.created_at.isoformat() if identity.created_at else None,
        password_set=bool(identity.password_set),
        referral_code=encode_referral_code(int(billing_user_id)) if billing_user_id is not None else "",
        balance=balance,
        trial_status=int(trial_status),
        keys_total=keys_total,
        referrals_total=int(ref.get("total_referrals") or 0),
        referrals_active=int(ref.get("active_referrals") or 0),
        referral_bonus_total=float(ref.get("total_referral_bonus") or 0),
        gifts_sent=int(gifts_sent),
        gifts_claimed=int(gifts_claimed),
        coupons_used=int(coupons_used),
        partner_enabled=bool(partner.get("partner_enabled", False)),
        partner_code=str(partner.get("partner_code") or ""),
        partner_balance=float(partner.get("partner_balance") or 0.0),
        partner_percent=float(partner.get("partner_percent") or 0.0),
        partner_percent_custom=bool(partner.get("partner_percent_custom", False)),
        partner_referred_total=int(partner.get("partner_referred_total") or 0),
        partner_referred_paid=int(partner.get("partner_referred_paid") or 0),
        partner_payout_method=partner.get("partner_payout_method"),
        unread_notifications=int(unread_notifications),
    )


class MyPaymentItem(BaseModel):
    id: int
    payment_id: str | None
    amount: float
    currency: str
    status: str
    provider: str
    created_at: str | None
    purpose: str | None


class MyPaymentsResponse(BaseModel):
    ok: bool = True
    payments: list[MyPaymentItem]


@router.get("/me/payments", response_model=MyPaymentsResponse)
async def my_payments(
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
    limit: int = 50,
):
    """История платежей текущего юзера. Привязка через Identity → User → Payment."""
    actor = get_request_actor(request)
    billing_user_id = actor.billing_user_id if actor and actor.billing_user_id is not None else None
    if billing_user_id is None:
        billing_user_id = await idb.ensure_billing_user_for_identity(session, identity)
    if billing_user_id is None:
        return MyPaymentsResponse(ok=True, payments=[])
    safe_limit = max(1, min(200, int(limit) if limit else 50))
    from core.redis_cache import cache_get, cache_key, cache_set

    payments_key = cache_key("my_payments", billing_user_id, safe_limit)
    cached_payments = await cache_get(payments_key)
    if isinstance(cached_payments, list):
        return MyPaymentsResponse(ok=True, payments=[MyPaymentItem.model_validate(p) for p in cached_payments])
    rows = await session.execute(
        select(Payment).where(Payment.user_id == billing_user_id).order_by(Payment.created_at.desc()).limit(safe_limit)
    )
    payments = rows.scalars().all()
    dated: list[tuple] = []
    for p in payments:
        meta = p.metadata_ if isinstance(p.metadata_, dict) else None
        purpose = None
        if meta:
            purpose = meta.get("purpose") or meta.get("description") or meta.get("tariff_name")
            if purpose is not None:
                purpose = str(purpose)
        dated.append((
            p.created_at,
            MyPaymentItem(
                id=int(p.id),
                payment_id=str(p.payment_id) if p.payment_id else None,
                amount=float(p.amount or 0),
                currency=str(p.currency or "RUB"),
                status=str(p.status or ""),
                provider=str(p.payment_system or ""),
                created_at=p.created_at.isoformat() if p.created_at else None,
                purpose=purpose,
            ),
        ))

    gift_rows = await session.execute(
        select(Gift).where(Gift.sender_user_id == billing_user_id).order_by(Gift.created_at.desc()).limit(safe_limit)
    )
    for g in gift_rows.scalars().all():
        try:
            gift_key = -int(str(g.gift_id)[:8], 16)
        except (ValueError, TypeError):
            gift_key = 0
        dated.append((
            g.created_at,
            MyPaymentItem(
                id=gift_key,
                payment_id=str(g.gift_id) if g.gift_id else None,
                amount=-float(g.selected_price_rub or 0),
                currency="RUB",
                status="success",
                provider="gift",
                created_at=g.created_at.isoformat() if g.created_at else None,
                purpose="🎁 Подарочная подписка",
            ),
        ))

    spend_purposes = {
        "created": "Покупка подписки",
        "renewed": "Продление подписки",
        "addons": "Докупка опций",
    }
    spend_rows = await session.execute(
        select(SubscriptionEvent)
        .where(SubscriptionEvent.user_id == billing_user_id)
        .where(SubscriptionEvent.event_type.in_(("created", "renewed", "addons")))
        .where(SubscriptionEvent.price_rub > 0)
        .where((SubscriptionEvent.source.is_(None)) | (SubscriptionEvent.source != "backfill"))
        .order_by(SubscriptionEvent.created_at.desc())
        .limit(safe_limit)
    )
    for ev in spend_rows.scalars().all():
        dated.append((
            ev.created_at,
            MyPaymentItem(
                id=-(1_000_000_000 + int(ev.id)),
                payment_id=str(ev.client_id) if ev.client_id else None,
                amount=-float(ev.price_rub or 0),
                currency="RUB",
                status="success",
                provider="spend",
                created_at=ev.created_at.isoformat() if ev.created_at else None,
                purpose=spend_purposes.get(ev.event_type, "Списание"),
            ),
        ))

    dated.sort(key=lambda r: (r[0] is not None, r[0]), reverse=True)
    items = [item for _, item in dated[:safe_limit]]
    await cache_set(payments_key, [item.model_dump() for item in items], 60)
    return MyPaymentsResponse(ok=True, payments=items)


def _esc(value: object) -> str:
    s = "" if value is None else str(value)
    return (
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")
    )


@router.get("/me/payments/{payment_id}/invoice", response_class=HTMLResponse)
async def get_my_payment_invoice(
    payment_id: int = Path(..., ge=1),
    request: Request = None,  # type: ignore[assignment]
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    """HTML-инвойс по конкретному платежу. Браузер может сохранить как PDF (Cmd+P → Save as PDF)."""
    actor = get_request_actor(request) if request is not None else None
    billing_user_id = actor.billing_user_id if actor and actor.billing_user_id is not None else None
    if billing_user_id is None:
        billing_user_id = await idb.ensure_billing_user_for_identity(session, identity)
    if billing_user_id is None:
        raise HTTPException(status_code=404, detail="Платёж не найден")
    payment = (
        await session.execute(
            select(Payment).where(Payment.id == payment_id, Payment.user_id == billing_user_id).limit(1)
        )
    ).scalar_one_or_none()
    if payment is None:
        raise HTTPException(status_code=404, detail="Платёж не найден")
    meta = payment.metadata_ if isinstance(payment.metadata_, dict) else {}
    purpose = ""
    if meta:
        v = meta.get("purpose") or meta.get("description") or meta.get("tariff_name")
        if v is not None:
            purpose = str(v)
    created = payment.created_at.strftime("%d.%m.%Y %H:%M") if payment.created_at else "—"
    amount_value = float(payment.amount or 0)
    currency = str(payment.currency or "RUB").upper()
    status_raw = str(payment.status or "")
    status_norm = status_raw.lower()
    status_label = (
        "ОПЛАЧЕН"
        if status_norm in {"completed", "success", "paid"}
        else "ОЖИДАЕТ"
        if status_norm in {"pending", "processing"}
        else "ОТКЛОНЁН"
    )
    provider = str(payment.payment_system or "").upper() or "—"
    user_label = identity.email or (f"tg · {identity.tg_id}" if identity.tg_id else identity.id)
    payment_identifier = str(payment.payment_id).strip() if payment.payment_id else ""
    title_suffix = payment_identifier if payment_identifier else f"#{payment.id}"
    html = f"""<!DOCTYPE html>
<html lang=\"ru\">
<head>
  <meta charset=\"utf-8\" />
  <title>Квитанция {_esc(title_suffix)}</title>
  <style>
    @page {{ size: A4; margin: 18mm; }}
    body {{ font-family: 'JetBrains Mono', ui-monospace, monospace; color: #111; background: #fff; max-width: 720px; margin: 0 auto; padding: 24px; }}
    h1 {{ font-size: 24px; letter-spacing: -0.02em; margin: 0 0 4px; text-transform: uppercase; }}
    .sub {{ color: #888; font-size: 11px; letter-spacing: 0.18em; text-transform: uppercase; margin-bottom: 32px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    td {{ padding: 11px 0; border-bottom: 1px dashed #ddd; vertical-align: top; }}
    td.k {{ color: #888; width: 35%; letter-spacing: 0.08em; text-transform: uppercase; font-size: 11px; }}
    td.v {{ font-weight: 600; }}
    .amount {{ font-size: 32px; font-weight: 800; letter-spacing: -0.02em; margin: 24px 0 8px; }}
    .badge {{ display: inline-block; padding: 4px 10px; border: 1px solid #111; font-size: 11px; letter-spacing: 0.14em; text-transform: uppercase; }}
    .footer {{ margin-top: 48px; font-size: 10px; color: #aaa; letter-spacing: 0.12em; text-transform: uppercase; text-align: center; }}
    @media print {{ .no-print {{ display: none; }} }}
    .print-btn {{ position: fixed; top: 16px; right: 16px; padding: 10px 16px; background: #111; color: #fff; border: 0; cursor: pointer; font-family: inherit; font-size: 12px; letter-spacing: 0.1em; text-transform: uppercase; }}
  </style>
</head>
<body>
  <button class=\"print-btn no-print\" onclick=\"window.print()\">Сохранить PDF</button>
  <h1>Квитанция {_esc(title_suffix)}</h1>
  <div class=\"sub\">// {_esc(created)}</div>
  <div class=\"amount\">{amount_value:,.2f} {_esc(currency)}</div>
  <span class=\"badge\">{_esc(status_label)}</span>
  <table>
    <tr><td class=\"k\">Назначение</td><td class=\"v\">{_esc(purpose) or "—"}</td></tr>
    <tr><td class=\"k\">Провайдер</td><td class=\"v\">{_esc(provider)}</td></tr>
    <tr><td class=\"k\">Дата</td><td class=\"v\">{_esc(created)}</td></tr>
    <tr><td class=\"k\">Получатель</td><td class=\"v\">{_esc(user_label)}</td></tr>
    <tr><td class=\"k\">Идентификатор платежа</td><td class=\"v\" style=\"font-size:11px;color:#666\">{_esc(payment_identifier) if payment_identifier else "—"}</td></tr>
  </table>
  <div class=\"footer\">Документ сгенерирован автоматически. Не требует подписи и печати.</div>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=200)


class NotifChannelPref(BaseModel):
    channel: str
    enabled: bool


class NotifChannelPrefsResponse(BaseModel):
    ok: bool = True
    channels: list[NotifChannelPref]


class NotifChannelPrefsUpdateRequest(BaseModel):
    channels: list[NotifChannelPref]


_NOTIF_CHANNEL_RE = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")


@router.get("/me/notification-prefs", response_model=NotifChannelPrefsResponse)
async def get_my_notification_prefs(
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    rows = (
        (await session.execute(select(IdentityNotifPref).where(IdentityNotifPref.identity_id == identity.id)))
        .scalars()
        .all()
    )
    return NotifChannelPrefsResponse(
        ok=True,
        channels=[NotifChannelPref(channel=str(r.channel), enabled=bool(r.enabled)) for r in rows],
    )


@router.put("/me/notification-prefs", response_model=NotifChannelPrefsResponse)
async def set_my_notification_prefs(
    body: NotifChannelPrefsUpdateRequest,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    for entry in body.channels:
        channel = str(entry.channel or "").strip()
        if not channel or not _NOTIF_CHANNEL_RE.match(channel):
            raise HTTPException(status_code=422, detail=f"Некорректный канал: {channel!r}")
        existing = (
            await session.execute(
                select(IdentityNotifPref).where(
                    IdentityNotifPref.identity_id == identity.id,
                    IdentityNotifPref.channel == channel,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(IdentityNotifPref(identity_id=identity.id, channel=channel, enabled=bool(entry.enabled)))
        else:
            existing.enabled = bool(entry.enabled)
    await session.flush()
    rows = (
        (await session.execute(select(IdentityNotifPref).where(IdentityNotifPref.identity_id == identity.id)))
        .scalars()
        .all()
    )
    return NotifChannelPrefsResponse(
        ok=True,
        channels=[NotifChannelPref(channel=str(r.channel), enabled=bool(r.enabled)) for r in rows],
    )


@router.get("/me/search", response_model=AccountSearchResponse)
async def my_search(
    q: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
    limit: int = 8,
):
    """Поиск по подпискам, платежам, уведомлениям текущего user'а. Простое ILIKE."""
    query_raw = (q or "").strip()
    if len(query_raw) < 2:
        return AccountSearchResponse(query=query_raw, hits=[], total=0)
    actor = get_request_actor(request)
    billing_user_id = actor.billing_user_id if actor and actor.billing_user_id is not None else None
    if billing_user_id is None:
        billing_user_id = await idb.ensure_billing_user_for_identity(session, identity)
    if billing_user_id is None:
        return AccountSearchResponse(query=query_raw, hits=[], total=0)
    safe_limit = max(1, min(20, int(limit) if limit else 8))
    pattern = f"%{query_raw.lower()}%"
    hits: list[AccountSearchHit] = []

    # Keys: alias / email / server_id
    keys_rows = (
        (
            await session.execute(
                select(Key)
                .where(Key.user_id == billing_user_id)
                .where(
                    func.lower(func.coalesce(Key.alias, "")).like(pattern)
                    | func.lower(func.coalesce(Key.email, "")).like(pattern)
                    | func.lower(func.coalesce(Key.server_id, "")).like(pattern)
                    | func.lower(func.coalesce(Key.client_id, "")).like(pattern)
                )
                .limit(safe_limit)
            )
        )
        .scalars()
        .all()
    )
    for k in keys_rows:
        label = (k.alias or k.email or k.client_id or "").strip() or "—"
        sublabel = (k.server_id or "").strip() or "—"
        hits.append(
            AccountSearchHit(
                kind="subscription", label=label, sublabel=sublabel, href="/dashboard/keys", meta=str(k.client_id)
            )
        )

    # Payments: provider / metadata.purpose
    payments_rows = (
        (
            await session.execute(
                select(Payment)
                .where(Payment.user_id == billing_user_id)
                .where(
                    func.lower(func.coalesce(Payment.payment_system, "")).like(pattern)
                    | func.cast(Payment.metadata_, String).ilike(pattern)
                )
                .order_by(Payment.created_at.desc())
                .limit(safe_limit)
            )
        )
        .scalars()
        .all()
    )
    for p in payments_rows:
        meta = p.metadata_ if isinstance(p.metadata_, dict) else None
        purpose = ""
        if meta:
            v = meta.get("purpose") or meta.get("description") or meta.get("tariff_name")
            if v is not None:
                purpose = str(v)
        amount_label = f"{float(p.amount or 0):,.0f} {(p.currency or 'RUB').upper()}"
        hits.append(
            AccountSearchHit(
                kind="payment",
                label=purpose or amount_label,
                sublabel=f"{(p.payment_system or '').upper()} · {amount_label}",
                href="/dashboard",
                meta=str(p.id),
            )
        )

    # Notifications: title / message
    notif_rows = (
        (
            await session.execute(
                select(WebNotification)
                .where(WebNotification.identity_id == identity.id)
                .where(
                    func.lower(WebNotification.title).like(pattern) | func.lower(WebNotification.message).like(pattern)
                )
                .order_by(WebNotification.created_at.desc())
                .limit(safe_limit)
            )
        )
        .scalars()
        .all()
    )
    for n in notif_rows:
        hits.append(
            AccountSearchHit(
                kind="notification",
                label=str(n.title or "—"),
                sublabel=(str(n.message or "")[:80]),
                href="/dashboard/notifications",
                meta=str(n.id),
            )
        )

    return AccountSearchResponse(query=query_raw, hits=hits, total=len(hits))


@router.post("/set-password")
async def set_password(
    body: SetPasswordRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    if body.password != body.password_confirm:
        raise HTTPException(status_code=400, detail="Пароли не совпадают")
    updated = await idb.set_initial_password(session, identity.id, body.password)
    if not updated:
        raise HTTPException(
            status_code=409,
            detail="Пароль уже установлен или аккаунт недоступен",
        )
    await bind_identity_actor(request, session, updated)
    return {"ok": True}


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    if body.password != body.password_confirm:
        raise HTTPException(status_code=400, detail="Новые пароли не совпадают")
    err = await idb.change_identity_password(
        session,
        identity.id,
        body.current_password,
        body.password,
    )
    if err == "no_password":
        raise HTTPException(
            status_code=409,
            detail="Пароль ещё не установлен. Сначала задайте пароль в кабинете.",
        )
    if err == "wrong_password":
        raise HTTPException(status_code=401, detail="Неверный текущий пароль")
    refreshed = await idb.get_identity_by_id(session, identity.id)
    if refreshed:
        await bind_identity_actor(request, session, refreshed)
    current_hash = _current_token_hash(request)
    if current_hash:
        await idsess.delete_other_sessions(session, identity_id=identity.id, keep_token_hash=current_hash)
    return {"ok": True}

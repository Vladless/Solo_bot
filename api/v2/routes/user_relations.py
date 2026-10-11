from datetime import timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import Response
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.admin_permissions import require_admin_action
from api.depends import get_session, verify_identity_admin
from database.access.resolution import UserId, get_user_by_id, public_tg_id
from database.gifts import delete_gift_for_user
from database.models import Gift, GiftUsage, Key, Referral, SubscriptionEvent, Tariff, User
from database.subscription_events import get_user_subscription_history
from filters.permissions import PERM_GIFTS, PERM_KEY_VIEW, PERM_USERS
from services.formatting import get_site_gift_link
from services.user_audit import record_admin_user_event, user_action_history
from utils.csv_export import export_referrals_csv


router = APIRouter()


def _iso_utc(value) -> str | None:
    """Передаёт время базы с явным часовым поясом."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


async def _owner(session, user_ref: int) -> User:
    """Находит клиента строго по каноническому идентификатору."""
    user = await get_user_by_id(session, UserId(user_ref))
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return user


def _brief(user: User | None) -> dict | None:
    """Возвращает профиль без платёжных и личных реквизитов."""
    if user is None:
        return None
    return {
        "user_id": int(user.id),
        "tg_id": public_tg_id(user.tg_id),
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
    }


def _page(items: list, total: int, page: int, limit: int) -> dict:
    """Собирает постраничный ответ."""
    return {"items": items, "total": int(total), "page": page, "limit": limit, "has_next": (page + 1) * limit < total}


def _subscription_owner(user: User):
    """Учитывает старый Telegram только у записей без владельца."""
    refs = [SubscriptionEvent.user_id == user.id]
    tg_id = public_tg_id(user.tg_id)
    if tg_id is not None:
        refs.append(and_(SubscriptionEvent.user_id.is_(None), SubscriptionEvent.tg_id == tg_id))
    return or_(*refs)


async def _gift_delete_allowed(session, identity) -> bool:
    """Проверяет право удаления без запрета просмотра списка."""
    try:
        await require_admin_action(session, identity, PERM_GIFTS)
    except HTTPException as exc:
        if exc.status_code != 403:
            raise
        return False
    return True


def _gift_filter(user: User, direction: str = "sent"):
    """Выбирает подарки по каноническим владельцам и активациям."""
    if direction == "sent":
        return Gift.sender_user_id == user.id
    return or_(
        Gift.recipient_user_id == user.id,
        exists(select(GiftUsage.gift_id).where(GiftUsage.gift_id == Gift.gift_id, GiftUsage.user_id == user.id)),
    )


def _gift_item(gift: Gift, *, tariff_name: str | None, usage_count: int, can_delete: bool) -> dict:
    """Готовит подарок для карточки клиента."""
    return {
        "gift_id": gift.gift_id,
        "sender_user_id": gift.sender_user_id,
        "recipient_user_id": gift.recipient_user_id,
        "sender_tg_id": public_tg_id(gift.sender_tg_id),
        "recipient_tg_id": public_tg_id(gift.recipient_tg_id),
        "selected_months": gift.selected_months,
        "created_at": _iso_utc(gift.created_at),
        "expiry_time": _iso_utc(gift.expiry_time),
        "is_used": bool(gift.is_used),
        "is_unlimited": bool(gift.is_unlimited),
        "max_usages": gift.max_usages,
        "tariff_id": gift.tariff_id,
        "tariff_name": tariff_name,
        "selected_device_limit": gift.selected_device_limit,
        "selected_traffic_gb": gift.selected_traffic_gb,
        "selected_price_rub": gift.selected_price_rub,
        "site_gift_link": get_site_gift_link(gift.gift_id),
        "gift_link": gift.gift_link,
        "usage_count": int(usage_count),
        "can_delete": can_delete,
    }


@router.get("/{user_ref}/actions")
async def actions(
    identity: Annotated[Any, Depends(verify_identity_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    user_ref: int = Path(...),
    channel: Literal["all", "api", "telegram"] = Query("all"),
    category: Literal["all", "balance", "auth", "payments", "subscriptions", "marketing"] = Query("all"),
    page: int = Query(0, ge=0, le=200),
    limit: int = Query(20, ge=1, le=100),
):
    """Показывает действия клиента и изменения администратора."""
    await require_admin_action(session, identity, PERM_USERS)
    user = await _owner(session, user_ref)
    return await user_action_history(session, user, channel=channel, category=category, page=page, limit=limit)


@router.get("/{user_ref}/subscription-history")
async def subscription_history(
    identity: Annotated[Any, Depends(verify_identity_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    user_ref: int = Path(...),
    page: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """Показывает подписки клиента, включая удалённые."""
    await require_admin_action(session, identity, PERM_KEY_VIEW)
    user = await _owner(session, user_ref)
    history = await get_user_subscription_history(session, user_id=user.id, tg_id=public_tg_id(user.tg_id))
    current = (
        (await session.execute(select(Key).where(Key.user_id == user.id).order_by(Key.created_at.desc())))
        .scalars()
        .all()
    )
    current_map = {str(key.client_id): key for key in current}
    history_ids = {str(item["client_id"]) for item in history}
    for key in current:
        if str(key.client_id) not in history_ids:
            history.append({
                "client_id": str(key.client_id),
                "first_at": None,
                "last_at": None,
                "renewals": 0,
                "tariff_id": key.tariff_id,
                "server_id": key.server_id,
                "max_expiry": key.expiry_time,
                "last_event": "created",
            })
    selected = history[page * limit : (page + 1) * limit]
    tariff_ids = {item["tariff_id"] for item in selected if item["tariff_id"] is not None}
    tariff_names = (
        dict((await session.execute(select(Tariff.id, Tariff.name).where(Tariff.id.in_(tariff_ids)))).all())
        if tariff_ids
        else {}
    )
    items = []
    for row in selected:
        current_key = current_map.get(str(row["client_id"]))
        status = (
            "current"
            if current_key is not None
            else row["last_event"]
            if row["last_event"] in {"deleted", "expired"}
            else "completed"
        )
        items.append({
            **row,
            "first_at": _iso_utc(row["first_at"]),
            "last_at": _iso_utc(row["last_at"]),
            "tariff_name": tariff_names.get(row["tariff_id"]),
            "status": status,
            "is_current": current_key is not None,
            "is_frozen": bool(current_key.is_frozen) if current_key is not None else False,
            "expiry_time": current_key.expiry_time if current_key is not None else row["max_expiry"],
        })
    return _page(items, len(history), page, limit)


@router.get("/{user_ref}/subscription-events")
async def subscription_events(
    identity: Annotated[Any, Depends(verify_identity_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    user_ref: int = Path(...),
    client_id: str | None = Query(None, max_length=128),
    page: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """Возвращает события жизненного цикла подписок клиента."""
    await require_admin_action(session, identity, PERM_KEY_VIEW)
    user = await _owner(session, user_ref)
    stmt = (
        select(SubscriptionEvent, Tariff.name)
        .outerjoin(Tariff, Tariff.id == SubscriptionEvent.tariff_id)
        .where(_subscription_owner(user))
    )
    if client_id:
        stmt = stmt.where(SubscriptionEvent.client_id == client_id)
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    rows = (
        await session.execute(
            stmt.order_by(SubscriptionEvent.created_at.desc(), SubscriptionEvent.id.desc())
            .offset(page * limit)
            .limit(limit)
        )
    ).all()
    items = [
        {
            "id": int(event.id),
            "event_type": event.event_type,
            "client_id": event.client_id,
            "tariff_id": event.tariff_id,
            "tariff_name": tariff_name,
            "server_id": event.server_id,
            "price_rub": event.price_rub,
            "duration_days": event.duration_days,
            "expiry_time": event.expiry_time,
            "was_expired": event.was_expired,
            "source": event.source,
            "created_at": _iso_utc(event.created_at),
        }
        for event, tariff_name in rows
    ]
    return _page(items, total, page, limit)


@router.get("/{user_ref}/referrals")
async def referrals(
    identity: Annotated[Any, Depends(verify_identity_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    user_ref: int = Path(...),
    page: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """Показывает пригласившего и прямых рефералов клиента."""
    await require_admin_action(session, identity, PERM_USERS)
    user = await _owner(session, user_ref)
    referrer = (
        await session.execute(
            select(User)
            .join(Referral, Referral.referrer_user_id == User.id)
            .where(Referral.referred_user_id == user.id)
            .order_by(User.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    stmt = (
        select(User, Referral.reward_issued)
        .join(Referral, Referral.referred_user_id == User.id)
        .where(Referral.referrer_user_id == user.id)
    )
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    rows = (await session.execute(stmt.order_by(User.id).offset(page * limit).limit(limit))).all()
    return {
        **_page([{**_brief(owner), "reward_issued": bool(reward)} for owner, reward in rows], total, page, limit),
        "referrer": _brief(referrer),
    }


@router.get("/{user_ref}/referrals/export")
async def referrals_export(
    identity: Annotated[Any, Depends(verify_identity_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    user_ref: int = Path(...),
):
    """Выгружает прямых рефералов через общий экспорт бота."""
    await require_admin_action(session, identity, PERM_USERS)
    user = await _owner(session, user_ref)
    exported = await export_referrals_csv(UserId(user.id), session)
    if exported is None:
        raise HTTPException(status_code=404, detail="У клиента пока нет рефералов")
    return Response(
        content=exported.data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="referrals_{user.id}.csv"'},
    )


@router.get("/{user_ref}/gifts")
async def gifts(
    identity: Annotated[Any, Depends(verify_identity_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    user_ref: int = Path(...),
    direction: Literal["sent", "received"] = Query("sent"),
    page: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """Показывает отправленные и полученные подарки клиента."""
    await require_admin_action(session, identity, PERM_USERS)
    user = await _owner(session, user_ref)
    can_delete = await _gift_delete_allowed(session, identity)
    stmt = select(Gift, Tariff.name).outerjoin(Tariff, Tariff.id == Gift.tariff_id).where(_gift_filter(user, direction))
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    rows = (
        await session.execute(stmt.order_by(Gift.created_at.desc(), Gift.gift_id).offset(page * limit).limit(limit))
    ).all()
    gift_ids = [gift.gift_id for gift, _tariff_name in rows]
    usage_counts = (
        dict(
            (
                await session.execute(
                    select(GiftUsage.gift_id, func.count())
                    .where(GiftUsage.gift_id.in_(gift_ids))
                    .group_by(GiftUsage.gift_id)
                )
            ).all()
        )
        if gift_ids
        else {}
    )
    items = [
        _gift_item(
            gift,
            tariff_name=tariff_name,
            usage_count=usage_counts.get(gift.gift_id, 0),
            can_delete=can_delete and gift.sender_user_id == user.id,
        )
        for gift, tariff_name in rows
    ]
    return _page(items, total, page, limit)


@router.get("/{user_ref}/gifts/{gift_id}")
async def gift_detail(
    identity: Annotated[Any, Depends(verify_identity_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    user_ref: int = Path(...),
    gift_id: str = Path(..., min_length=1, max_length=128),
    usage_page: int = Query(0, ge=0),
    usage_limit: int = Query(100, ge=1, le=100),
):
    """Показывает подарок клиента и его активации."""
    await require_admin_action(session, identity, PERM_USERS)
    user = await _owner(session, user_ref)
    row = (
        await session.execute(
            select(Gift, Tariff.name)
            .outerjoin(Tariff, Tariff.id == Gift.tariff_id)
            .where(Gift.gift_id == gift_id, or_(_gift_filter(user), _gift_filter(user, "received")))
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Подарок клиента не найден")
    gift, tariff_name = row
    usages_total = (
        await session.execute(select(func.count()).select_from(GiftUsage).where(GiftUsage.gift_id == gift_id))
    ).scalar() or 0
    usages = (
        await session.execute(
            select(GiftUsage, User)
            .outerjoin(User, User.id == GiftUsage.user_id)
            .where(GiftUsage.gift_id == gift_id)
            .order_by(GiftUsage.used_at.desc(), GiftUsage.user_id)
            .offset(usage_page * usage_limit)
            .limit(usage_limit)
        )
    ).all()
    can_delete = gift.sender_user_id == user.id and await _gift_delete_allowed(session, identity)
    return {
        **_gift_item(gift, tariff_name=tariff_name, usage_count=usages_total, can_delete=can_delete),
        "usages": [
            {
                **(
                    _brief(recipient)
                    or {
                        "user_id": usage.user_id,
                        "tg_id": public_tg_id(usage.tg_id),
                        "username": None,
                        "first_name": None,
                        "last_name": None,
                    }
                ),
                "used_at": _iso_utc(usage.used_at),
            }
            for usage, recipient in usages
        ],
        "usages_total": int(usages_total),
        "usages_page": usage_page,
        "usages_has_next": (usage_page + 1) * usage_limit < usages_total,
    }


@router.delete("/{user_ref}/gifts/{gift_id}")
async def gift_delete(
    identity: Annotated[Any, Depends(verify_identity_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    user_ref: int = Path(...),
    gift_id: str = Path(..., min_length=1, max_length=128),
):
    """Удаляет подарок отправителя, сохраняя выданные подписки."""
    await require_admin_action(session, identity, PERM_USERS, PERM_GIFTS)
    user = await _owner(session, user_ref)
    if not await delete_gift_for_user(session, gift_id, user.id):
        raise HTTPException(status_code=404, detail="Подарок клиента не найден")
    await record_admin_user_event(
        session,
        identity=identity,
        user_id=user.id,
        event_type="admin_user_gift_deleted",
        path=f"/api/users/{user.id}/gifts/{gift_id}",
        metadata={"gift_id": gift_id},
    )
    return {"ok": True}

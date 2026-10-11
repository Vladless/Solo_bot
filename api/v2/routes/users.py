import asyncio

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import Text, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.admin_permissions import admin_action_capabilities, require_admin_action
from api.depends import get_session, verify_identity_admin
from api.v2.base_crud import generate_crud_router
from api.v2.routes import user_actions, user_relations
from api.v2.schemas import UserBase, UserResponse, UserUpdate
from api.v2.schemas.user_management import UserBalanceRequest, UserProfileRequest
from core.redis_cache import cache_key
from database import async_session_maker, delete_user_data, get_servers
from database.access.identity_cache import invalidate_identity_cache
from database.access.resolution import UserId, get_user_by_id, get_user_by_tg_id, public_tg_id
from database.cache_purge import defer_purge
from database.models import Gift, Identity, Key, ManualBan, Payment, Referral, Tariff, User
from database.payments import add_payment
from database.users import update_balance
from filters.permissions import (
    PERM_GIFTS,
    PERM_KEY_CREATE,
    PERM_KEY_DELETE,
    PERM_KEY_EXPIRY,
    PERM_KEY_FREEZE,
    PERM_KEY_VIEW,
    PERM_USERS,
    PERM_USER_BALANCE,
    PERM_USER_BAN,
)
from logger import logger
from services.operations import delete_key_from_cluster
from services.user_audit import record_admin_user_event


router = APIRouter()
ADMIN_IDENTITY = Depends(verify_identity_admin)
DATABASE_SESSION = Depends(get_session)

CLIENT_ACTION_PERMISSIONS = {
    "create_subscription": (PERM_KEY_CREATE,),
    "send_message": (PERM_USERS,),
    "view_audit": (PERM_USERS,),
    "view_subscription_history": (PERM_KEY_VIEW,),
    "view_referrals": (PERM_USERS,),
    "view_gifts": (PERM_USERS,),
    "manage_gifts": (PERM_USERS, PERM_GIFTS),
    "cabinet": (PERM_USERS,),
    "restore_trial": (PERM_KEY_CREATE,),
    "unlink_email": (PERM_USERS,),
    "unlink_telegram": (PERM_USERS,),
    "autorenew": (PERM_KEY_VIEW,),
    "manage_autorenew": (PERM_USER_BALANCE,),
    "edit_profile": (PERM_USERS,),
    "balance": (PERM_USER_BALANCE,),
    "ban": (PERM_USER_BAN,),
    "delete_user": (PERM_KEY_DELETE,),
    "renew_subscription": (PERM_KEY_EXPIRY,),
    "freeze_subscription": (PERM_KEY_FREEZE,),
    "delete_subscription": (PERM_KEY_DELETE,),
}


def _user_brief(u: User) -> dict:
    return {
        "id": int(u.id),
        "tg_id": public_tg_id(u.tg_id),
        "username": u.username,
        "first_name": u.first_name,
        "last_name": u.last_name,
        "balance": float(u.balance or 0.0),
        "trial": int(u.trial or 0),
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


@router.get("/search")
async def search_users(
    q: str = Query("", description="users.id, tg_id, username, email, имя"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    identity=ADMIN_IDENTITY,
    session: AsyncSession = DATABASE_SESSION,
):
    """Поиск клиентов по users.id, tg_id, username, имени или email ключа."""
    await require_admin_action(session, identity, PERM_USERS)
    term = q.strip()
    stmt = select(User)
    if term:
        like = f"%{term.lstrip('@')}%"
        num = f"%{term.lstrip('@#-')}%"
        email_uids = select(Key.user_id).where(Key.email.ilike(like))
        conds = [
            User.username.ilike(like),
            User.first_name.ilike(like),
            User.last_name.ilike(like),
            cast(User.tg_id, Text).like(num),
            cast(User.id, Text).like(num),
            User.id.in_(email_uids),
            User.identity_id.in_(select(Identity.id).where(Identity.email.ilike(like))),
        ]
        stmt = stmt.where(or_(*conds))
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    rows = (await session.execute(stmt.order_by(User.created_at.desc()).limit(limit).offset(offset))).scalars().all()
    return {"total": int(total), "items": [_user_brief(u) for u in rows]}


@router.post("/{user_ref}/ban")
async def ban_user(
    user_ref: int = Path(..., description="users.id клиента"),
    identity=ADMIN_IDENTITY,
    session: AsyncSession = DATABASE_SESSION,
):
    """Ставит ручной бан пользователю (блокирует доступ к боту)."""
    await require_admin_action(session, identity, PERM_USER_BAN)
    u = await get_user_by_id(session, UserId(user_ref))
    if u is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    existing = (await session.execute(select(ManualBan).where(ManualBan.user_id == u.id))).scalar_one_or_none()
    if existing is None:
        session.add(ManualBan(user_id=u.id, tg_id=u.tg_id, reason="manual", banned_by=None))
    if public_tg_id(u.tg_id) is not None:
        defer_purge(session, cache_key("ban_status", u.tg_id))
    await record_admin_user_event(
        session,
        identity=identity,
        user_id=u.id,
        event_type="admin_user_banned",
        path=f"/users/{u.id}/ban",
    )
    return {"banned": True}


@router.post("/{user_ref}/unban")
async def unban_user(
    user_ref: int = Path(..., description="users.id клиента"),
    identity=ADMIN_IDENTITY,
    session: AsyncSession = DATABASE_SESSION,
):
    """Снимает ручной бан пользователя."""
    await require_admin_action(session, identity, PERM_USER_BAN)
    u = await get_user_by_id(session, UserId(user_ref))
    if u is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    await session.execute(ManualBan.__table__.delete().where(ManualBan.user_id == u.id))
    if public_tg_id(u.tg_id) is not None:
        defer_purge(session, cache_key("ban_status", u.tg_id))
    await record_admin_user_event(
        session,
        identity=identity,
        user_id=u.id,
        event_type="admin_user_unbanned",
        path=f"/users/{u.id}/unban",
    )
    return {"banned": False}


@router.get("/{user_ref}/card")
async def user_card(
    user_ref: int = Path(..., description="users.id клиента или Telegram ID при by_tg=true"),
    by_tg: bool = Query(False, description="Ссылка содержит именно Telegram ID"),
    identity=ADMIN_IDENTITY,
    session: AsyncSession = DATABASE_SESSION,
):
    """Агрегированная карточка клиента: профиль, ключи, платежи, подарки, бан."""
    await require_admin_action(session, identity, PERM_KEY_VIEW)
    u = await get_user_by_tg_id(session, user_ref) if by_tg else await get_user_by_id(session, UserId(user_ref))
    if u is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    linked = await session.get(Identity, u.identity_id) if u.identity_id else None
    capabilities = await admin_action_capabilities(session, identity, CLIENT_ACTION_PERMISSIONS)

    keys_rows = (
        await session.execute(
            select(Key, Tariff.name)
            .outerjoin(Tariff, Tariff.id == Key.tariff_id)
            .where(Key.user_id == u.id)
            .order_by(Key.created_at.desc())
        )
    ).all()
    keys = [
        {
            "client_id": str(k.client_id),
            "email": k.email,
            "alias": k.alias,
            "server_id": k.server_id,
            "tariff_id": k.tariff_id,
            "tariff_name": tname,
            "expiry_time": int(k.expiry_time or 0),
            "is_frozen": bool(k.is_frozen),
        }
        for k, tname in keys_rows
    ]

    payments = (
        (
            await session.execute(
                select(Payment)
                .where(
                    or_(
                        Payment.user_id == u.id,
                        and_(Payment.user_id.is_(None), Payment.tg_id.is_not(None), Payment.tg_id == u.tg_id),
                    )
                )
                .order_by(Payment.created_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    payment_list = [
        {
            "id": int(p.id),
            "amount": float(p.amount or 0.0),
            "currency": p.currency,
            "payment_system": p.payment_system,
            "status": p.status,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in payments
    ]

    gifts_count = (
        await session.execute(select(func.count()).select_from(Gift).where(Gift.sender_user_id == u.id))
    ).scalar() or 0

    ban = (await session.execute(select(ManualBan).where(ManualBan.user_id == u.id).limit(1))).scalar_one_or_none()

    invited_count = (
        await session.execute(select(func.count()).select_from(Referral).where(Referral.referrer_user_id == u.id))
    ).scalar() or 0
    invited_by_row = (
        await session.execute(select(Referral.referrer_user_id).where(Referral.referred_user_id == u.id).limit(1))
    ).scalar_one_or_none()

    return {
        "user": _user_brief(u),
        "identity": {
            "email": linked.email if linked else None,
            "email_verified": bool(linked and linked.email_verified),
            "tg_id": public_tg_id(linked.tg_id) if linked else None,
            "google_linked": bool(linked and linked.google_sub),
            "yandex_linked": bool(linked and linked.yandex_sub),
            "can_unlink_email": bool(linked and linked.email and public_tg_id(linked.tg_id)),
            "can_unlink_telegram": bool(linked and public_tg_id(linked.tg_id) and linked.email),
        },
        "capabilities": capabilities,
        "keys": keys,
        "payments": payment_list,
        "gifts_count": int(gifts_count),
        "invited_count": int(invited_count),
        "invited_by": int(invited_by_row) if invited_by_row else None,
        "banned": ban is not None,
        "ban_reason": getattr(ban, "reason", None) if ban else None,
    }


@router.patch("/{user_ref}/profile")
async def update_user_profile(
    body: UserProfileRequest,
    user_ref: int = Path(..., gt=0, description="users.id клиента"),
    identity=ADMIN_IDENTITY,
    session: AsyncSession = DATABASE_SESSION,
):
    """Обновляет имена клиента без изменения каналов входа."""
    await require_admin_action(session, identity, PERM_USERS)
    user = await get_user_by_id(session, UserId(user_ref))
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    values = body.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(status_code=400, detail="Укажите данные для изменения")
    for field, value in values.items():
        setattr(user, field, value)
    user.updated_at = datetime.now(UTC).replace(tzinfo=None)
    await invalidate_identity_cache(session, user_ids=(user.id,))
    await session.flush()
    await record_admin_user_event(
        session,
        identity=identity,
        user_id=user.id,
        event_type="admin_user_profile_updated",
        path=f"/users/{user.id}/profile",
        metadata={"fields": sorted(values)},
    )
    return {"ok": True, "user": _user_brief(user)}


@router.post("/{user_ref}/balance")
async def adjust_user_balance(
    body: UserBalanceRequest,
    user_ref: int = Path(..., gt=0, description="users.id клиента"),
    identity=ADMIN_IDENTITY,
    session: AsyncSession = DATABASE_SESSION,
):
    """Атомарно изменяет баланс и сохраняет запись операции."""
    await require_admin_action(session, identity, PERM_USER_BALANCE)
    user = await get_user_by_id(session, UserId(user_ref))
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    amount = float(body.amount)
    balance = await update_balance(session, UserId(user.id), amount)
    if balance is None:
        raise HTTPException(status_code=409, detail="Недостаточно средств для списания")
    await add_payment(
        session=session,
        legacy_user_ref=UserId(user.id),
        amount=amount,
        payment_system="admin",
        status="success",
    )
    await record_admin_user_event(
        session,
        identity=identity,
        user_id=user.id,
        event_type="admin_user_balance_adjusted",
        path=f"/users/{user.id}/balance",
        metadata={
            "amount": amount,
            "currency": "RUB",
            "balance_before": balance - amount,
            "balance_after": balance,
        },
    )
    return {"ok": True, "balance": balance}


@router.delete("/{user_ref}", response_model=dict)
async def delete_user(
    user_ref: int = Path(..., description="users.id клиента"),
    identity=ADMIN_IDENTITY,
    session: AsyncSession = DATABASE_SESSION,
):
    """Удаляет пользователя и его ключи на серверах."""
    await require_admin_action(session, identity, PERM_KEY_DELETE)
    try:
        u = await get_user_by_id(session, UserId(user_ref))
        if u is None:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        result = await session.execute(select(Key.email, Key.client_id).where(Key.user_id == u.id))
        key_records = result.all()

        async with async_session_maker() as s:
            servers = await get_servers(session=s)
        cluster_ids = list(servers.keys())

        async def _delete_one(cluster_id: str, email: str, client_id: str):
            async with async_session_maker() as s:
                return await delete_key_from_cluster(cluster_id, email, client_id, s)

        tasks = [
            _delete_one(cluster_id, email, client_id) for email, client_id in key_records for cluster_id in cluster_ids
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        if (key_records and not cluster_ids) or any(
            isinstance(result, BaseException) or result is False for result in results
        ):
            logger.error(f"[Site:Clients] Не подтверждено удаление подписок клиента {user_ref}: {results}")
            raise HTTPException(
                status_code=502,
                detail="Не удалось удалить все подписки с серверов. Клиент сохранён; повторите удаление.",
            )

        await delete_user_data(session, UserId(u.id))
        return {"detail": f"Клиент {user_ref} и его ключи успешно удалены."}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Site:Clients] Не удалось удалить клиента {user_ref}: {e}")
        raise HTTPException(status_code=500, detail="Ошибка при удалении пользователя") from None


router.include_router(user_actions.router)
router.include_router(user_relations.router)


crud_router = generate_crud_router(
    model=User,
    schema_response=UserResponse,
    schema_create=UserBase,
    schema_update=UserUpdate,
    identifier_field="id",
    parameter_name="user_ref",
    enabled_methods=["get_all", "get_one", "create", "update"],
)

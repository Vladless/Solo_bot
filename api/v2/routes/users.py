import asyncio

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import Text, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_session, verify_identity_admin
from api.v2.base_crud import generate_crud_router
from api.v2.schemas import UserBase, UserResponse, UserUpdate
from database import async_session_maker, delete_user_data, get_servers
from database.access.resolution import public_tg_id, resolve_user_optional
from database.models import Gift, Key, ManualBan, Payment, Referral, Tariff, User
from logger import logger
from services.operations import delete_key_from_cluster


router = APIRouter()


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
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Поиск клиентов по users.id, tg_id, username, имени или email ключа."""
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
        ]
        stmt = stmt.where(or_(*conds))
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    rows = (await session.execute(stmt.order_by(User.created_at.desc()).limit(limit).offset(offset))).scalars().all()
    return {"total": int(total), "items": [_user_brief(u) for u in rows]}


@router.post("/{user_ref}/ban")
async def ban_user(
    user_ref: int = Path(..., description="users.id клиента (или legacy tg_id)"),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Ставит ручной бан пользователю (блокирует доступ к боту)."""
    u = await resolve_user_optional(session, user_ref)
    if u is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    existing = (await session.execute(select(ManualBan).where(ManualBan.user_id == u.id))).scalar_one_or_none()
    if existing is None:
        session.add(ManualBan(user_id=u.id, tg_id=u.tg_id, reason="manual", banned_by=None))
    return {"banned": True}


@router.post("/{user_ref}/unban")
async def unban_user(
    user_ref: int = Path(..., description="users.id клиента (или legacy tg_id)"),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Снимает ручной бан пользователя."""
    u = await resolve_user_optional(session, user_ref)
    if u is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    await session.execute(ManualBan.__table__.delete().where(ManualBan.user_id == u.id))
    return {"banned": False}


@router.get("/{user_ref}/card")
async def user_card(
    user_ref: int = Path(..., description="users.id клиента (или legacy tg_id)"),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Агрегированная карточка клиента: профиль, ключи, платежи, подарки, бан."""
    u = await resolve_user_optional(session, user_ref)
    if u is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

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
                .where(or_(Payment.user_id == u.id, and_(Payment.tg_id.is_not(None), Payment.tg_id == u.tg_id)))
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
        "keys": keys,
        "payments": payment_list,
        "gifts_count": int(gifts_count),
        "invited_count": int(invited_count),
        "invited_by": int(invited_by_row) if invited_by_row else None,
        "banned": ban is not None,
        "ban_reason": getattr(ban, "reason", None) if ban else None,
    }


@router.delete("/{user_ref}", response_model=dict)
async def delete_user(
    user_ref: int = Path(..., description="users.id клиента (или legacy tg_id)"),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Удаляет пользователя и его ключи на серверах."""
    try:
        u = await resolve_user_optional(session, user_ref)
        if u is None:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        result = await session.execute(select(Key.email, Key.client_id).where(Key.user_id == u.id))
        key_records = result.all()

        async with async_session_maker() as s:
            servers = await get_servers(session=s)
        cluster_ids = list(servers.keys())

        async def _delete_one(cluster_id: str, email: str, client_id: str):
            async with async_session_maker() as s:
                await delete_key_from_cluster(cluster_id, email, client_id, s)

        try:
            tasks = [
                _delete_one(cluster_id, email, client_id)
                for email, client_id in key_records
                for cluster_id in cluster_ids
            ]
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            logger.error(f"[DELETE] Ошибка при удалении ключей с серверов для клиента {user_ref}: {e}")

        await delete_user_data(session, user_ref)
        return {"detail": f"Клиент {user_ref} и его ключи успешно удалены."}
    except Exception as e:
        logger.error(f"[DELETE] Ошибка при удалении клиента {user_ref}: {e}")
        raise HTTPException(status_code=500, detail="Ошибка при удалении пользователя") from None


crud_router = generate_crud_router(
    model=User,
    schema_response=UserResponse,
    schema_create=UserBase,
    schema_update=UserUpdate,
    identifier_field="tg_id",
    legacy_user_ref=True,
    enabled_methods=["get_all", "get_one", "create", "update"],
)

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_session, verify_identity_token
from database import web_notifications as wn_db
from database.models import Identity


router = APIRouter()


class PushSubscribeRequest(BaseModel):
    endpoint: str
    keys: dict


class NotificationItem(BaseModel):
    id: str
    type: str
    title: str
    message: str
    read: bool
    created_at: str
    data: dict | None = None


class NotificationsResponse(BaseModel):
    ok: bool = True
    notifications: list[NotificationItem]
    unread_count: int


@router.post("/push/subscribe", tags=["Notifications"])
async def push_subscribe(
    body: PushSubscribeRequest,
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(verify_identity_token),
):
    user_id = identity.tg_id or 0

    await wn_db.upsert_push_subscription(
        session,
        user_id=user_id,
        identity_id=identity.id,
        endpoint=body.endpoint,
        keys_json=body.keys,
    )
    return {"ok": True}


@router.get("/notifications", response_model=NotificationsResponse, tags=["Notifications"])
async def get_notifications(
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(verify_identity_token),
):
    notifications = await wn_db.get_notifications_for_identity(
        session,
        identity.id,
        limit=limit,
    )
    unread_count = await wn_db.count_unread_for_identity(session, identity.id)

    items = [
        NotificationItem(
            id=n.id,
            type=n.type,
            title=n.title,
            message=n.message,
            read=n.read,
            created_at=n.created_at.isoformat() if n.created_at else "",
            data=n.data,
        )
        for n in notifications
    ]
    return NotificationsResponse(notifications=items, unread_count=unread_count)


@router.post("/notifications/read-all", tags=["Notifications"])
async def read_all_notifications(
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(verify_identity_token),
):
    count = await wn_db.mark_all_read_for_identity(session, identity.id)
    return {"ok": True, "updated": count}


@router.delete("/notifications", tags=["Notifications"])
async def delete_all_notifications(
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(verify_identity_token),
):
    count = await wn_db.delete_all_for_identity(session, identity.id)
    return {"ok": True, "deleted": count}


@router.post("/notifications/{notification_id}/read", tags=["Notifications"])
async def read_one_notification(
    notification_id: str = Path(..., min_length=1, max_length=64),
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(verify_identity_token),
):
    """Пометить одно уведомление прочитанным. 404 если не найдено или не принадлежит юзеру."""
    ok = await wn_db.mark_one_read_for_identity(session, identity.id, notification_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Уведомление не найдено")
    return {"ok": True}


@router.delete("/notifications/{notification_id}", tags=["Notifications"])
async def delete_one_notification(
    notification_id: str = Path(..., min_length=1, max_length=64),
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(verify_identity_token),
):
    """Удалить одно уведомление. 404 если не найдено или не принадлежит юзеру."""
    ok = await wn_db.delete_one_for_identity(session, identity.id, notification_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Уведомление не найдено")
    return {"ok": True}

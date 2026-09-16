import re

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import distinct, exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_session, verify_admin_token, verify_admin_token_short
from api.shared.broadcast_admin import (
    BroadcastLaunchPayload,
    DomainChange,
    MaintenanceUpdate,
    ScheduledBroadcastCreatePayload,
    ScheduledBroadcastUpdatePayload,
    get_broadcast_bot,
    require_future_schedule,
    resolve_update_payload,
    restart_bot_process,
)
from core.bootstrap import MANAGEMENT_CONFIG
from core.executor import spawn
from core.settings.management_config import update_management_config
from database.models import Key, Server, User
from database.scheduled_broadcasts import (
    cancel_scheduled_broadcast,
    create_scheduled_broadcast,
    get_scheduled_broadcast,
    list_scheduled_broadcasts,
    mark_scheduled_broadcast_failed,
    mark_scheduled_broadcast_sent,
    start_scheduled_broadcast,
    update_scheduled_broadcast,
)
from handlers.admin.sender.scheduled_service import (
    execute_broadcast_payload,
    execute_scheduled_broadcast,
    prepare_broadcast_payload,
    scheduled_broadcast_to_dict,
)
from logger import logger
from utils.backup import backup_database


router = APIRouter()


@router.get("/status")
async def get_status(admin=Depends(verify_admin_token)):
    return {
        "maintenance_enabled": bool(MANAGEMENT_CONFIG.get("MAINTENANCE_ENABLED", False)),
        "management": dict(MANAGEMENT_CONFIG or {}),
    }


@router.post("/maintenance")
async def set_maintenance(
    payload: MaintenanceUpdate,
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    current_config = dict(MANAGEMENT_CONFIG or {})
    current_config["MAINTENANCE_ENABLED"] = bool(payload.enabled)
    await update_management_config(session, current_config)
    return {"maintenance_enabled": bool(MANAGEMENT_CONFIG.get("MAINTENANCE_ENABLED", False))}


@router.post("/restart")
async def restart_bot(
    background: BackgroundTasks,
    admin=Depends(verify_admin_token),
):
    background.add_task(restart_bot_process)
    return {"status": "restarting"}


@router.post("/change-domain")
async def change_domain(
    payload: DomainChange,
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    domain = payload.domain.strip()
    if not domain or " " in domain or not re.fullmatch(r"[a-zA-Z0-9.-]+", domain):
        raise HTTPException(status_code=400, detail="Invalid domain")

    new_domain_url = f"https://{domain}"

    stmt = (
        update(Key)
        .values(
            key=func.regexp_replace(Key.key, r"^https://[^/]+", new_domain_url),
            remnawave_link=func.regexp_replace(Key.remnawave_link, r"^https://[^/]+", new_domain_url),
        )
        .where(
            (Key.key.startswith("https://") & ~Key.key.startswith(new_domain_url))
            | (Key.remnawave_link.startswith("https://") & ~Key.remnawave_link.startswith(new_domain_url))
        )
    )
    result = await session.execute(stmt)

    return {"updated": result.rowcount or 0}


@router.post("/restore-trials")
async def restore_trials(
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        update(User)
        .where(
            User.trial == 1,
            ~exists(select(Key.user_id).where(Key.user_id == User.id)),
        )
        .values(trial=0)
    )
    result = await session.execute(stmt)

    return {"restored": result.rowcount or 0}


@router.post("/backup")
async def trigger_backup(admin=Depends(verify_admin_token)):
    async def _run_backup() -> None:
        try:
            exception = await backup_database()
        except Exception as error:
            logger.opt(exception=error).error("[Management] Backup crashed: {}", error)
            return
        if exception:
            logger.error(f"[Management] Backup finished with error: {exception}")

    spawn(_run_backup())
    return {"status": "backup_started"}


@router.get("/broadcast/clusters")
async def get_broadcast_clusters(
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(distinct(Server.cluster_name)).where(Server.cluster_name.is_not(None)))
    clusters = sorted([row[0] for row in result.all() if row and row[0]])
    return {"clusters": clusters}


@router.post("/broadcast")
async def launch_broadcast(
    payload: BroadcastLaunchPayload,
    admin=Depends(verify_admin_token_short),
):
    """Запуск рассылки. Сессия БД не держится на время рассылки."""
    try:
        prepared = prepare_broadcast_payload(
            send_to=payload.send_to,
            text=payload.text,
            photo=payload.photo,
            cluster_name=payload.cluster_name,
            workers=payload.workers,
            messages_per_second=payload.messages_per_second,
            channel=payload.channel,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await execute_broadcast_payload(prepared, bot=get_broadcast_bot())


@router.post("/broadcast/scheduled")
async def create_broadcast_schedule(
    payload: ScheduledBroadcastCreatePayload,
    admin=Depends(verify_admin_token_short),
    session: AsyncSession = Depends(get_session),
):
    try:
        prepared = prepare_broadcast_payload(
            send_to=payload.send_to,
            text=payload.text,
            photo=payload.photo,
            cluster_name=payload.cluster_name,
            workers=payload.workers,
            messages_per_second=payload.messages_per_second,
            channel=payload.channel,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    broadcast = await create_scheduled_broadcast(
        session,
        created_by_tg_id=getattr(admin, "tg_id", None),
        send_to=prepared["send_to"],
        channel=prepared["channel"],
        cluster_name=prepared["cluster_name"],
        text=prepared["text"],
        photo=prepared["photo"],
        keyboard_json=prepared["keyboard_json"],
        scheduled_for=require_future_schedule(payload.scheduled_for),
        workers=prepared["workers"],
        messages_per_second=prepared["messages_per_second"],
    )
    return {"success": True, "item": scheduled_broadcast_to_dict(broadcast)}


@router.get("/broadcast/scheduled")
async def list_broadcast_schedules(
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
    status: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    statuses = [item.strip() for item in (status or "").split(",") if item.strip()] or None
    items = await list_scheduled_broadcasts(session, statuses=statuses, limit=limit, offset=offset)
    return {"items": [scheduled_broadcast_to_dict(item) for item in items], "limit": limit, "offset": offset}


@router.get("/broadcast/scheduled/{broadcast_id}")
async def get_broadcast_schedule(
    broadcast_id: str,
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    item = await get_scheduled_broadcast(session, broadcast_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Scheduled broadcast not found")
    return {"item": scheduled_broadcast_to_dict(item)}


@router.patch("/broadcast/scheduled/{broadcast_id}")
async def update_broadcast_schedule(
    broadcast_id: str,
    payload: ScheduledBroadcastUpdatePayload,
    admin=Depends(verify_admin_token_short),
    session: AsyncSession = Depends(get_session),
):
    current = await get_scheduled_broadcast(session, broadcast_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Scheduled broadcast not found")
    try:
        values = resolve_update_payload(payload, current)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    updated = await update_scheduled_broadcast(session, broadcast_id, **values)
    if updated is None:
        raise HTTPException(status_code=409, detail="Scheduled broadcast can no longer be edited")
    return {"success": True, "item": scheduled_broadcast_to_dict(updated)}


@router.post("/broadcast/scheduled/{broadcast_id}/cancel")
async def cancel_broadcast_schedule(
    broadcast_id: str,
    admin=Depends(verify_admin_token_short),
    session: AsyncSession = Depends(get_session),
):
    item = await cancel_scheduled_broadcast(session, broadcast_id)
    if item is None:
        raise HTTPException(status_code=409, detail="Scheduled broadcast can no longer be cancelled")
    return {"success": True, "item": scheduled_broadcast_to_dict(item)}


@router.post("/broadcast/scheduled/{broadcast_id}/send-now")
async def send_broadcast_schedule_now(
    broadcast_id: str,
    admin=Depends(verify_admin_token_short),
    session: AsyncSession = Depends(get_session),
):
    item = await start_scheduled_broadcast(session, broadcast_id)
    if item is None:
        raise HTTPException(status_code=409, detail="Scheduled broadcast can no longer be sent now")
    try:
        result = await execute_scheduled_broadcast(item, bot=get_broadcast_bot())
    except Exception as exc:
        await mark_scheduled_broadcast_failed(session, broadcast_id, str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if result.get("success"):
        item = await mark_scheduled_broadcast_sent(session, broadcast_id, result)
    else:
        item = await mark_scheduled_broadcast_failed(session, broadcast_id, result.get("message", "Broadcast failed"))
    return {"success": bool(result.get("success")), "item": scheduled_broadcast_to_dict(item), "result": result}

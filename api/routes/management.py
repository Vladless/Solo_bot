import os
import re
import subprocess
import sys
import asyncio
from typing import Literal

import psutil
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import distinct, exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_session, verify_admin_token
from config import API_TOKEN
from core.bootstrap import MANAGEMENT_CONFIG
from core.settings.management_config import update_management_config
from database.models import Key, User
from database.models import Server
from handlers.admin.sender.sender_service import BroadcastService
from handlers.admin.sender.sender_utils import get_recipients, parse_message_buttons
from logger import logger
from utils.backup import backup_database


router = APIRouter()


class MaintenanceUpdate(BaseModel):
    enabled: bool


class DomainChange(BaseModel):
    domain: str


class BroadcastLaunchPayload(BaseModel):
    send_to: Literal["all", "subscribed", "unsubscribed", "untrial", "trial", "hotleads", "cluster"] = "all"
    text: str
    photo: str | None = None
    cluster_name: str | None = None
    workers: int = 5
    messages_per_second: int = 35


_broadcast_bot: Bot | None = None


def _get_broadcast_bot() -> Bot:
    global _broadcast_bot
    if _broadcast_bot is None:
        _broadcast_bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    return _broadcast_bot


async def _restart_bot() -> None:
    await asyncio.sleep(1)
    try:
        parent = psutil.Process(os.getpid()).parent()
        is_systemd = parent and "systemd" in parent.name().lower()

        if is_systemd:
            subprocess.run(
                ["sudo", "systemctl", "restart", "bot.service"],
                check=True,
            )
        else:
            python_exe = sys.executable
            script_path = os.path.abspath(sys.argv[0])
            os.execv(python_exe, [python_exe, script_path] + sys.argv[1:])
    except Exception:
        os._exit(1)


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
    background.add_task(_restart_bot)
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
    await session.commit()

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
            ~exists(select(Key.tg_id).where(Key.tg_id == User.tg_id)),
        )
        .values(trial=0)
    )
    result = await session.execute(stmt)
    await session.commit()

    return {"restored": result.rowcount or 0}


@router.post("/backup")
async def trigger_backup(admin=Depends(verify_admin_token)):
    async def _run_backup() -> None:
        exception = await backup_database()
        if exception:
            logger.error(f"[Management] Backup finished with error: {exception}")

    asyncio.create_task(_run_backup())
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
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    text_raw = (payload.text or "").strip()
    if not text_raw:
        raise HTTPException(status_code=400, detail="Broadcast text is required")

    if payload.send_to == "cluster" and not (payload.cluster_name or "").strip():
        raise HTTPException(status_code=400, detail="Cluster name is required for cluster broadcast")

    clean_text, keyboard = parse_message_buttons(text_raw)

    max_len = 1024 if payload.photo else 4096
    if len(clean_text) > max_len:
        raise HTTPException(status_code=400, detail=f"Message too long. Max {max_len} symbols")

    tg_ids, total_users = await get_recipients(session, payload.send_to, (payload.cluster_name or None))
    if not tg_ids:
        return {"success": False, "message": "No recipients found", "stats": {"total_messages": 0}}

    bot = _get_broadcast_bot()
    messages = [
        {
            "tg_id": tg_id,
            "text": clean_text,
            "photo": payload.photo,
            "keyboard": keyboard,
        }
        for tg_id in tg_ids
    ]

    workers = max(1, min(int(payload.workers or 5), 30))
    rate = max(1, min(int(payload.messages_per_second or 35), 60))
    broadcast_service = BroadcastService(bot=bot, session=session, messages_per_second=rate)
    stats = await broadcast_service.broadcast(messages, workers=workers)
    return {
        "success": True,
        "message": "Broadcast completed",
        "recipients": total_users,
        "stats": stats,
    }

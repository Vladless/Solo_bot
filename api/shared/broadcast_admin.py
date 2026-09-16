import asyncio
import os
import subprocess
import sys

from datetime import datetime, timezone
from typing import Literal

import psutil

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from fastapi import HTTPException
from pydantic import BaseModel

from core.executor import run_io
from core.settings.modes_config import resolve_protect_content
from database.models import ScheduledBroadcast
from handlers.admin.sender.scheduled_service import ensure_utc_datetime, prepare_broadcast_payload
from settings.config import API_TOKEN, BOT_SERVICE


class MaintenanceUpdate(BaseModel):
    enabled: bool


class DomainChange(BaseModel):
    domain: str


class BroadcastLaunchPayload(BaseModel):
    send_to: Literal["all", "subscribed", "unsubscribed", "untrial", "trial", "hotleads", "cluster"] = "all"
    channel: Literal["bot", "site", "both"] = "both"
    text: str
    photo: str | None = None
    cluster_name: str | None = None
    workers: int = 5
    messages_per_second: int = 35


class ScheduledBroadcastCreatePayload(BroadcastLaunchPayload):
    scheduled_for: datetime


class ScheduledBroadcastUpdatePayload(BaseModel):
    send_to: Literal["all", "subscribed", "unsubscribed", "untrial", "trial", "hotleads", "cluster"] | None = None
    channel: Literal["bot", "site", "both"] | None = None
    text: str | None = None
    photo: str | None = None
    cluster_name: str | None = None
    workers: int | None = None
    messages_per_second: int | None = None
    scheduled_for: datetime | None = None


_broadcast_bot: Bot | None = None


def get_broadcast_bot() -> Bot:
    """Возвращает экземпляр бота для рассылки."""
    global _broadcast_bot
    if _broadcast_bot is None:
        _broadcast_bot = Bot(
            token=API_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML, protect_content=resolve_protect_content()),
        )
    elif _broadcast_bot.default is not None:
        _broadcast_bot.default.protect_content = resolve_protect_content()
    return _broadcast_bot


def require_future_schedule(value: datetime) -> datetime:
    """Проверяет, что время отложенной рассылки ещё не наступило."""
    scheduled_for = ensure_utc_datetime(value)
    if scheduled_for <= datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="scheduled_for must be in the future")
    return scheduled_for


def resolve_update_payload(payload: ScheduledBroadcastUpdatePayload, current: ScheduledBroadcast) -> dict:
    """Поля отложенной рассылки после правки: незаданное берётся из текущей записи."""
    fields = payload.model_fields_set
    text_changed = "text" in fields
    prepared = prepare_broadcast_payload(
        send_to=payload.send_to if "send_to" in fields else current.send_to,
        text=payload.text if "text" in fields else current.text,
        photo=payload.photo if "photo" in fields else current.photo,
        cluster_name=payload.cluster_name if "cluster_name" in fields else current.cluster_name,
        workers=payload.workers if "workers" in fields else current.workers,
        messages_per_second=(
            payload.messages_per_second if "messages_per_second" in fields else current.messages_per_second
        ),
        channel=payload.channel if "channel" in fields else current.channel,
    )
    if not text_changed:
        prepared["text"] = current.text
        prepared["keyboard_json"] = current.keyboard_json
    if "scheduled_for" in fields:
        prepared["scheduled_for"] = require_future_schedule(payload.scheduled_for)
    return prepared


async def restart_bot_process() -> None:
    """Перезапуск процесса бота (systemctl или execv)."""
    await asyncio.sleep(1)
    try:
        parent = psutil.Process(os.getpid()).parent()
        if parent and "systemd" in parent.name().lower():
            await run_io(lambda: subprocess.run(["sudo", "systemctl", "restart", BOT_SERVICE], check=True))
        else:
            python_exe = sys.executable
            script_path = os.path.abspath(sys.argv[0])
            os.execv(python_exe, [python_exe, script_path] + sys.argv[1:])
    except Exception:
        os._exit(1)

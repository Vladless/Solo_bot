import asyncio

from datetime import datetime, timezone

import pytz

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup

from core.settings.modes_config import resolve_protect_content
from database import async_session_maker, save_blocked_user_ids
from database.models import ScheduledBroadcast
from database.scheduled_broadcasts import (
    SCHEDULED_BROADCAST_STATUS_CANCELLED,
    SCHEDULED_BROADCAST_STATUS_FAILED,
    SCHEDULED_BROADCAST_STATUS_RUNNING,
    SCHEDULED_BROADCAST_STATUS_SCHEDULED,
    SCHEDULED_BROADCAST_STATUS_SENT,
    claim_due_scheduled_broadcasts,
    mark_scheduled_broadcast_failed,
    mark_scheduled_broadcast_sent,
)
from handlers.admin.sender.sender_service import BroadcastService
from handlers.admin.sender.sender_utils import get_recipients, parse_message_buttons
from logger import logger
from settings.config import API_TOKEN


MOSCOW_TZ = pytz.timezone("Europe/Moscow")


def clamp_broadcast_workers(value: int | None) -> int:
    return max(1, min(int(value or 5), 30))


def clamp_broadcast_rate(value: int | None) -> int:
    return max(1, min(int(value or 25), 60))


def ensure_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return MOSCOW_TZ.localize(value).astimezone(timezone.utc)
    return value.astimezone(timezone.utc)


def parse_moscow_datetime_input(raw_value: str) -> datetime:
    value = (raw_value or "").strip()
    formats = ("%d.%m.%Y %H:%M", "%d.%m.%y %H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M")
    for fmt in formats:
        try:
            parsed = datetime.strptime(value, fmt)
            return ensure_utc_datetime(parsed)
        except ValueError:
            continue
    raise ValueError("Неверный формат даты. Используйте ДД.ММ.ГГГГ ЧЧ:ММ")


def format_moscow_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(MOSCOW_TZ).strftime("%d.%m.%Y %H:%M")


def prepare_broadcast_payload(
    *,
    send_to: str,
    text: str,
    photo: str | None = None,
    cluster_name: str | None = None,
    workers: int | None = None,
    messages_per_second: int | None = None,
    channel: str = "both",
) -> dict:
    text_raw = (text or "").strip()
    if not text_raw:
        raise ValueError("Broadcast text is required")
    from .sender_utils import channels_to_str, parse_channels

    channel = channels_to_str(parse_channels(channel))
    normalized_cluster_name = (cluster_name or "").strip() or None
    if send_to == "cluster" and not normalized_cluster_name:
        raise ValueError("Cluster name is required for cluster broadcast")
    if send_to == "source" and not normalized_cluster_name:
        raise ValueError("UTM source is required for source broadcast")
    clean_text, keyboard = parse_message_buttons(text_raw)
    max_len = 1024 if photo else 4096
    if len(clean_text) > max_len:
        raise ValueError(f"Message too long. Max {max_len} symbols")
    keyboard_json = keyboard.model_dump() if keyboard else None
    return {
        "send_to": send_to,
        "channel": channel,
        "text": clean_text,
        "photo": photo,
        "cluster_name": normalized_cluster_name,
        "workers": clamp_broadcast_workers(workers),
        "messages_per_second": clamp_broadcast_rate(messages_per_second),
        "keyboard_json": keyboard_json,
    }


def scheduled_broadcast_to_dict(broadcast: ScheduledBroadcast) -> dict:
    return {
        "id": broadcast.id,
        "created_by_tg_id": broadcast.created_by_tg_id,
        "status": broadcast.status,
        "send_to": broadcast.send_to,
        "channel": broadcast.channel,
        "cluster_name": broadcast.cluster_name,
        "text": broadcast.text,
        "photo": broadcast.photo,
        "keyboard_json": broadcast.keyboard_json,
        "scheduled_for": broadcast.scheduled_for.isoformat() if broadcast.scheduled_for else None,
        "scheduled_for_moscow": format_moscow_datetime(broadcast.scheduled_for),
        "workers": broadcast.workers,
        "messages_per_second": broadcast.messages_per_second,
        "stats": broadcast.stats_json,
        "error_text": broadcast.error_text,
        "started_at": broadcast.started_at.isoformat() if broadcast.started_at else None,
        "sent_at": broadcast.sent_at.isoformat() if broadcast.sent_at else None,
        "cancelled_at": broadcast.cancelled_at.isoformat() if broadcast.cancelled_at else None,
        "created_at": broadcast.created_at.isoformat() if broadcast.created_at else None,
        "updated_at": broadcast.updated_at.isoformat() if broadcast.updated_at else None,
        "is_editable": broadcast.status in {SCHEDULED_BROADCAST_STATUS_SCHEDULED, SCHEDULED_BROADCAST_STATUS_FAILED},
        "is_terminal": broadcast.status in {SCHEDULED_BROADCAST_STATUS_SENT, SCHEDULED_BROADCAST_STATUS_CANCELLED},
        "is_running": broadcast.status == SCHEDULED_BROADCAST_STATUS_RUNNING,
    }


async def execute_broadcast_payload(payload: dict, bot: Bot | None = None) -> dict:
    own_bot = bot is None
    if own_bot:
        bot = Bot(
            token=API_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML, protect_content=resolve_protect_content()),
        )
    try:
        async with async_session_maker() as session:
            channel = payload.get("channel", "both")
            tg_ids, total_users = await get_recipients(
                session,
                payload["send_to"],
                payload.get("cluster_name"),
                channel=channel,
            )
            await session.commit()
        if not tg_ids:
            return {
                "success": False,
                "message": "No recipients found",
                "recipients": 0,
                "stats": {"total_messages": 0},
            }
        keyboard = (
            InlineKeyboardMarkup.model_validate(payload["keyboard_json"]) if payload.get("keyboard_json") else None
        )
        messages = [
            {
                "tg_id": tg_id,
                "text": payload["text"],
                "photo": payload.get("photo"),
                "keyboard": keyboard,
            }
            for tg_id in tg_ids
        ]
        broadcast_service = BroadcastService(
            bot=bot,
            session=None,
            messages_per_second=clamp_broadcast_rate(payload.get("messages_per_second")),
        )
        stats = await broadcast_service.broadcast(
            messages,
            workers=clamp_broadcast_workers(payload.get("workers")),
            channel=payload.get("channel", "both"),
        )
        blocked_ids = stats.get("blocked_user_ids") or []
        if blocked_ids:
            async with async_session_maker() as session:
                try:
                    await save_blocked_user_ids(session, blocked_ids)
                    await session.commit()
                except Exception as e:
                    logger.warning("[Broadcast] Ошибка сохранения blocked_ids: {}", e)
        return {
            "success": True,
            "message": "Broadcast completed",
            "recipients": total_users,
            "stats": stats,
        }
    finally:
        if own_bot and bot is not None and bot.session is not None:
            await bot.session.close()


async def execute_scheduled_broadcast(broadcast: ScheduledBroadcast, bot: Bot | None = None) -> dict:
    payload = {
        "send_to": broadcast.send_to,
        "channel": broadcast.channel,
        "text": broadcast.text,
        "photo": broadcast.photo,
        "cluster_name": broadcast.cluster_name,
        "workers": broadcast.workers,
        "messages_per_second": broadcast.messages_per_second,
        "keyboard_json": broadcast.keyboard_json,
    }
    return await execute_broadcast_payload(payload, bot=bot)


async def process_due_scheduled_broadcasts_once(bot: Bot, limit: int = 3) -> int:
    async with async_session_maker() as session:
        broadcasts = await claim_due_scheduled_broadcasts(session, limit=limit)
        await session.commit()
    processed = 0
    for broadcast in broadcasts:
        processed += 1
        try:
            result = await execute_scheduled_broadcast(broadcast, bot=bot)
            async with async_session_maker() as session:
                if result.get("success"):
                    await mark_scheduled_broadcast_sent(session, broadcast.id, result)
                else:
                    await mark_scheduled_broadcast_failed(
                        session, broadcast.id, result.get("message", "Broadcast failed")
                    )
                await session.commit()
        except Exception as exc:
            async with async_session_maker() as session:
                await mark_scheduled_broadcast_failed(session, broadcast.id, str(exc))
                await session.commit()
    return processed


async def scheduled_broadcasts_loop(
    bot: Bot,
    *,
    interval_seconds: int = 15,
    limit: int = 3,
) -> None:
    while True:
        try:
            await process_due_scheduled_broadcasts_once(bot, limit=limit)
        except Exception as exc:
            logger.error("[ScheduledBroadcasts] Loop error: {}", exc)
        await asyncio.sleep(interval_seconds)

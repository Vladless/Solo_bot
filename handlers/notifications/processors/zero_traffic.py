from __future__ import annotations

from datetime import datetime, timedelta

import pytz

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from config import NOTIFY_INACTIVE_TRAFFIC, REMNAWAVE_WEBAPP, REMNAWAVE_WEBAPP_OPEN_IN_BROWSER
from core.bootstrap import MODES_CONFIG, NOTIFICATIONS_CONFIG
from database.models import Key
from database.tariffs import get_tariffs
from handlers.buttons import CONNECT_DEVICE, MAIN_MENU, SUPPORT
from handlers.keys.utils import build_key_callback
from handlers.notifications.sender import send_messages_with_limit
from handlers.texts import ZERO_TRAFFIC_MSG
from handlers.utils import build_support_button, is_full_remnawave_cluster
from hooks.hook_buttons import insert_hook_buttons
from hooks.hooks import run_hooks
from logger import logger
from panels.remnawave_runtime import fetch_all_remnawave_traffic


moscow_tz = pytz.timezone("Europe/Moscow")

_ZERO_TRAFFIC_UPDATE_BATCH_SIZE = 5000


async def process_zero_traffic(
    bot: Bot,
    session: AsyncSession,
    current_time: int,
    keys: list,
):
    inactive_traffic_hours = int(NOTIFICATIONS_CONFIG.get("INACTIVE_TRAFFIC_ENABLED", NOTIFY_INACTIVE_TRAFFIC))
    if inactive_traffic_hours <= 0:
        return

    trial_tariffs = await get_tariffs(session, group_code="trial")
    trial_tariff_ids = {t["id"] for t in trial_tariffs} if trial_tariffs else set()
    if not trial_tariff_ids:
        return

    current_dt = datetime.fromtimestamp(current_time / 1000, tz=moscow_tz)
    remnawave_webapp_enabled = bool(MODES_CONFIG.get("REMNAWAVE_WEBAPP_ENABLED", REMNAWAVE_WEBAPP))
    open_in_browser = bool(MODES_CONFIG.get("REMNAWAVE_WEBAPP_OPEN_IN_BROWSER", REMNAWAVE_WEBAPP_OPEN_IN_BROWSER))

    candidate_keys = []
    for key in keys:
        if key.tariff_id not in trial_tariff_ids:
            continue
        if key.created_at is None or key.notified:
            continue
        created_at_dt = pytz.utc.localize(datetime.fromtimestamp(key.created_at / 1000)).astimezone(moscow_tz)
        if current_dt < created_at_dt + timedelta(hours=inactive_traffic_hours):
            continue
        if key.expiry_time:
            expiry_dt = pytz.utc.localize(datetime.fromtimestamp(key.expiry_time / 1000)).astimezone(moscow_tz)
            if current_dt > expiry_dt:
                continue
        candidate_keys.append(key)

    if not candidate_keys:
        return

    needed_uuids = {k.client_id for k in candidate_keys if k.client_id}
    logger.info(f"[ZeroTraffic] Кандидатов: {len(candidate_keys)}, UUID: {len(needed_uuids)}")

    try:
        traffic_map = await fetch_all_remnawave_traffic(session, needed_uuids=needed_uuids)
    except Exception as e:
        logger.error(f"[ZeroTraffic] Ошибка получения трафика: {e}")
        return

    messages = []
    keys_to_mark = [k.client_id for k in candidate_keys]

    for key in candidate_keys:
        tg_id = key.tg_id
        email = key.email
        client_id = key.client_id

        used_bytes = traffic_map.get(client_id)
        if used_bytes is None or used_bytes > 0:
            continue

        builder = InlineKeyboardBuilder()
        server_id = key.server_id
        try:
            is_full_remnawave = await is_full_remnawave_cluster(server_id, session)
            final_link = key.key or key.remnawave_link

            if is_full_remnawave and final_link and remnawave_webapp_enabled:
                if open_in_browser:
                    builder.row(InlineKeyboardButton(text=CONNECT_DEVICE, url=final_link))
                else:
                    builder.row(InlineKeyboardButton(text=CONNECT_DEVICE, web_app=WebAppInfo(url=final_link)))
            else:
                builder.row(
                    InlineKeyboardButton(
                        text=CONNECT_DEVICE,
                        callback_data=build_key_callback("connect_device", client_id, email),
                    )
                )
        except Exception as e:
            logger.error(f"Ошибка типа панели для {email}: {e}")
            builder.row(
                InlineKeyboardButton(
                    text=CONNECT_DEVICE,
                    callback_data=build_key_callback("connect_device", client_id, email),
                )
            )

        support_btn = await build_support_button()
        if support_btn:
            builder.row(support_btn)
        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

        try:
            hook_commands = await run_hooks(
                "zero_traffic_notification",
                chat_id=tg_id,
                admin=False,
                session=session,
                email=email,
            )
            if hook_commands:
                builder = insert_hook_buttons(builder, hook_commands)
        except Exception as e:
            logger.warning(f"[ZeroTraffic] Ошибка хуков: {e}")

        messages.append({
            "tg_id": tg_id,
            "text": ZERO_TRAFFIC_MSG.format(email=email),
            "keyboard": builder.as_markup(),
            "client_id": client_id,
        })

    if keys_to_mark:
        try:
            for i in range(0, len(keys_to_mark), _ZERO_TRAFFIC_UPDATE_BATCH_SIZE):
                batch = keys_to_mark[i : i + _ZERO_TRAFFIC_UPDATE_BATCH_SIZE]
                await session.execute(update(Key).where(Key.client_id.in_(batch)).values(notified=True))
                await session.commit()
            logger.info(f"[ZeroTraffic] Отмечено {len(keys_to_mark)} ключей как notified")
        except Exception as e:
            logger.error(f"[ZeroTraffic] Ошибка обновления notified: {e}")

    if messages:
        results = await send_messages_with_limit(bot, messages)
        sent_count = sum(1 for r in results if r)
        logger.info(f"[ZeroTraffic] Отправлено {sent_count} уведомлений")

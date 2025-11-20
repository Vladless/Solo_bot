import json
import re

from datetime import datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import distinct, exists, func, not_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import BlockedUser, Key, ManualBan, Payment, Server, Tariff, User
from logger import logger
from core.constants import PAYMENT_SYSTEMS_EXCLUDED


async def get_recipients(
    session: AsyncSession,
    send_to: str,
    cluster_name: str | None = None
) -> tuple[list[int], int]:
    now_ms = int(datetime.utcnow().timestamp() * 1000)
    banned_tg_ids = select(BlockedUser.tg_id).union_all(
        select(ManualBan.tg_id).where(
            (ManualBan.until.is_(None)) | (ManualBan.until > datetime.utcnow())
        )
    )

    query = None
    
    if send_to == "subscribed":
        query = (
            select(distinct(User.tg_id))
            .join(Key)
            .where(Key.expiry_time > now_ms)
            .where(~User.tg_id.in_(banned_tg_ids))
        )
    
    elif send_to == "unsubscribed":
        subquery = (
            select(User.tg_id)
            .outerjoin(Key, User.tg_id == Key.tg_id)
            .group_by(User.tg_id)
            .having(func.count(Key.tg_id) == 0)
            .union_all(
                select(User.tg_id)
                .join(Key, User.tg_id == Key.tg_id)
                .group_by(User.tg_id)
                .having(func.max(Key.expiry_time) <= now_ms)
            )
        )
        query = select(distinct(subquery.c.tg_id)).where(~subquery.c.tg_id.in_(banned_tg_ids))
    
    elif send_to == "untrial":
        subquery = select(Key.tg_id)
        query = (
            select(distinct(User.tg_id))
            .where(~User.tg_id.in_(subquery) & User.trial.in_([0, -1]))
            .where(~User.tg_id.in_(banned_tg_ids))
        )
    
    elif send_to == "cluster":
        query = (
            select(distinct(User.tg_id))
            .join(Key, User.tg_id == Key.tg_id)
            .join(Server, Key.server_id == Server.cluster_name)
            .where(Server.cluster_name == cluster_name)
            .where(~User.tg_id.in_(banned_tg_ids))
        )
    
    elif send_to == "hotleads":
        subquery_active_keys = select(Key.tg_id).where(Key.expiry_time > now_ms).distinct()
        query = (
            select(distinct(User.tg_id))
            .join(Payment, User.tg_id == Payment.tg_id)
            .where(Payment.status == "success")
            .where(Payment.amount > 0)
            .where(Payment.payment_system.notin_(PAYMENT_SYSTEMS_EXCLUDED))
            .where(not_(exists(subquery_active_keys.where(Key.tg_id == User.tg_id))))
            .where(~User.tg_id.in_(banned_tg_ids))
        )
    
    elif send_to == "trial":
        trial_tariff_subquery = select(Tariff.id).where(Tariff.group_code == "trial")
        query = (
            select(distinct(Key.tg_id))
            .where(Key.tariff_id.in_(trial_tariff_subquery))
            .where(~Key.tg_id.in_(banned_tg_ids))
        )
    
    else:
        query = select(distinct(User.tg_id)).where(~User.tg_id.in_(banned_tg_ids))

    result = await session.execute(query)
    tg_ids = [row[0] for row in result.all()]
    return tg_ids, len(tg_ids)


def strip_html_tags(text: str) -> str:
    text = re.sub(r'<tg-emoji emoji-id="[^"]*">([^<]*)</tg-emoji>', r"\1", text)
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
    return text.strip()


def parse_message_buttons(text: str) -> tuple[str, InlineKeyboardMarkup | None]:
    buttons_match = re.search(r'(<[^>]+>)?\s*BUTTONS\s*:\s*(</[^>]+>)?', text, re.IGNORECASE)
    if not buttons_match:
        return text, None

    clean_text = text[:buttons_match.start()].strip()

    buttons_section = text[buttons_match.start():].strip()
    buttons_text = strip_html_tags(buttons_section)

    buttons_text = re.sub(r'^.*?BUTTONS\s*:\s*', '', buttons_text, flags=re.IGNORECASE).strip()

    if not buttons_text:
        return clean_text, None

    buttons = []
    button_lines = [line.strip() for line in buttons_text.split("\n") if line.strip()]

    for line in button_lines:
        try:
            button_data = json.loads(line)

            if not isinstance(button_data, dict) or "text" not in button_data:
                logger.warning(f"[Sender] Неверный формат кнопки: {line}")
                continue

            text_btn = button_data["text"]

            if "callback" in button_data:
                callback_data = button_data["callback"]
                if len(callback_data) > 64:
                    logger.warning(f"[Sender] Callback слишком длинный: {callback_data}")
                    continue
                button = InlineKeyboardButton(text=text_btn, callback_data=callback_data)
            elif "url" in button_data:
                url = button_data["url"]
                button = InlineKeyboardButton(text=text_btn, url=url)
            else:
                logger.warning(f"[Sender] Кнопка без действия: {line}")
                continue

            buttons.append([button])

        except json.JSONDecodeError as e:
            logger.warning(f"[Sender] Ошибка парсинга JSON кнопки: {line} - {e}")
            continue
        except Exception as e:
            logger.error(f"[Sender] Ошибка создания кнопки: {line} - {e}")
            continue

    if not buttons:
        return clean_text, None

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return clean_text, keyboard


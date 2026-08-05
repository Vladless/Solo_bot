import json
import re

from datetime import datetime, timezone

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import and_, distinct, exists, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.constants import PAYMENT_SYSTEMS_EXCLUDED
from database.models import BlockedUser, Identity, Key, ManualBan, Payment, Server, Tariff, User
from logger import logger


def _not_banned(user_id_col):
    return ~exists().where(BlockedUser.user_id == user_id_col) & ~exists().where(
        ManualBan.user_id == user_id_col,
        (ManualBan.until.is_(None)) | (ManualBan.until > datetime.now(timezone.utc)),
    )


def is_telegram_chat_id(tg_id: int | None) -> bool:
    return tg_id is not None and tg_id > 0


ALL_CHANNELS = ("bot", "site", "email")


def parse_channels(channel: str | None) -> list[str]:
    if not channel:
        return ["bot", "site"]
    if channel == "both":
        return ["bot", "site"]
    parts = {p.strip() for p in channel.split(",") if p.strip()}
    ordered = [c for c in ALL_CHANNELS if c in parts]
    return ordered or ["bot", "site"]


def channels_to_str(channels) -> str:
    selected = set(channels)
    ordered = [c for c in ALL_CHANNELS if c in selected]
    return ",".join(ordered) if ordered else "bot,site"


def _email_exists(identity_col):
    return exists(
        select(1).select_from(Identity).where(and_(Identity.id == identity_col, Identity.email.isnot(None)))
    )


def _reachability_filters(tg_col, identity_col, *, channel: str | None, telegram_only: bool):
    filters = [tg_col.isnot(None)]
    if channel is not None:
        channels = parse_channels(channel)
        conds = []
        if "bot" in channels:
            conds.append(tg_col > 0)
        if "site" in channels:
            conds.append(identity_col.isnot(None))
        if "email" in channels:
            conds.append(_email_exists(identity_col))
        if conds:
            filters.append(or_(*conds))
    elif telegram_only:
        filters.append(tg_col > 0)
    return filters


async def get_recipient_emails(session: AsyncSession, tg_ids: list[int]) -> dict[int, str]:
    if not tg_ids:
        return {}
    result = await session.execute(
        select(User.tg_id, Identity.email)
        .join(Identity, User.identity_id == Identity.id)
        .where(User.tg_id.in_(tg_ids), Identity.email.isnot(None))
    )
    return {row[0]: row[1] for row in result.all() if row[1]}


async def get_recipients(
    session: AsyncSession,
    send_to: str,
    cluster_name: str | None = None,
    *,
    telegram_only: bool = False,
    channel: str | None = None,
) -> tuple[list[int], int]:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    query = None

    tg_filters = _reachability_filters(User.tg_id, User.identity_id, channel=channel, telegram_only=telegram_only)

    if send_to == "subscribed":
        query = (
            select(distinct(User.tg_id))
            .join(Key, Key.user_id == User.id)
            .where(Key.expiry_time > now_ms)
            .where(*tg_filters)
            .where(_not_banned(User.id))
        )

    elif send_to == "unsubscribed":
        unsub_base = (
            select(User.id.label("uid"), User.tg_id, User.identity_id)
            .outerjoin(Key, User.id == Key.user_id)
            .group_by(User.id, User.tg_id, User.identity_id)
            .having(func.count(Key.client_id) == 0)
            .union_all(
                select(User.id.label("uid"), User.tg_id, User.identity_id)
                .join(Key, User.id == Key.user_id)
                .group_by(User.id, User.tg_id, User.identity_id)
                .having(func.max(Key.expiry_time) <= now_ms)
            )
        ).subquery()
        unsub_tg_filters = _reachability_filters(
            unsub_base.c.tg_id, unsub_base.c.identity_id, channel=channel, telegram_only=telegram_only
        )
        query = (
            select(distinct(unsub_base.c.tg_id))
            .select_from(unsub_base)
            .where(*unsub_tg_filters)
            .where(
                ~exists().where(BlockedUser.user_id == unsub_base.c.uid),
                ~exists().where(
                    ManualBan.user_id == unsub_base.c.uid,
                    (ManualBan.until.is_(None)) | (ManualBan.until > datetime.now(timezone.utc)),
                ),
            )
        )

    elif send_to == "untrial":
        key_user_ids = select(Key.user_id).distinct()
        query = (
            select(distinct(User.tg_id))
            .where(~User.id.in_(key_user_ids) & User.trial.in_([0, -1]))
            .where(*tg_filters)
            .where(_not_banned(User.id))
        )

    elif send_to == "cluster":
        query = (
            select(distinct(User.tg_id))
            .join(Key, Key.user_id == User.id)
            .join(Server, Key.server_id == Server.cluster_name)
            .where(Server.cluster_name == cluster_name)
            .where(*tg_filters)
            .where(_not_banned(User.id))
        )

    elif send_to == "source":
        query = (
            select(distinct(User.tg_id))
            .where(User.source_code == cluster_name)
            .where(*tg_filters)
            .where(_not_banned(User.id))
        )

    elif send_to == "hotleads":
        query = (
            select(distinct(User.tg_id))
            .join(Payment, User.id == Payment.user_id)
            .where(Payment.status == "success")
            .where(Payment.amount > 0)
            .where(Payment.payment_system.notin_(PAYMENT_SYSTEMS_EXCLUDED))
            .where(
                not_(exists(select(1).select_from(Key).where(and_(Key.user_id == User.id, Key.expiry_time > now_ms))))
            )
            .where(*tg_filters)
            .where(_not_banned(User.id))
        )

    elif send_to == "trial":
        trial_tariff_subquery = select(Tariff.id).where(Tariff.group_code == "trial")
        query = (
            select(distinct(User.tg_id))
            .join(Key, Key.user_id == User.id)
            .where(Key.tariff_id.in_(trial_tariff_subquery))
            .where(*tg_filters)
            .where(_not_banned(User.id))
        )

    elif send_to == "no_email":
        has_email = exists(
            select(1).select_from(Identity).where(and_(Identity.tg_id == User.tg_id, Identity.email.isnot(None)))
        )
        query = select(distinct(User.tg_id)).where(not_(has_email)).where(*tg_filters).where(_not_banned(User.id))

    else:
        query = select(distinct(User.tg_id)).where(*tg_filters).where(_not_banned(User.id))

    result = await session.execute(query)
    tg_ids = [row[0] for row in result.all()]
    return tg_ids, len(tg_ids)


def strip_html_tags(text: str) -> str:
    text = re.sub(r'<tg-emoji emoji-id="[^"]*">([^<]*)</tg-emoji>', r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return text.strip()


def parse_message_buttons(text: str) -> tuple[str, InlineKeyboardMarkup | None]:
    if "BUTTONS:" not in text:
        return text, None

    parts = text.split("BUTTONS:", 1)
    clean_text = parts[0].strip()
    buttons_text = parts[1].strip()

    if not buttons_text:
        return clean_text, None

    buttons = []
    button_lines = [line.strip() for line in buttons_text.split("\n") if line.strip()]

    for line in button_lines:
        try:
            cleaned_line = re.sub(r'<tg-emoji emoji-id="[^"]*">([^<]*)</tg-emoji>', r"\1", line)
            button_data = json.loads(cleaned_line)

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

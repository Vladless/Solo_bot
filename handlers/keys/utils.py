import hashlib

from typing import Any

from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_key_by_client_id, get_key_by_email, get_keys
from database.models import Key
from services.payments.currency_rates import format_for_user
from handlers.utils import render_text
from settings.texts import (
    SUBGROUP_DESCRIPTION_TEXT,
    TARIFF_DESCRIPTIONS_HIDDEN,
    TARIFF_DESCRIPTIONS_ROW,
    TARIFF_DESCRIPTIONS_TEXT,
)


def key_owned_by_user(record: dict | None, user_id: int) -> bool:
    """Проверка, что ключ принадлежит пользователю (защита от пересылки callback)."""
    return record is not None and record.get("tg_id") == user_id


def build_key_ref(client_id: str | None, email: str | None = None) -> str:
    source = str(client_id or email or "")
    return hashlib.blake2s(source.encode(), digest_size=6).hexdigest()


def build_key_callback(prefix: str, client_id: str | None, email: str | None = None) -> str:
    return f"{prefix}|{build_key_ref(client_id, email)}"


async def resolve_key(session: AsyncSession, tg_id: int, key_ref: str | int | None) -> Key | None:
    if key_ref is None:
        return None

    key_ref_str = str(key_ref)

    for candidate in await get_keys(session, tg_id):
        if build_key_ref(candidate.client_id, candidate.email) == key_ref_str:
            return await get_key_by_client_id(session, candidate.client_id, tg_id)

    key_obj = await get_key_by_email(session, key_ref_str, tg_id)
    if key_obj:
        return key_obj

    key_obj = await get_key_by_client_id(session, key_ref_str, tg_id)
    if key_obj:
        return key_obj

    return await _resolve_key_from_db(session, tg_id, key_ref_str)


async def _resolve_key_from_db(session: AsyncSession, tg_id: int, key_ref: str) -> Key | None:
    """Ищет ключ по свежему списку из БД: кеш списка мог отстать от только что созданной подписки."""
    from database.access.resolution import resolve_uid_cached

    uid = await resolve_uid_cached(session, tg_id)
    if uid is None:
        return None
    rows = (await session.execute(select(Key).where(Key.user_id == uid))).scalars().all()
    for candidate in rows:
        if build_key_ref(candidate.client_id, candidate.email) == key_ref:
            return candidate
    return None


def _escape_html(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def order_tariff_items(grouped_tariffs: dict) -> list[tuple[str, Any]]:
    """Сквозной порядок выбора: одиночные тарифы и подгруппы вперемешку по sort_order.

    Подгруппа встаёт по минимальному sort_order своих тарифов. Раньше одиночные
    тарифы всегда шли первыми, из-за чего подгруппы оказывались внизу.
    Возвращает список ("tariff", tariff_dict) | ("subgroup", subgroup_title).
    """
    items: list[tuple[int, str, Any]] = []
    for t in grouped_tariffs.get(None, []):
        items.append((int(t.get("sort_order") or 0), "tariff", t))
    for subgroup in sorted(s for s in grouped_tariffs if s):
        tlist = grouped_tariffs[subgroup]
        min_order = min((int(t.get("sort_order") or 0) for t in tlist), default=0)
        items.append((min_order, "subgroup", subgroup))
    items.sort(key=lambda x: x[0])
    return [(kind, payload) for _, kind, payload in items]


def format_subgroup_description(description: str | None, limit: int = 300) -> str:
    """Блок описания подгруппы над списком тарифов (пусто, если не задано).

    Длина ограничена, чтобы caption фото не превысил лимит Telegram (1024).
    """
    text = (description or "").strip()
    if not text:
        return ""
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return render_text(SUBGROUP_DESCRIPTION_TEXT, description=_escape_html(text))


def format_tariff_descriptions(tariffs: list[dict[str, Any]], total_limit: int = 500) -> str:
    """Компактный блок описаний для списка тарифов рядом с кнопками.

    Описание схлопывается в одну строку. Длина тизера подбирается под число
    тарифов с описанием, а общий объём ограничен total_limit, чтобы caption
    не превысил лимит Telegram (1024) даже вместе со скидочным блоком.
    """
    described = [
        (str(t.get("name", "")), " ".join((t.get("description") or "").split()))
        for t in tariffs
        if (t.get("description") or "").strip()
    ]
    if not described:
        return ""

    per_limit = max(60, total_limit // len(described))
    lines: list[str] = []
    used = 0
    hidden = 0
    for name, desc in described:
        if len(desc) > per_limit:
            desc = desc[: per_limit - 1].rstrip() + "…"
        line = TARIFF_DESCRIPTIONS_ROW.format(name=_escape_html(name), description=_escape_html(desc))
        if used + len(line) + 1 > total_limit:
            hidden += 1
            continue
        lines.append(line)
        used += len(line) + 1

    if hidden:
        lines.append(TARIFF_DESCRIPTIONS_HIDDEN.format(count=hidden))
    return render_text(TARIFF_DESCRIPTIONS_TEXT, items="\n".join(lines))


async def add_tariff_button_generic(
    builder: InlineKeyboardBuilder,
    tariff: dict[str, Any],
    session: AsyncSession,
    tg_id: int,
    language_code: str | None,
    callback_prefix: str,
):
    """Добавляет кнопку тарифа с учётом конфигуратора."""
    is_configurable = bool(tariff.get("configurable"))
    if is_configurable:
        button_text = tariff["name"]
    else:
        price_rub = float(tariff.get("price_rub") or 0)
        price_text = await format_for_user(session, tg_id, price_rub, language_code)
        button_text = f"{tariff['name']} — {price_text}"

    builder.row(
        InlineKeyboardButton(
            text=button_text,
            callback_data=f"{callback_prefix}|{tariff['id']}",
        )
    )

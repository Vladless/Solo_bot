from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Setting
from database.models.web import WebNotification
from database.web_layout import slug_to_path
from logger import logger


RULES_SETTING_KEY = "WEB_NOTIFY_RULES"

TRIGGERS = ("login", "first_login")
AUDIENCES = ("all", "no_subscription", "with_subscription", "source")
REPEATS = ("once", "daily", "always")
DISPLAYS = ("popup", "feed")

MAX_RULES = 20
MAX_TITLE = 120
MAX_MESSAGE = 400
MAX_LABEL = 40


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _pick(value: Any, allowed: tuple[str, ...], fallback: str) -> str:
    raw = str(value or "").strip().lower()
    return raw if raw in allowed else fallback


def normalize_rule(raw: Any) -> dict | None:
    """Приводит правило к рабочему виду. Без заголовка правило показывать нечего."""
    if not isinstance(raw, dict):
        return None
    title = _text(raw.get("title"), MAX_TITLE)
    if not title:
        return None
    target = raw.get("target") if isinstance(raw.get("target"), dict) else {}
    return {
        "id": _text(raw.get("id"), 64) or _text(title, 64),
        "enabled": bool(raw.get("enabled", True)),
        "trigger": _pick(raw.get("trigger"), TRIGGERS, "login"),
        "audience": _pick(raw.get("audience"), AUDIENCES, "all"),
        "source_code": _text(raw.get("source_code"), 64),
        "repeat": _pick(raw.get("repeat"), REPEATS, "once"),
        "display": _pick(raw.get("display"), DISPLAYS, "popup"),
        "title": title,
        "message": _text(raw.get("message"), MAX_MESSAGE),
        "button_label": _text(raw.get("button_label"), MAX_LABEL),
        "target": {
            "page": _text(target.get("page"), 64),
            "tab_group": _text(target.get("tab_group"), 64),
            "tab": _text(target.get("tab"), 64),
            "screen_group": _text(target.get("screen_group"), 64),
            "screen": _text(target.get("screen"), 64),
            "anchor": _text(target.get("anchor"), 64).lstrip("#"),
            "url": _text(target.get("url"), 300),
        },
    }


def normalize_rules(raw: Any) -> list[dict]:
    items = raw if isinstance(raw, list) else []
    out: list[dict] = []
    seen: set[str] = set()
    for item in items[:MAX_RULES]:
        rule = normalize_rule(item)
        if rule is None:
            continue
        rule_id = rule["id"]
        while rule_id in seen:
            rule_id = f"{rule_id}-2"
        rule["id"] = rule_id
        seen.add(rule_id)
        out.append(rule)
    return out


def rule_href(rule: dict) -> str:
    """Куда ведёт кнопка: свой адрес или страница с вкладкой кабинета, экраном раздела и якорем блока."""
    target = rule.get("target") or {}
    url = str(target.get("url") or "").strip()
    if url:
        return url
    path = slug_to_path(str(target.get("page") or "").strip() or "dashboard")
    query: dict[str, str] = {}
    tab = str(target.get("tab") or "").strip()
    if tab and path == "/dashboard":
        query["tab"] = tab
    screen = str(target.get("screen") or "").strip()
    if screen:
        group = str(target.get("screen_group") or "").strip()
        query["screen"] = f"{group}:{screen}" if group else screen
    anchor = str(target.get("anchor") or "").strip().lstrip("#")
    return f"{path}{f'?{urlencode(query)}' if query else ''}{f'#{anchor}' if anchor else ''}"


def rule_payload(rule: dict) -> dict:
    """Что показывает сайт: текст, подпись кнопки и куда она ведёт."""
    return {
        "rule": rule["id"],
        "display": rule["display"],
        "title": rule["title"],
        "message": rule["message"],
        "button_label": rule["button_label"],
        "href": rule_href(rule),
        "target": dict(rule["target"]),
    }


async def load_rules(session: AsyncSession) -> list[dict]:
    setting = await session.scalar(select(Setting).where(Setting.key == RULES_SETTING_KEY))
    return normalize_rules(setting.value if setting is not None else [])


async def save_rules(session: AsyncSession, raw: Any) -> list[dict]:
    rules = normalize_rules(raw)
    setting = await session.scalar(select(Setting).where(Setting.key == RULES_SETTING_KEY))
    if setting is None:
        session.add(Setting(key=RULES_SETTING_KEY, value=rules, description="Свои уведомления сайта"))
    else:
        setting.value = rules
    await session.flush()
    return rules


def _audience_ok(rule: dict, *, has_subscription: bool, source_code: str | None) -> bool:
    audience = rule.get("audience")
    if audience == "no_subscription":
        return not has_subscription
    if audience == "with_subscription":
        return has_subscription
    if audience == "source":
        wanted = str(rule.get("source_code") or "").strip().lower()
        return bool(wanted) and str(source_code or "").strip().lower() == wanted
    return True


async def _already_sent(session: AsyncSession, identity_id: str | None, rule_id: str, repeat: str) -> bool:
    if repeat == "always":
        return False
    stmt = (
        select(func.count())
        .select_from(WebNotification)
        .where(
            WebNotification.identity_id == identity_id,
            WebNotification.type == "custom",
            WebNotification.data["rule"].astext == rule_id,
        )
    )
    if repeat == "daily":
        stmt = stmt.where(WebNotification.created_at >= datetime.now(UTC) - timedelta(hours=24))
    return bool(await session.scalar(stmt))


async def apply_rules_on_login(
    session: AsyncSession,
    *,
    user_id: int,
    identity_id: str | None,
    first_login: bool,
    has_subscription: bool,
    source_code: str | None = None,
) -> list[dict]:
    """Создаёт уведомления по правилам админа. Возвращает сработавшие правила для показа на странице."""
    from database.web_notifications import create_notification

    try:
        rules = await load_rules(session)
    except Exception as exc:
        logger.warning(f"[WebNotifyRules] правила не прочитаны: {exc}")
        return []

    fired: list[dict] = []
    for rule in rules:
        if not rule["enabled"]:
            continue
        if rule["trigger"] == "first_login" and not first_login:
            continue
        if not _audience_ok(rule, has_subscription=has_subscription, source_code=source_code):
            continue
        if await _already_sent(session, identity_id, rule["id"], rule["repeat"]):
            continue
        payload = rule_payload(rule)
        notification = await create_notification(
            session,
            user_id=user_id,
            identity_id=identity_id,
            type="custom",
            title=rule["title"],
            message=rule["message"],
            data=payload,
        )
        fired.append({**payload, "notification_id": str(notification.id)})
    return fired

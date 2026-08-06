import html
import re

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytz

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from audit import list_audit_events
from core.redis_cache import cache_get, cache_key, cache_set
from database.models import User
from filters.admin import IsAdminFilter
from settings.cache_config import AUDIT_HISTORY_CACHE_TTL_SEC

from ..panel.headers import card, menu_text, quote, section, strip_tags
from .keyboard import AdminUserEditorCallback, build_user_audit_kb


MOSCOW_TZ = pytz.timezone("Europe/Moscow")
PAGE_SIZE = 10
LOGS_DIR = Path(__file__).resolve().parents[3] / "logs"
BALANCE_LOG_PATTERN = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| [^|]+ \| [^|]+ \| .*?\[DB\] Баланс пользователя "
    r"(?P<tg_id>\d+) обновлён: (?P<old>-?\d+(?:\.\d+)?) → (?P<new>-?\d+(?:\.\d+)?)$"
)
router = Router()


def _serialize_audit_events(events: list) -> list[dict]:
    """Для кэша Redis: список событий в JSON-сериализуемый вид."""
    out = []
    for e in events:
        out.append({
            "event_type": e.event_type,
            "channel": e.channel,
            "path_or_handler": getattr(e, "path_or_handler", None) or "",
            "actor_identity_id": getattr(e, "actor_identity_id", None),
            "actor_tg_id": getattr(e, "actor_tg_id", None),
            "entity_type": getattr(e, "entity_type", None),
            "entity_id": getattr(e, "entity_id", None),
            "result": getattr(e, "result", "success"),
            "reason": getattr(e, "reason", None),
            "metadata_": getattr(e, "metadata_", None),
            "request_id": getattr(e, "request_id", None),
            "created_at": e.created_at.isoformat() if e.created_at else None,
        })
    return out


def _deserialize_audit_events(cached: list[dict]) -> list:
    """Из кэша: список dict → объекты с атрибутами как у AuditEvent."""
    out = []
    for d in cached:
        created = d.get("created_at")
        if isinstance(created, str):
            try:
                created = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except Exception:
                created = None
        out.append(
            SimpleNamespace(
                event_type=d.get("event_type", ""),
                channel=d.get("channel", "telegram"),
                path_or_handler=d.get("path_or_handler") or "",
                actor_identity_id=d.get("actor_identity_id"),
                actor_tg_id=d.get("actor_tg_id"),
                entity_type=d.get("entity_type"),
                entity_id=d.get("entity_id"),
                result=d.get("result", "success"),
                reason=d.get("reason"),
                metadata_=d.get("metadata_"),
                request_id=d.get("request_id"),
                created_at=created,
            )
        )
    return out


def _event_created_at(event) -> datetime:
    created = getattr(event, "created_at", None)
    if created is None:
        return datetime.min
    if created.tzinfo is not None:
        return created.astimezone(pytz.UTC).replace(tzinfo=None)
    return created


def _event_created_at_moscow(event) -> datetime | None:
    created = getattr(event, "created_at", None)
    if created is None:
        return None
    if getattr(event, "event_type", "") == "balance_changed" and created.tzinfo is None:
        return MOSCOW_TZ.localize(created)
    if created.tzinfo is None:
        return pytz.UTC.localize(created).astimezone(MOSCOW_TZ)
    return created.astimezone(MOSCOW_TZ)


def _load_balance_log_events(tg_id: int, limit: int) -> list:
    if limit <= 0 or not LOGS_DIR.is_dir():
        return []

    files = [path for path in LOGS_DIR.iterdir() if path.is_file() and path.name.startswith("logging")]
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)

    events = []
    for path in files:
        try:
            with open(path, encoding="utf-8", errors="ignore") as file:
                lines = file.read().splitlines()
        except Exception:
            continue

        for line in reversed(lines):
            match = BALANCE_LOG_PATTERN.match(line.strip())
            if not match:
                continue
            if int(match.group("tg_id")) != tg_id:
                continue

            old_balance = float(match.group("old"))
            new_balance = float(match.group("new"))
            amount = new_balance - old_balance
            if amount == 0:
                continue

            try:
                created_at = MOSCOW_TZ.localize(datetime.strptime(match.group("ts"), "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                continue

            events.append(
                SimpleNamespace(
                    event_type="balance_changed",
                    channel="system",
                    path_or_handler="logger:balance",
                    actor_identity_id=None,
                    actor_tg_id=tg_id,
                    entity_type="telegram_user",
                    entity_id=tg_id,
                    result="success",
                    reason=None,
                    metadata_={
                        "amount": amount,
                        "balance_before": old_balance,
                        "balance_after": new_balance,
                    },
                    request_id=None,
                    created_at=created_at,
                )
            )
            if len(events) >= limit:
                break
        if len(events) >= limit:
            break

    events.sort(key=_event_created_at, reverse=True)
    return events[:limit]


EVENT_CATEGORY_MAP = {
    "balance": {
        "balance_changed",
    },
    "auth": {
        "register_success",
        "register_failed",
        "login_success",
        "login_failed",
        "login_code_sent",
        "login_code_send_failed",
        "login_by_code_success",
        "login_by_code_failed",
        "telegram_login_success",
        "telegram_login_failed",
        "telegram_link_success",
        "telegram_link_failed",
    },
    "payments": {
        "payment_menu_opened",
        "payment_currency_selected",
        "balance_screen_opened",
        "balance_history_opened",
        "payment_link_created",
        "payment_link_create_failed",
        "coupon_activation_success",
        "coupon_activation_failed",
        "coupon_renewal_success",
        "coupon_renewal_failed",
        "coupon_key_selection_opened",
    },
    "subscriptions": {
        "trial_requested",
        "key_purchase_started",
        "keys_list_opened",
        "key_view_opened",
        "key_alias_updated",
        "key_hwid_reset",
        "key_renew_requested",
        "key_renew_blocked",
        "key_renew_insufficient_funds",
        "key_renew_completed",
        "key_renew_failed",
    },
    "marketing": {
        "start_link_opened",
        "coupon_link_opened",
        "gift_link_opened",
        "referral_link_opened",
        "referral_link_applied",
        "referral_link_failed",
        "referral_screen_opened",
        "referral_qr_opened",
        "top_referrals_opened",
        "utm_link_opened",
        "utm_link_failed",
    },
}

EVENT_TYPE_LABELS = {
    "balance_changed": "Движение баланса",
    "start_entry_opened": "Старт бота",
    "start_link_opened": "Переход по ссылке",
    "register_success": "Регистрация (успех)",
    "register_failed": "Регистрация (ошибка)",
    "login_success": "Вход по паролю",
    "login_failed": "Вход (ошибка)",
    "login_code_sent": "Код входа отправлен",
    "login_code_send_failed": "Код входа не отправлен",
    "login_by_code_success": "Вход по коду",
    "login_by_code_failed": "Вход по коду (ошибка)",
    "telegram_login_success": "Вход через Telegram",
    "telegram_login_failed": "Вход через Telegram (ошибка)",
    "telegram_link_success": "Привязка Telegram",
    "telegram_link_failed": "Привязка Telegram (ошибка)",
    "payment_menu_opened": "Меню оплаты",
    "payment_currency_selected": "Выбор валюты",
    "balance_screen_opened": "Экран баланса",
    "balance_history_opened": "История пополнений",
    "payment_link_created": "Создана платёжная ссылка",
    "payment_link_create_failed": "Ошибка создания ссылки",
    "coupon_activation_success": "Купон активирован",
    "coupon_activation_failed": "Купон не активирован",
    "coupon_renewal_success": "Продление по купону",
    "coupon_renewal_failed": "Продление по купону (ошибка)",
    "coupon_key_selection_opened": "Выбор ключа для купона",
    "coupon_link_opened": "Переход по ссылке купона",
    "trial_requested": "Запрос триала",
    "key_purchase_started": "Начало покупки подписки",
    "keys_list_opened": "Список подписок",
    "key_view_opened": "Карточка подписки",
    "key_alias_updated": "Переименование подписки",
    "key_hwid_reset": "Сброс устройств (HWID)",
    "key_renew_requested": "Запрос продления",
    "key_renew_blocked": "Продление ещё недоступно",
    "key_renew_insufficient_funds": "Не хватило баланса на продление",
    "key_renew_completed": "Подписка продлена",
    "key_renew_failed": "Ошибка продления",
    "gift_link_opened": "Переход по подарочной ссылке",
    "referral_link_opened": "Переход по реферальной ссылке",
    "referral_link_applied": "Реферал применён",
    "referral_link_failed": "Реферальная ссылка (ошибка)",
    "referral_screen_opened": "Экран рефералов",
    "referral_qr_opened": "QR реферальной ссылки",
    "top_referrals_opened": "Топ рефералов",
    "utm_link_opened": "Переход по UTM",
    "utm_link_failed": "UTM не найден",
}


def _parse_filter_page(raw_data: str | int | None) -> tuple[str, str, int]:
    if isinstance(raw_data, str):
        parts = raw_data.split("|")
        if len(parts) == 3:
            channel_filter, category_filter, page_str = parts
        elif len(parts) == 2:
            channel_filter, page_str = parts
            category_filter = "all"
        else:
            return "all", "all", 0
        if channel_filter not in {"all", "api", "telegram"}:
            channel_filter = "all"
        if category_filter not in {"all", "balance", "auth", "payments", "subscriptions", "marketing"}:
            category_filter = "all"
        if page_str.isdigit():
            return channel_filter, category_filter, int(page_str)
        return channel_filter, category_filter, 0
    return "all", "all", 0


def _resolve_event_types(category_filter: str) -> list[str] | None:
    if category_filter == "all":
        return None
    category_events = EVENT_CATEGORY_MAP.get(category_filter)
    if not category_events:
        return None
    return sorted(category_events)


CATEGORY_BLOCK_ORDER = ("balance", "auth", "subscriptions", "payments", "marketing", "other")
CATEGORY_BLOCK_LABELS = {
    "balance": "Баланс",
    "auth": "Авторизация",
    "payments": "Платежи",
    "subscriptions": "Подписки",
    "marketing": "Маркетинг",
    "other": "Другое",
}


def _event_category(event) -> str:
    """Определяет категорию события для группировки (при выборке «все»)."""
    path = (getattr(event, "path_or_handler", None) or "").lower()
    etype = (getattr(event, "event_type", None) or "").lower()
    if etype == "balance_changed":
        return "balance"
    if etype != "telegram_access":
        for cat, event_types in EVENT_CATEGORY_MAP.items():
            if etype in event_types:
                return cat
        return "other"
    if (
        "handlers.keys" in path
        or "key_view" in path
        or "key_create" in path
        or "key_renew" in path
        or "tariffs" in path
        or "key_tariffs" in path
        or "addon" in path
        or "key_addons" in path
    ):
        return "subscriptions"
    if (
        "handlers.payments" in path
        or "pay" in path
        or "balance" in path
        or "handlers.coupons" in path
        or "payment" in path
    ):
        return "payments"
    if "handlers.refferal" in path or "referral" in path or "gift" in path or "utm" in path:
        return "marketing"
    if "handlers.start" in path or "start_entry" in path or "process_start" in path:
        return "marketing"
    if "auth" in path or "login" in path or "register" in path:
        return "auth"
    return "other"


def _format_metadata(metadata: dict | None) -> str | None:
    if not metadata:
        return None
    items = []
    for key in sorted(metadata.keys()):
        value = metadata[key]
        if value is None or value == "":
            continue
        items.append(f"{key}={value}")
        if len(items) >= 3:
            break
    if not items:
        return None
    return ", ".join(items)


def _event_label(event_type: str) -> str:
    return EVENT_TYPE_LABELS.get(event_type, event_type)


_FLOW_INDENT = "   "


def _fmt_money(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _format_balance_event(event) -> str:
    metadata = getattr(event, "metadata_", None) or {}
    amount = metadata.get("amount", 0)
    before = metadata.get("balance_before")
    after = metadata.get("balance_after")
    amount_value = float(amount) if isinstance(amount, int | float) else 0.0
    title = "Пополнение" if amount_value > 0 else "Списание" if amount_value < 0 else "Изменение баланса"
    direction = "+" if amount_value > 0 else ""

    lines = [f"{html.escape(title)}: <code>{direction}{html.escape(_fmt_money(amount))} RUB</code>"]
    if before is not None and after is not None:
        lines.append(
            f"{_FLOW_INDENT}Баланс: <code>{html.escape(_fmt_money(before))}</code> → "
            f"<code>{html.escape(_fmt_money(after))}</code>"
        )
    if event.reason:
        lines.append(f"{_FLOW_INDENT}{html.escape(str(event.reason)[:100])}")
    return "\n".join(lines)


def _humanize_path(path: str) -> str:
    """Сокращает типичные callback для админки до читаемого вида."""
    if not path or "callback:" not in path:
        return path
    if "users_audit:" in path:
        rest = path.split("users_audit:", 1)[-1].strip()
        parts = [p.strip() for p in rest.split("|")[:2] if p.strip()]
        if len(parts) >= 2:
            ch, cat = parts[0].split(":")[-1] if ":" in parts[0] else parts[0], parts[1]
            return f"История: {ch}, {cat}"
        return "История"
    if "users_editor:" in path:
        return "Карточка пользователя"
    if "admin_panel:search_user" in path:
        return "Поиск пользователя"
    if "admin_panel:admin" in path:
        return "Админ-панель"
    if "message:" in path:
        text = path.split("message:", 1)[-1].strip()
        if text and text != "-":
            return f"Сообщение: {text[:40]}{'…' if len(text) > 40 else ''}"
    return path[:55] + ("…" if len(path) > 55 else "")


def _format_event_line(
    event, show_request_id: bool = False, inside_block: bool = False, skip_time: bool = False
) -> str:
    """Строка события. Если skip_time=True — только описание (время уже в строке статуса)."""
    created_dt = _event_created_at_moscow(event)
    created_at = created_dt.strftime("%d.%m %H:%M:%S") if created_dt is not None else "unknown"
    if event.event_type == "balance_changed":
        time_part = "" if skip_time else f"<code>{created_at}</code> "
        return f"{time_part}{_format_balance_event(event)}"
    if event.event_type == "telegram_access":
        raw_path = (event.path_or_handler or "—").strip()
        event_name = html.escape(_humanize_path(raw_path))
        if event_name == raw_path and len(raw_path) > 55:
            event_name = html.escape(raw_path[:55] + "…")
    else:
        event_name = html.escape(_event_label(event.event_type))
    req_part = ""
    if show_request_id and not inside_block and getattr(event, "request_id", None):
        short_id = (event.request_id or "")[:8]
        if short_id:
            req_part = f" <code>[{short_id}]</code>"
    entity = ""
    if event.entity_type or event.entity_id:
        etype = html.escape(str(event.entity_type or "entity"))
        raw_id = str(event.entity_id or "")
        eid = html.escape(raw_id[:40] + ("…" if len(raw_id) > 40 else ""))
        if event.entity_type == "telegram_user" and raw_id.isdigit():
            entity = f"\n{_FLOW_INDENT}tg_id: <code>{eid}</code>"
        else:
            entity = f"\n{_FLOW_INDENT}{etype}: <code>{eid}</code>"

    metadata_line = _format_metadata(event.metadata_)
    if metadata_line:
        metadata_line = f"\n{_FLOW_INDENT}{html.escape(metadata_line)}"
    else:
        metadata_line = ""

    reason_line = ""
    if event.reason:
        reason_line = f"\n{_FLOW_INDENT}{html.escape(str(event.reason)[:100])}"

    time_part = "" if skip_time else f"<code>{created_at}</code> "
    return f"{time_part}{event_name}{req_part}{entity}{metadata_line}{reason_line}"


def _format_event_status(event) -> str:
    """Только время и результат (ок/ошибка)."""
    created_dt = _event_created_at_moscow(event)
    created_at = created_dt.strftime("%d.%m %H:%M:%S") if created_dt is not None else "unknown"
    result_text = "ок" if event.result == "success" else "ошибка"
    return f"<code>{created_at}</code> {result_text}"


def _render_events_as_flow(events: list) -> list[str]:
    """Возвращает события ленты аудита секциями."""
    blocks = []
    for number, event in enumerate(events, start=1):
        mark = "✅" if event.result == "success" else "❌"
        title = f"{mark} {number}. {strip_tags(_format_event_status(event))}"
        body = _format_event_line(event, show_request_id=False, inside_block=True, skip_time=True)
        rows = [strip_tags(row).strip() for row in body.split("\n")]
        blocks.append(section(title, *[row for row in rows if row]))
    return blocks


async def _render_user_audit(
    message: Message,
    session: AsyncSession,
    tg_id: int,
    *,
    channel_filter: str = "all",
    category_filter: str = "all",
    page: int = 0,
) -> None:
    page = max(0, page)
    user_identity_id = await session.scalar(select(User.identity_id).where(User.tg_id == tg_id))
    channel = None if channel_filter == "all" else channel_filter
    event_types = _resolve_event_types(category_filter)
    include_balance_logs = channel_filter == "all" and category_filter in {"all", "balance"}
    combined_limit = (page + 1) * PAGE_SIZE + 1

    cache_key_str = cache_key(
        "audit_history",
        tg_id,
        user_identity_id or "",
        channel_filter,
        category_filter,
        page,
    )
    cached = None if include_balance_logs else await cache_get(cache_key_str)
    if cached is not None and isinstance(cached, list):
        raw_events = _deserialize_audit_events(cached)
        has_prev = page > 0
        has_next = len(raw_events) > PAGE_SIZE
        events = raw_events[:PAGE_SIZE]
    else:
        if include_balance_logs:
            audit_events = []
            if category_filter != "balance":
                audit_events = await list_audit_events(
                    session,
                    tg_id=tg_id,
                    identity_id=user_identity_id,
                    channel=channel,
                    event_types=event_types,
                    limit=combined_limit,
                    offset=0,
                )
            balance_events = _load_balance_log_events(tg_id, combined_limit)
            merged_events = sorted(audit_events + balance_events, key=_event_created_at, reverse=True)
            start = page * PAGE_SIZE
            stop = start + PAGE_SIZE
            has_prev = page > 0
            has_next = len(merged_events) > stop
            events = merged_events[start:stop]
        else:
            raw_events = await list_audit_events(
                session,
                tg_id=tg_id,
                identity_id=user_identity_id,
                channel=channel,
                event_types=event_types,
                limit=PAGE_SIZE + 1,
                offset=page * PAGE_SIZE,
            )
            has_prev = page > 0
            has_next = len(raw_events) > PAGE_SIZE
            events = raw_events[:PAGE_SIZE]
            if raw_events:
                await cache_set(
                    cache_key_str,
                    _serialize_audit_events(raw_events),
                    AUDIT_HISTORY_CACHE_TTL_SEC,
                )

    full_flow = channel_filter == "all" and category_filter == "all"

    head = [f"Канал: {channel_filter}", f"Категория: {category_filter}"]
    if user_identity_id:
        head.append(f"Identity: {user_identity_id}")
    blocks = [section("🔎 Фильтр", *head)]

    if not events:
        blocks.append(quote("Событий пока нет"))
    else:
        rev = list(reversed(events))
        if full_flow and not any(getattr(event, "event_type", "") == "balance_changed" for event in rev):
            by_cat: dict[str, list] = {}
            for event in rev:
                by_cat.setdefault(_event_category(event), []).append(event)
            for cat in CATEGORY_BLOCK_ORDER:
                if cat in by_cat:
                    blocks.append(f"<b>▸ {CATEGORY_BLOCK_LABELS[cat]}</b>")
                    blocks.extend(_render_events_as_flow(by_cat[cat]))
        else:
            blocks.extend(_render_events_as_flow(rev))

    await message.edit_text(
        text=menu_text("Аудит", f"Клиент <code>{tg_id}</code>", card(*blocks)),
        reply_markup=build_user_audit_kb(
            tg_id=tg_id,
            channel_filter=channel_filter,
            category_filter=category_filter,
            page=page,
            has_prev=has_prev,
            has_next=has_next,
        ),
        disable_web_page_preview=True,
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_audit"),
    IsAdminFilter(),
)
async def handle_user_audit(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    channel_filter, category_filter, page = _parse_filter_page(callback_data.data)
    await _render_user_audit(
        callback_query.message,
        session,
        callback_data.tg_id,
        channel_filter=channel_filter,
        category_filter=category_filter,
        page=page,
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_audit_page"),
    IsAdminFilter(),
)
async def handle_user_audit_page(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    channel_filter, category_filter, page = _parse_filter_page(callback_data.data)
    await _render_user_audit(
        callback_query.message,
        session,
        callback_data.tg_id,
        channel_filter=channel_filter,
        category_filter=category_filter,
        page=page,
    )

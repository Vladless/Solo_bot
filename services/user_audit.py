import asyncio
import json
import re

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytz

from sqlalchemy import select

from audit import list_audit_events, safe_record_audit_event
from database.access.resolution import public_tg_id
from database.models import AuditEvent
from logger import logger


MOSCOW_TZ = pytz.timezone("Europe/Moscow")


LOGS_DIR = Path(__file__).resolve().parents[1] / "logs"


BALANCE_LOG_PATTERN = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| [^|]+ \| [^|]+ \| .*?\[DB\] Баланс пользователя "
    r"(?:id=(?P<user_id>\d+)|(?P<tg_id>-?\d+)) обновлён: "
    r"(?P<old>-?\d+(?:\.\d+)?) → (?P<new>-?\d+(?:\.\d+)?)$"
)


def _event_created_at(event) -> datetime:
    """Возвращает время события в UTC для сортировки."""
    created = getattr(event, "created_at", None)
    if created is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if created.tzinfo is not None:
        return created.astimezone(timezone.utc)
    return created.replace(tzinfo=timezone.utc)


def _event_created_at_moscow(event) -> datetime | None:
    """Возвращает время события для истории в боте."""
    created = getattr(event, "created_at", None)
    if created is None:
        return None
    if getattr(event, "event_type", "") == "balance_changed" and created.tzinfo is None:
        return MOSCOW_TZ.localize(created)
    if created.tzinfo is None:
        return pytz.UTC.localize(created).astimezone(MOSCOW_TZ)
    return created.astimezone(MOSCOW_TZ)


def _load_balance_log_events(
    user_id: int,
    limit: int,
    *,
    tg_id: int | None = None,
    identity_id: str | None = None,
) -> list:
    """Восстанавливает движения баланса из старых журналов."""
    if limit <= 0 or not LOGS_DIR.is_dir():
        return []

    files = [path for path in LOGS_DIR.iterdir() if path.is_file() and path.name.startswith("logging")]
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)

    events = []
    for path in files:
        try:
            with open(path, encoding="utf-8", errors="ignore") as file:
                lines = file.read().splitlines()
        except OSError as exc:
            logger.debug("[Site:Audit] Не удалось прочитать журнал {}: {}", path.name, type(exc).__name__)
            continue

        for line in reversed(lines):
            match = BALANCE_LOG_PATTERN.match(line.strip())
            if not match:
                continue
            logged_user_id = match.group("user_id")
            logged_tg_id = match.group("tg_id")
            if logged_user_id is not None:
                if int(logged_user_id) != user_id:
                    continue
                entity_type, entity_id = "user", user_id
            elif tg_id is None or int(logged_tg_id) != tg_id:
                continue
            else:
                entity_type, entity_id = "telegram_user", tg_id

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
                    actor_identity_id=identity_id,
                    actor_tg_id=tg_id if tg_id is not None and tg_id > 0 else None,
                    entity_type=entity_type,
                    entity_id=entity_id,
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


def _resolve_event_types(category_filter: str) -> list[str] | None:
    """Выбирает типы событий для категории."""
    if category_filter == "all":
        return None
    category_events = EVENT_CATEGORY_MAP.get(category_filter)
    if not category_events:
        return None
    return sorted(category_events)


def _event_category(event) -> str:
    """Определяет категорию события аудита."""
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


EVENT_TYPE_LABELS.update({
    "admin_user_subscription_created": "Администратор создал подписку",
    "admin_user_trial_restored": "Администратор вернул пробный период",
    "admin_user_message_sent": "Администратор отправил сообщение",
    "admin_user_email_detached": "Администратор отвязал почту",
    "admin_user_telegram_detached": "Администратор отвязал Telegram",
    "admin_user_cabinet_sent": "Администратор отправил ссылку на кабинет",
    "admin_user_autorenew_disabled": "Администратор отключил автопродление",
    "admin_user_gift_deleted": "Администратор удалил подарок",
    "admin_user_profile_updated": "Администратор обновил профиль",
    "admin_user_balance_adjusted": "Администратор изменил баланс",
    "admin_user_banned": "Администратор заблокировал клиента",
    "admin_user_unbanned": "Администратор разблокировал клиента",
})
EVENT_CATEGORY_MAP["subscriptions"].update({
    "admin_user_subscription_created",
    "admin_user_trial_restored",
    "admin_user_autorenew_disabled",
})
EVENT_CATEGORY_MAP["auth"].update({
    "admin_user_email_detached",
    "admin_user_telegram_detached",
    "admin_user_banned",
    "admin_user_unbanned",
})
EVENT_CATEGORY_MAP["marketing"].add("admin_user_gift_deleted")
EVENT_CATEGORY_MAP["balance"].add("admin_user_balance_adjusted")
_SENSITIVE_KEY_RE = re.compile(
    r"token|password|secret|api[_-]?key|authorization|cookie|session|auth|credential|bearer|pass|"
    r"phone|email|hash|private|pin|code|recipient|message|text",
    re.IGNORECASE,
)


def _redact_metadata(value, depth: int = 0):
    """Скрывает реквизиты и секреты в истории клиента."""
    if depth >= 6:
        return "[redacted]"
    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if _SENSITIVE_KEY_RE.search(str(key)) else _redact_metadata(item, depth + 1)
            for key, item in list(value.items())[:100]
        }
    if isinstance(value, list):
        return [_redact_metadata(item, depth + 1) for item in value[:100]]
    if isinstance(value, str):
        return value[:2000]
    return value


def _safe_path(path: str) -> str:
    """Убирает параметры ссылок и содержимое сообщений."""
    if path.startswith("message:"):
        return "message: Сообщение клиента"
    if path.startswith("callback:") and _SENSITIVE_KEY_RE.search(path):
        return "callback: Действие клиента"
    return path.partition("?")[0][:255]


def _audit_item(event, user) -> dict:
    """Готовит безопасную запись аудита для веб-карточки."""
    path = _safe_path(getattr(event, "path_or_handler", "") or "")
    event_type = getattr(event, "event_type", "")
    actor_identity = getattr(event, "actor_identity_id", None)
    actor_tg = public_tg_id(getattr(event, "actor_tg_id", None))
    own_actor = bool(
        (user.identity_id and actor_identity == user.identity_id)
        or (public_tg_id(user.tg_id) and actor_tg == public_tg_id(user.tg_id))
    )
    is_target = getattr(event, "entity_type", None) == "user" and str(getattr(event, "entity_id", "")) == str(user.id)
    kind = "administration" if is_target and (event_type.startswith("admin_") or not own_actor) else "client"
    created_at = getattr(event, "created_at", None)
    if created_at is not None and created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return {
        "id": getattr(event, "id", None),
        "event_type": event_type,
        "label": EVENT_TYPE_LABELS.get(event_type, path if event_type.endswith("_access") else event_type),
        "category": _event_category(event),
        "kind": kind,
        "channel": getattr(event, "channel", ""),
        "actor_identity_id": actor_identity,
        "actor_tg_id": actor_tg,
        "path_or_handler": path,
        "entity_type": getattr(event, "entity_type", None),
        "entity_id": str(event.entity_id) if getattr(event, "entity_id", None) is not None else None,
        "result": getattr(event, "result", "success"),
        "reason": None,
        "metadata": _redact_metadata(getattr(event, "metadata_", None)),
        "request_id": getattr(event, "request_id", None),
        "created_at": created_at.isoformat() if created_at is not None else None,
    }


async def user_action_history(session, user, *, channel: str = "all", category: str = "all", page=0, limit=20):
    """Объединяет действия клиента и администратора без чужих событий."""
    needed = (page + 1) * limit + 1
    event_types = _resolve_event_types(category)
    channel_filter = None if channel == "all" else channel
    events = []
    tg_id = public_tg_id(user.tg_id)
    if user.identity_id or tg_id is not None:
        events = await list_audit_events(
            session,
            identity_id=user.identity_id,
            tg_id=tg_id,
            channel=channel_filter,
            event_types=event_types,
            limit=needed,
        )
    target_stmt = select(AuditEvent).where(
        AuditEvent.entity_type == "user",
        AuditEvent.entity_id == str(user.id),
        AuditEvent.event_type.startswith("admin_user_", autoescape=True),
    )
    if channel_filter is not None:
        target_stmt = target_stmt.where(AuditEvent.channel == channel_filter)
    if event_types:
        target_stmt = target_stmt.where(AuditEvent.event_type.in_(event_types))
    target_result = await session.execute(
        target_stmt.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(needed)
    )
    events = [*events, *target_result.scalars().all()]
    if channel == "all" and category in {"all", "balance"}:
        events.extend(
            await asyncio.to_thread(
                _load_balance_log_events, user.id, needed, tg_id=tg_id, identity_id=user.identity_id
            )
        )
    unique = {}
    for event in events:
        request_id = getattr(event, "request_id", None)
        event_id = getattr(event, "id", None)
        if request_id:
            key = ("request", request_id, event.event_type, event.path_or_handler, event.result)
        elif event_id is not None:
            key = ("db", event_id)
        else:
            key = (
                "raw",
                event.event_type,
                event.path_or_handler,
                _event_created_at(event),
                json.dumps(getattr(event, "metadata_", None), sort_keys=True, default=str),
            )
        unique.setdefault(key, event)
    merged = sorted(unique.values(), key=_event_created_at, reverse=True)
    offset = page * limit
    return {
        "items": [_audit_item(event, user) for event in merged[offset : offset + limit]],
        "page": page,
        "limit": limit,
        "has_next": len(merged) > offset + limit,
    }


async def record_admin_user_event(
    session,
    *,
    identity,
    user_id: int,
    event_type: str,
    path: str,
    metadata: dict | None = None,
    request_id: str | None = None,
) -> None:
    """Записывает действие администратора над каноническим клиентом."""
    await safe_record_audit_event(
        session,
        event_type=event_type,
        channel="api",
        path_or_handler=_safe_path(path),
        actor_identity_id=getattr(identity, "id", None),
        actor_tg_id=public_tg_id(getattr(identity, "tg_id", None)),
        entity_type="user",
        entity_id=int(user_id),
        metadata=_redact_metadata(metadata),
        request_id=request_id,
    )

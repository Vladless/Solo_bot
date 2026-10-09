import ast
import json
import math
import os
import re

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from database.settings import get_setting, set_setting
from database.settings_cache import settings_cache
from logger import logger
from settings import texts

from .payments_config import PAYMENTS_CONFIG
from .runtime_sync import publish_runtime_config, register_runtime_config


YOOKASSA_AUTOPAY_SETTING_KEY = "YOOKASSA_AUTOPAY_CONFIG"
DEFAULT_YOOKASSA_AUTOPAY_CONFIG: dict[str, Any] = {
    "SHOP_ID": "",
    "SECRET_KEY": "",
    "DAYS_BEFORE_EXPIRY": 3,
    "MAX_RETRY_ATTEMPTS": 30,
    "RETRY_INTERVAL_HOURS": 24,
    "AUTOPAY_DISCOUNT_PERCENT": 0.0,
    "MIN_PAYMENT_AMOUNT": 1.0,
    "QUICK_AMOUNTS": [90, 500, 900],
    "NOTIFY_SUCCESS": True,
    "NOTIFY_FAILED_ATTEMPT": False,
    "NOTIFY_ALL_ATTEMPTS_FAILED": True,
    "NOTIFY_SUBSCRIPTION_CANCELLED": True,
    "MANAGE_BUTTON_LOCATION": "profile",
    "SEND_RECEIPT": True,
    "RECEIPT_EMAIL": "",
    "RECEIPT_PHONE": "",
    "TAX_SYSTEM_CODE": 1,
    "VAT_CODE": 1,
    "PAYMENT_MODE": "full_payment",
    "PAYMENT_SUBJECT": "service",
    "API_TIMEOUT": 30,
    "WEBHOOK_PATH": "/yookassa_autopay/webhook",
}
YOOKASSA_AUTOPAY_CONFIG: dict[str, Any] = DEFAULT_YOOKASSA_AUTOPAY_CONFIG.copy()
register_runtime_config(YOOKASSA_AUTOPAY_SETTING_KEY, YOOKASSA_AUTOPAY_CONFIG)
_registered_webhook_path: str | None = None

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
AUTOPAY_SETTING_LIMITS = {
    "DAYS_BEFORE_EXPIRY": (0, 3650),
    "MAX_RETRY_ATTEMPTS": (1, 1000),
    "RETRY_INTERVAL_HOURS": (1, 8760),
    "AUTOPAY_DISCOUNT_PERCENT": (0, 99.99),
    "MIN_PAYMENT_AMOUNT": (0.01, 1000000),
    "TAX_SYSTEM_CODE": (0, 6),
    "VAT_CODE": (1, 12),
}
_AUTOPAY_SETTING_VALUES = {
    "PAYMENT_MODE": {
        "full_prepayment",
        "partial_prepayment",
        "advance",
        "full_payment",
        "partial_payment",
        "credit",
        "credit_payment",
    },
    "PAYMENT_SUBJECT": {
        "commodity",
        "excise",
        "job",
        "service",
        "payment",
        "casino",
        "gambling_bet",
        "gambling_prize",
        "lottery",
        "lottery_prize",
        "intellectual_activity",
        "agent_commission",
        "property_right",
        "non_operating_gain",
        "insurance_premium",
        "sales_tax",
        "resort_fee",
        "another",
        "marked",
        "non_marked",
        "marked_excise",
        "non_marked_excise",
        "fine",
        "tax",
        "lien",
        "cost",
        "agent_withdrawals",
        "pension_insurance_without_payouts",
        "pension_insurance_with_payouts",
        "health_insurance_without_payouts",
        "health_insurance_with_payouts",
        "health_insurance",
    },
    "MANAGE_BUTTON_LOCATION": {"profile", "pay_menu", "both"},
}


def register_runtime_webhook_path(path: str) -> None:
    """Запоминает фактически подключённый адрес уведомлений."""
    global _registered_webhook_path
    _registered_webhook_path = path


def require_runtime_webhook_path() -> None:
    """Запрещает создание счетов после смены неподключённого адреса."""
    path = str(YOOKASSA_AUTOPAY_CONFIG.get("WEBHOOK_PATH") or DEFAULT_YOOKASSA_AUTOPAY_CONFIG["WEBHOOK_PATH"])
    if _registered_webhook_path is not None and path not in {_registered_webhook_path, "/yookassa/webhook"}:
        raise ValueError("Адрес уведомлений автоплатежей изменён; перезапустите бота перед созданием счетов")


def _legacy_module_literals() -> dict[str, Any]:
    """Читает прежние константы автоплатежей без запуска модуля."""
    path = _PROJECT_ROOT / "modules" / "yookassa_autopay" / "settings.py"
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except FileNotFoundError:
        return {}
    except (OSError, SyntaxError, UnicodeError) as exc:
        logger.warning("[Autopay] Не удалось прочитать прежние настройки: {}", exc)
        return {}
    allowed = {*DEFAULT_YOOKASSA_AUTOPAY_CONFIG, "ENABLED"}
    result: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = [target.id for target in node.targets if isinstance(target, ast.Name) and target.id in allowed]
        if not names:
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError, SyntaxError):
            continue
        for name in names:
            result[name] = value
    return result


def _legacy_module_is_enabled() -> bool:
    if _legacy_module_literals().get("ENABLED") is not True:
        return False
    state_path = Path(os.getenv("MODULES_STATE_FILE", "storage/modules_state.json"))
    if not state_path.is_absolute():
        state_path = _PROJECT_ROOT / state_path
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return True
    except (OSError, ValueError, UnicodeError) as exc:
        logger.warning("[Autopay] Не удалось прочитать прежнее состояние модуля: {}", exc)
        return False
    if not isinstance(data, dict) or not isinstance(data.get("disabled", []), list):
        return False
    return "yookassa_autopay" not in {str(name).strip() for name in data.get("disabled", [])}


async def migrate_missing_autopay_flag(session: AsyncSession, stored: dict[str, Any]) -> dict[str, Any]:
    """Восстанавливает флаг ранее использованных автоплатежей."""
    if "YOOKASSA_AUTOPAY" in stored:
        return stored
    from database.yookassa_autopay import has_legacy_autopay_usage

    migrated = dict(stored)
    migrated["YOOKASSA_AUTOPAY"] = bool(_legacy_module_is_enabled() and await has_legacy_autopay_usage(session))
    return migrated


def _normalized_config(values: dict[str, Any]) -> dict[str, Any]:
    result = DEFAULT_YOOKASSA_AUTOPAY_CONFIG.copy()
    for key, default in DEFAULT_YOOKASSA_AUTOPAY_CONFIG.items():
        value = values.get(key, default)
        if isinstance(default, bool):
            if isinstance(value, bool):
                result[key] = value
        elif isinstance(default, int | float):
            if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
                result[key] = float(value) if isinstance(default, float) else int(value)
        elif isinstance(default, str):
            if isinstance(value, str):
                result[key] = value.strip()
            elif key == "SHOP_ID" and isinstance(value, int) and not isinstance(value, bool) and value > 0:
                result[key] = str(value)
        elif key == "QUICK_AMOUNTS" and isinstance(value, list | tuple):
            amounts = [
                int(amount)
                for amount in value
                if isinstance(amount, int | float)
                and not isinstance(amount, bool)
                and math.isfinite(amount)
                and amount >= 1
            ]
            if amounts:
                result[key] = sorted(set(amounts))
    result["MAX_RETRY_ATTEMPTS"] = max(1, result["MAX_RETRY_ATTEMPTS"])
    result["RETRY_INTERVAL_HOURS"] = max(1, result["RETRY_INTERVAL_HOURS"])
    result["API_TIMEOUT"] = max(1, result["API_TIMEOUT"])
    result["AUTOPAY_DISCOUNT_PERCENT"] = min(100, result["AUTOPAY_DISCOUNT_PERCENT"])
    if result["MANAGE_BUTTON_LOCATION"] not in {"profile", "pay_menu", "both"}:
        result["MANAGE_BUTTON_LOCATION"] = "profile"
    if not re.fullmatch(r"/[A-Za-z0-9_/-]+", result["WEBHOOK_PATH"]) or "//" in result["WEBHOOK_PATH"]:
        logger.error("[Autopay] Некорректный WEBHOOK_PATH; используется стандартный адрес уведомлений")
        result["WEBHOOK_PATH"] = DEFAULT_YOOKASSA_AUTOPAY_CONFIG["WEBHOOK_PATH"]
    return result


def is_yookassa_autopay_enabled() -> bool:
    return bool(PAYMENTS_CONFIG.get("YOOKASSA_AUTOPAY", False))


async def is_yookassa_autopay_enabled_for_session(session: AsyncSession) -> bool:
    """Проверяет включение автоплатежей по настройке в базе."""
    config = await get_setting(session, "PAYMENTS_CONFIG", {})
    return bool(isinstance(config, dict) and config.get("YOOKASSA_AUTOPAY", False))


async def load_yookassa_autopay_config(session: AsyncSession) -> None:
    stored = await get_setting(session, YOOKASSA_AUTOPAY_SETTING_KEY)
    if stored is None:
        values = _legacy_module_literals()
    else:
        values = stored if isinstance(stored, dict) else {}
    config = _normalized_config(values)
    await set_setting(session, YOOKASSA_AUTOPAY_SETTING_KEY, config, "Настройки автоплатежей YooKassa")
    YOOKASSA_AUTOPAY_CONFIG.clear()
    YOOKASSA_AUTOPAY_CONFIG.update(config)


def validate_yookassa_autopay_settings(values: dict[str, Any]) -> dict[str, Any]:
    """Проверяет редактируемые в админке параметры автоплатежей."""
    boolean_keys = {
        "NOTIFY_SUCCESS",
        "NOTIFY_FAILED_ATTEMPT",
        "NOTIFY_ALL_ATTEMPTS_FAILED",
        "NOTIFY_SUBSCRIPTION_CANCELLED",
        "SEND_RECEIPT",
    }
    integer_keys = {"DAYS_BEFORE_EXPIRY", "MAX_RETRY_ATTEMPTS", "RETRY_INTERVAL_HOURS", "TAX_SYSTEM_CODE", "VAT_CODE"}
    decimal_keys = {"AUTOPAY_DISCOUNT_PERCENT", "MIN_PAYMENT_AMOUNT"}
    string_keys = {"MANAGE_BUTTON_LOCATION", "PAYMENT_MODE", "PAYMENT_SUBJECT"}
    allowed = boolean_keys | integer_keys | decimal_keys | string_keys | {"QUICK_AMOUNTS"}
    if set(values).difference(allowed):
        raise ValueError(texts.YOOKASSA_AUTOPAY_SETTINGS_INVALID)
    result = {}
    for key, value in values.items():
        if key in boolean_keys:
            if not isinstance(value, bool):
                raise ValueError(texts.YOOKASSA_AUTOPAY_SETTINGS_INVALID)
        elif key in integer_keys:
            minimum, maximum = AUTOPAY_SETTING_LIMITS[key]
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not minimum <= value <= maximum
                or value != int(value)
            ):
                raise ValueError(texts.YOOKASSA_AUTOPAY_SETTINGS_INVALID)
            value = int(value)
        elif key in decimal_keys:
            if isinstance(value, bool) or not isinstance(value, int | float | str):
                raise ValueError(texts.YOOKASSA_AUTOPAY_SETTINGS_INVALID)
            try:
                amount = Decimal(str(value))
                minimum, maximum = (Decimal(str(limit)) for limit in AUTOPAY_SETTING_LIMITS[key])
                valid = amount.is_finite() and (
                    minimum <= amount <= maximum
                    and (key == "AUTOPAY_DISCOUNT_PERCENT" or amount == amount.quantize(Decimal("0.01")))
                )
                numeric = float(amount)
            except (InvalidOperation, ValueError, OverflowError) as exc:
                raise ValueError(texts.YOOKASSA_AUTOPAY_SETTINGS_INVALID) from exc
            if not valid or not math.isfinite(numeric):
                raise ValueError(texts.YOOKASSA_AUTOPAY_SETTINGS_INVALID)
            value = numeric
        elif key == "QUICK_AMOUNTS":
            items = value.split(",") if isinstance(value, str) else value
            if not isinstance(items, list | tuple) or not items:
                raise ValueError(texts.YOOKASSA_AUTOPAY_SETTINGS_INVALID)
            try:
                amounts = [Decimal(str(item).strip()) for item in items if not isinstance(item, bool)]
                if len(amounts) != len(items) or any(
                    not item.is_finite() or not 1 <= item <= 1000000 or item != item.to_integral_value()
                    for item in amounts
                ):
                    raise ValueError(texts.YOOKASSA_AUTOPAY_SETTINGS_INVALID)
                value = sorted({int(item) for item in amounts})
            except (InvalidOperation, ValueError, OverflowError) as exc:
                raise ValueError(texts.YOOKASSA_AUTOPAY_SETTINGS_INVALID) from exc
        elif key in string_keys:
            if not isinstance(value, str):
                raise ValueError(texts.YOOKASSA_AUTOPAY_SETTINGS_INVALID)
            value = value.strip()
            if value not in _AUTOPAY_SETTING_VALUES[key]:
                raise ValueError(texts.YOOKASSA_AUTOPAY_SETTINGS_INVALID)
        result[key] = value
    return result


async def update_yookassa_autopay_config(session: AsyncSession, new_values: dict[str, Any]) -> None:
    stored = await get_setting(session, YOOKASSA_AUTOPAY_SETTING_KEY, {})
    config = _normalized_config({**(stored if isinstance(stored, dict) else {}), **new_values})
    await set_setting(session, YOOKASSA_AUTOPAY_SETTING_KEY, config, "Настройки автоплатежей YooKassa")
    await session.commit()
    YOOKASSA_AUTOPAY_CONFIG.clear()
    YOOKASSA_AUTOPAY_CONFIG.update(config)
    settings_cache.update(YOOKASSA_AUTOPAY_SETTING_KEY, config)
    await publish_runtime_config(YOOKASSA_AUTOPAY_SETTING_KEY, config)

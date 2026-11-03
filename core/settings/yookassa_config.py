import re

from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from database.settings import get_setting, set_setting
from database.settings_cache import settings_cache
from settings import config

from .runtime_sync import publish_runtime_config, register_runtime_config


YOOKASSA_SETTING_KEY = "YOOKASSA_CONFIG"
YOOKASSA_MARKUP_INVALID = "Процент наценки должен быть числом от 0 до 100."
YOOKASSA_PAGE_METHODS = ("sbp", "bank_card", "tinkoff_bank", "yoo_money")
DEFAULT_YOOKASSA_CONFIG = {
    "MARKUP_ENABLED": bool(getattr(config, "YOOKASSA_MARKUP_ENABLED", False)),
    "MARKUP_PERCENT": str(getattr(config, "YOOKASSA_MARKUP_PERCENT", 0)),
    "PAGE_ENABLED": False,
    "PAGE_TEST_USERS": [],
    "PAGE_METHODS": list(YOOKASSA_PAGE_METHODS),
    "PAGE_LINK_TTL_MINUTES": 30,
    "PAGE_PUBLIC_URL": "",
}
YOOKASSA_CONFIG = DEFAULT_YOOKASSA_CONFIG.copy()
register_runtime_config(YOOKASSA_SETTING_KEY, YOOKASSA_CONFIG)


def _page_list(value: object) -> list:
    """Разбирает список из формы настроек."""
    if isinstance(value, str):
        return re.split(r"[,;\s]+", value.strip()) if value.strip() else []
    if isinstance(value, list | tuple):
        return list(value)
    raise ValueError("Укажите значения списком через запятую.")


def _page_integer(value: object, minimum: int, maximum: int, message: str) -> int:
    """Проверяет целое значение настройки."""
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise ValueError(message)
    if isinstance(value, str) and (not value.strip().isascii() or not value.strip().isdigit()):
        raise ValueError(message)
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(message) from exc
    if not minimum <= number <= maximum:
        raise ValueError(message)
    return number


def _page_public_url(value: object) -> str:
    """Проверяет внешний HTTPS-адрес страницы."""
    message = "Укажите HTTPS-адрес без пути, параметров и данных входа, например: https://pay.example.ru."
    if not isinstance(value, str):
        raise ValueError(message)
    address = value.strip()
    if not address:
        return ""
    if any(character.isspace() or ord(character) < 32 for character in address) or any(
        character in address for character in ("\\", "?", "#")
    ):
        raise ValueError(message)
    try:
        parts = urlsplit(address)
        valid = (
            parts.scheme == "https"
            and bool(parts.hostname)
            and not parts.username
            and not parts.password
            and "@" not in parts.netloc
            and parts.path in {"", "/"}
            and not parts.query
            and not parts.fragment
            and (parts.port is None or 1 <= parts.port <= 65535)
            and not parts.netloc.endswith(":")
        )
    except ValueError as exc:
        raise ValueError(message) from exc
    if not valid:
        raise ValueError(message)
    return f"https://{parts.netloc}"


def normalize_yookassa_config(values: dict | None) -> dict:
    """Проверяет наценку и параметры страницы оплаты."""
    if values is not None and not isinstance(values, dict):
        raise ValueError("Некорректные настройки ЮКассы.")
    result = {**DEFAULT_YOOKASSA_CONFIG, **(values or {})}
    try:
        percent = Decimal(str(result["MARKUP_PERCENT"]))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(YOOKASSA_MARKUP_INVALID) from exc
    if not percent.is_finite() or not 0 <= percent <= 100:
        raise ValueError(YOOKASSA_MARKUP_INVALID)
    if not isinstance(result["MARKUP_ENABLED"], bool):
        raise ValueError(YOOKASSA_MARKUP_INVALID)
    if not isinstance(result["PAGE_ENABLED"], bool):
        raise ValueError("Включение своей страницы оплаты должно быть логическим значением.")
    users = list(
        dict.fromkeys(
            _page_integer(value, 1, 2**63 - 1, "Telegram ID должны быть целыми положительными числами.")
            for value in _page_list(result["PAGE_TEST_USERS"])
        )
    )
    methods = _page_list(result["PAGE_METHODS"])
    if (
        not methods
        or any(not isinstance(method, str) or method not in YOOKASSA_PAGE_METHODS for method in methods)
        or len(set(methods)) != len(methods)
    ):
        raise ValueError("Укажите способы без повторов: sbp, bank_card, tinkoff_bank, yoo_money.")
    return {
        "MARKUP_ENABLED": result["MARKUP_ENABLED"],
        "MARKUP_PERCENT": str(percent),
        "PAGE_ENABLED": result["PAGE_ENABLED"],
        "PAGE_TEST_USERS": users,
        "PAGE_METHODS": methods,
        "PAGE_LINK_TTL_MINUTES": _page_integer(
            result["PAGE_LINK_TTL_MINUTES"], 1, 1440, "Срок ссылки должен быть целым числом от 1 до 1440 минут."
        ),
        "PAGE_PUBLIC_URL": _page_public_url(result["PAGE_PUBLIC_URL"]),
    }


async def load_yookassa_config(session: AsyncSession) -> None:
    """Загружает настройки наценки и страницы оплаты."""
    stored = await get_setting(session, YOOKASSA_SETTING_KEY, {})
    values = normalize_yookassa_config(stored)
    await set_setting(session, YOOKASSA_SETTING_KEY, values, "Настройки YooKassa")
    YOOKASSA_CONFIG.clear()
    YOOKASSA_CONFIG.update(values)


async def update_yookassa_config(session: AsyncSession, values: dict) -> None:
    """Сохраняет настройки ЮКассы и обновляет процессы."""
    updated = normalize_yookassa_config({**YOOKASSA_CONFIG, **values})
    await set_setting(session, YOOKASSA_SETTING_KEY, updated, "Настройки YooKassa")
    await session.commit()
    YOOKASSA_CONFIG.clear()
    YOOKASSA_CONFIG.update(updated)
    settings_cache.update(YOOKASSA_SETTING_KEY, updated)
    await publish_runtime_config(YOOKASSA_SETTING_KEY, updated)

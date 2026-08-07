from datetime import datetime, timezone
from typing import Any

from aiogram.fsm.state import State, StatesGroup

from handlers.utils import get_plural_form, render_text
from settings.texts import (
    ADDONS_HINT_BOTH_OPTIONS_TEXT,
    ADDONS_HINT_SINGLE_OPTION_TEXT,
    ADDONS_PACK_HINT_BOTH,
    ADDONS_PACK_HINT_DEVICES,
    ADDONS_PACK_HINT_TRAFFIC,
    ADDONS_TEXT,
    UNLIMITED_DEVICES_LABEL,
    UNLIMITED_ROW_VALUE,
    UNLIMITED_TRAFFIC_LABEL,
)


class KeyAddonConfigState(StatesGroup):
    configuring = State()


def format_devices_label(value, default_text: str = "по умолчанию") -> str:
    if value is None:
        return default_text
    value_int = int(value)
    if value_int <= 0:
        return UNLIMITED_DEVICES_LABEL
    return f"{value_int} {get_plural_form(value_int, 'устройство', 'устройства', 'устройств')}"


def format_traffic_label(value, default_text: str = "по умолчанию") -> str:
    if value is None:
        return default_text
    value_int = int(value)
    if value_int <= 0:
        return UNLIMITED_TRAFFIC_LABEL
    return f"{value_int} ГБ"


def is_not_downgrade(current_value, new_value) -> bool:
    if current_value is None:
        return True
    current_int = int(current_value)
    new_int = int(new_value)
    current_cmp = current_int if current_int > 0 else 10**9
    new_cmp = new_int if new_int > 0 else 10**9
    return new_cmp >= current_cmp


def calc_remaining_ratio_seconds(expiry_time: Any, tariff: dict) -> tuple[int, int]:
    """Секунды до конца подписки и длительность периода."""
    duration_days = int(tariff.get("duration_days") or 0) or 30
    total_seconds = max(1, duration_days * 86400)

    if not expiry_time:
        return total_seconds, total_seconds

    expiry_dt: datetime | None = None

    if isinstance(expiry_time, datetime):
        expiry_dt = expiry_time
    elif isinstance(expiry_time, int | float):
        ts = float(expiry_time)
        if ts > 10_000_000_000:
            ts = ts / 1000.0
        try:
            expiry_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            expiry_dt = None
    elif isinstance(expiry_time, str):
        try:
            expiry_dt = datetime.fromisoformat(expiry_time.replace("Z", "+00:00"))
        except Exception:
            expiry_dt = None

    if expiry_dt is None:
        return total_seconds, total_seconds

    now_utc = datetime.now(timezone.utc)
    if expiry_dt.tzinfo is None:
        expiry_utc = expiry_dt.replace(tzinfo=timezone.utc)
    else:
        expiry_utc = expiry_dt.astimezone(timezone.utc)

    remaining_seconds = int((expiry_utc - now_utc).total_seconds())
    if remaining_seconds <= 0:
        return 0, total_seconds

    if remaining_seconds > total_seconds:
        remaining_seconds = total_seconds

    return remaining_seconds, total_seconds


def limit_row_value(label: str) -> str:
    """Возвращает значение лимита для строки таблицы, без повтора её метки."""
    if label in (UNLIMITED_DEVICES_LABEL, UNLIMITED_TRAFFIC_LABEL):
        return UNLIMITED_ROW_VALUE
    for word in ("устройство", "устройства", "устройств"):
        if label.endswith(f" {word}"):
            return label[: -len(word) - 1]
    return label


def build_addons_screen_text(
    *,
    tariff_name: str,
    current_devices_label: str,
    current_traffic_label: str,
    new_devices_label: str,
    new_traffic_label: str,
    has_device_choice: bool,
    has_traffic_choice: bool,
    total_price_text: str,
    extra_price_text: str,
    downgrade_warning: str | None = None,
) -> str:
    """Собирает экран докупки опций по шаблону из файла текстов."""
    hint = ADDONS_HINT_BOTH_OPTIONS_TEXT if has_device_choice and has_traffic_choice else ADDONS_HINT_SINGLE_OPTION_TEXT
    if downgrade_warning:
        hint = f"{downgrade_warning}\n{hint}"

    return render_text(
        ADDONS_TEXT,
        tariff_name=tariff_name,
        devices_now=limit_row_value(current_devices_label) if has_device_choice else "",
        devices_pack="",
        devices_new=limit_row_value(new_devices_label) if has_device_choice else "",
        traffic_now=limit_row_value(current_traffic_label) if has_traffic_choice else "",
        traffic_pack="",
        traffic_new=limit_row_value(new_traffic_label) if has_traffic_choice else "",
        price_total=total_price_text,
        price_extra=extra_price_text,
        hint=hint,
    )


def build_addons_pack_screen_text(
    *,
    tariff_name: str,
    current_devices_label: str,
    current_traffic_label: str | None,
    selected_devices_label: str | None,
    selected_traffic_label: str | None,
    total_devices_label: str | None,
    total_traffic_label: str | None,
    extra_price_text: str,
    has_device_option: bool,
    has_traffic_option: bool,
) -> str:
    """Собирает экран докупки пакета по шаблону из файла текстов."""
    if has_device_option and has_traffic_option:
        hint = ADDONS_PACK_HINT_BOTH
    elif has_traffic_option:
        hint = ADDONS_PACK_HINT_TRAFFIC
    elif has_device_option:
        hint = ADDONS_PACK_HINT_DEVICES
    else:
        hint = ""

    def pack_value(label: str | None, enabled: bool) -> str:
        return f"+{limit_row_value(label)}" if enabled and label is not None else ""

    return render_text(
        ADDONS_TEXT,
        tariff_name=tariff_name,
        devices_now=limit_row_value(current_devices_label),
        devices_pack=pack_value(selected_devices_label, has_device_option),
        devices_new=limit_row_value(total_devices_label) if has_device_option and total_devices_label else "",
        traffic_now=limit_row_value(current_traffic_label) if current_traffic_label else "",
        traffic_pack=pack_value(selected_traffic_label, has_traffic_option),
        traffic_new=limit_row_value(total_traffic_label) if has_traffic_option and total_traffic_label else "",
        price_total="",
        price_extra=extra_price_text,
        hint=hint,
    )

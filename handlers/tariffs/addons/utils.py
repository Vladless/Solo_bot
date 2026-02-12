from datetime import datetime, timezone
from typing import Any

from aiogram.fsm.state import State, StatesGroup

from handlers.texts import (
    ADDONS_CURRENT_HEADER_TEXT,
    ADDONS_HINT_BOTH_OPTIONS_TEXT,
    ADDONS_HINT_SINGLE_OPTION_TEXT,
    ADDONS_NEW_CHOICE_BOTH_TEMPLATE,
    ADDONS_NEW_CHOICE_DEVICES_TEMPLATE,
    ADDONS_NEW_CHOICE_TRAFFIC_TEMPLATE,
    ADDONS_PACK_CURRENT_DEVICES_TEMPLATE,
    ADDONS_PACK_CURRENT_TRAFFIC_TEMPLATE,
    ADDONS_PACK_EXTRA_PRICE_TEMPLATE,
    ADDONS_PACK_HINT_BOTH,
    ADDONS_PACK_HINT_DEVICES,
    ADDONS_PACK_HINT_TRAFFIC,
    ADDONS_PACK_SELECTED_DEVICES_TEMPLATE,
    ADDONS_PACK_SELECTED_TRAFFIC_TEMPLATE,
    ADDONS_PACK_TITLE_TEMPLATE,
    ADDONS_PACK_TOTAL_DEVICES_TEMPLATE,
    ADDONS_PACK_TOTAL_TRAFFIC_TEMPLATE,
    ADDONS_PRICE_EXTRA_TEMPLATE,
    ADDONS_PRICE_TOTAL_TEMPLATE,
    ADDONS_TITLE_TEMPLATE,
    UNLIMITED_DEVICES_LABEL,
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
    return f"{value_int} устройств"


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
    current_text = f"{current_devices_label}, {current_traffic_label}"

    if has_device_choice and has_traffic_choice:
        new_choice_line = ADDONS_NEW_CHOICE_BOTH_TEMPLATE.format(
            new_devices_label=new_devices_label,
            new_traffic_label=new_traffic_label,
        )
        hint_line = ADDONS_HINT_BOTH_OPTIONS_TEXT
    elif has_device_choice:
        new_choice_line = ADDONS_NEW_CHOICE_DEVICES_TEMPLATE.format(
            new_devices_label=new_devices_label,
        )
        hint_line = ADDONS_HINT_SINGLE_OPTION_TEXT
    elif has_traffic_choice:
        new_choice_line = ADDONS_NEW_CHOICE_TRAFFIC_TEMPLATE.format(
            new_traffic_label=new_traffic_label,
        )
        hint_line = ADDONS_HINT_SINGLE_OPTION_TEXT
    else:
        new_choice_line = ""
        hint_line = ADDONS_HINT_SINGLE_OPTION_TEXT

    text = (
        ADDONS_TITLE_TEMPLATE.format(tariff_name=tariff_name)
        + "\n\n"
        + ADDONS_CURRENT_HEADER_TEXT
        + "\n"
        + f"<blockquote>{current_text}</blockquote>\n"
        + new_choice_line
        + ADDONS_PRICE_TOTAL_TEMPLATE.format(total_price_text=total_price_text)
        + "\n"
        + ADDONS_PRICE_EXTRA_TEMPLATE.format(extra_price_text=extra_price_text)
        + "\n"
    )

    if downgrade_warning:
        text += f"\n{downgrade_warning}\n"

    text += f"\n{hint_line}"
    return text


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
    params_lines: list[str] = []

    params_lines.append(ADDONS_PACK_CURRENT_DEVICES_TEMPLATE.format(value=current_devices_label))

    if current_traffic_label is not None:
        params_lines.append(ADDONS_PACK_CURRENT_TRAFFIC_TEMPLATE.format(value=current_traffic_label))

    if has_device_option and selected_devices_label is not None:
        params_lines.append(ADDONS_PACK_SELECTED_DEVICES_TEMPLATE.format(value=selected_devices_label))

    if has_traffic_option and selected_traffic_label is not None:
        params_lines.append(ADDONS_PACK_SELECTED_TRAFFIC_TEMPLATE.format(value=selected_traffic_label))

    if has_device_option and total_devices_label is not None:
        params_lines.append(ADDONS_PACK_TOTAL_DEVICES_TEMPLATE.format(value=total_devices_label))

    if has_traffic_option and total_traffic_label is not None:
        params_lines.append(ADDONS_PACK_TOTAL_TRAFFIC_TEMPLATE.format(value=total_traffic_label))

    params_block = "<blockquote>" + "\n".join(params_lines) + "</blockquote>"

    text_parts: list[str] = []
    text_parts.append(ADDONS_PACK_TITLE_TEMPLATE.format(tariff_name=tariff_name))
    text_parts.append("")
    text_parts.append(params_block)
    text_parts.append("")
    text_parts.append(ADDONS_PACK_EXTRA_PRICE_TEMPLATE.format(value=extra_price_text))

    if has_device_option or has_traffic_option:
        text_parts.append("")
        if has_device_option and has_traffic_option:
            text_parts.append(ADDONS_PACK_HINT_BOTH)
        elif has_traffic_option:
            text_parts.append(ADDONS_PACK_HINT_TRAFFIC)
        else:
            text_parts.append(ADDONS_PACK_HINT_DEVICES)

    return "\n".join(text_parts)

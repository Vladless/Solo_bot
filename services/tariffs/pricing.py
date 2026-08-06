from __future__ import annotations

from typing import Any

from core.settings.tariffs_config import get_override_value, normalize_tariff_config

from .tariff_display import GB


def _parse_int_options(raw: Any) -> list[int]:
    values: list[int] = []
    if isinstance(raw, list):
        for value in raw:
            try:
                values.append(int(value))
            except (TypeError, ValueError):
                continue
    return values


def resolve_config_base_limits(tariff: dict[str, Any]) -> tuple[int | None, int | None]:
    """Базовые лимиты тарифа: (device_limit, traffic_gb). Это нижняя граница конфигуратора."""
    cfg = normalize_tariff_config(tariff)

    device_values = _parse_int_options(tariff.get("device_options"))
    traffic_values = _parse_int_options(tariff.get("traffic_options_gb"))
    positive_device_values = [v for v in device_values if v > 0]
    positive_traffic_values = [v for v in traffic_values if v > 0]

    base_device_limit = cfg.get("base_device_limit")
    if base_device_limit is None:
        base_device_limit = tariff.get("device_limit")
    if base_device_limit is None:
        if positive_device_values:
            base_device_limit = min(positive_device_values)
        elif device_values:
            base_device_limit = device_values[0]
    base_device_limit = int(base_device_limit) if base_device_limit is not None else None

    base_traffic_gb = cfg.get("base_traffic_gb")
    if base_traffic_gb is None:
        traffic_limit_raw = tariff.get("traffic_limit")
        if traffic_limit_raw:
            traffic_limit_raw = int(traffic_limit_raw)
            if traffic_limit_raw >= GB:
                base_traffic_gb = int(traffic_limit_raw / GB)
            else:
                base_traffic_gb = traffic_limit_raw
        else:
            if positive_traffic_values:
                base_traffic_gb = min(positive_traffic_values)
            elif traffic_values:
                base_traffic_gb = traffic_values[0]
    base_traffic_gb = int(base_traffic_gb) if base_traffic_gb is not None else None

    return base_device_limit, base_traffic_gb


def filter_config_options(tariff: dict[str, Any]) -> tuple[list[int], list[int]]:
    """Опции конфигуратора не ниже базы тарифа; нулевые значения (безлимит) сохраняются."""
    base_device_limit, base_traffic_gb = resolve_config_base_limits(tariff)
    device_values = _parse_int_options(tariff.get("device_options"))
    traffic_values = _parse_int_options(tariff.get("traffic_options_gb"))
    devices = [
        v
        for v in device_values
        if v <= 0 or base_device_limit is None or base_device_limit <= 0 or v >= base_device_limit
    ]
    traffic = [
        v for v in traffic_values if v <= 0 or base_traffic_gb is None or base_traffic_gb <= 0 or v >= base_traffic_gb
    ]
    return devices, traffic


def calculate_config_price(
    tariff: dict[str, Any],
    selected_device_limit: int | None = None,
    selected_traffic_gb: int | None = None,
) -> int:
    """Рассчитывает цену тарифа с учётом выбранных лимитов."""
    cfg = normalize_tariff_config(tariff)

    base_price = int(tariff.get("price_rub") or 0)

    device_values = _parse_int_options(tariff.get("device_options"))
    traffic_values = _parse_int_options(tariff.get("traffic_options_gb"))

    positive_device_values = [v for v in device_values if v > 0]
    positive_traffic_values = [v for v in traffic_values if v > 0]

    base_device_limit, base_traffic_gb = resolve_config_base_limits(tariff)

    device_overrides = cfg.get("device_price_overrides") or tariff.get("device_overrides") or {}
    traffic_overrides = cfg.get("traffic_price_overrides") or tariff.get("traffic_overrides") or {}

    extra_device_step_price = int(cfg.get("extra_device_base_price_rub") or tariff.get("device_step_rub") or 0)
    extra_traffic_step_price = int(
        cfg.get("extra_traffic_base_price_per_gb_rub") or tariff.get("traffic_step_rub") or 0
    )

    devices_extra_price = 0
    traffic_extra_price = 0

    if selected_device_limit is not None and base_device_limit is not None:
        selected_device_limit = int(selected_device_limit)
        device_override = get_override_value(device_overrides, selected_device_limit)
        if device_override is not None:
            devices_extra_price = int(device_override)
        else:
            if selected_device_limit <= 0:
                if positive_device_values:
                    effective_devices = max(positive_device_values)
                    extra_devices = max(0, effective_devices - base_device_limit)
                    devices_extra_price = extra_devices * extra_device_step_price
            else:
                extra_devices = max(0, selected_device_limit - base_device_limit)
                devices_extra_price = extra_devices * extra_device_step_price

    if selected_traffic_gb is not None and base_traffic_gb is not None:
        selected_traffic_gb = int(selected_traffic_gb)
        traffic_override = get_override_value(traffic_overrides, selected_traffic_gb)
        if traffic_override is not None:
            traffic_extra_price = int(traffic_override)
        else:
            if selected_traffic_gb <= 0:
                if positive_traffic_values:
                    effective_gb = max(positive_traffic_values)
                    extra_traffic = max(0, effective_gb - base_traffic_gb)
                    traffic_extra_price = extra_traffic * extra_traffic_step_price
            else:
                extra_traffic = max(0, selected_traffic_gb - base_traffic_gb)
                traffic_extra_price = extra_traffic * extra_traffic_step_price

    return int(base_price + devices_extra_price + traffic_extra_price)

from math import ceil
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Setting


TARIFFS_CONFIG: dict[str, Any] = {
    "ALLOW_DOWNGRADE": True,
    "KEY_ADDONS_PACK_MODE": "all",
    "KEY_ADDONS_PRICE_BASE_MODE": "current",
}


async def load_tariffs_config(session: AsyncSession) -> None:
    """Загружает конфиг тарифов из БД."""
    stmt = select(Setting).where(Setting.key == "TARIFFS_CONFIG")
    result = await session.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        tariffs_config = TARIFFS_CONFIG.copy()
        setting = Setting(
            key="TARIFFS_CONFIG",
            value=tariffs_config,
            description="Конфигурация тарифов",
        )
        session.add(setting)
    else:
        stored = setting.value or {}
        tariffs_config = TARIFFS_CONFIG.copy()
        tariffs_config.update(stored)
        setting.value = tariffs_config

    TARIFFS_CONFIG.clear()
    TARIFFS_CONFIG.update(tariffs_config)
    await session.flush()


async def update_tariffs_config(session: AsyncSession, new_values: dict[str, Any]) -> None:
    """Обновляет конфиг тарифов."""
    stmt = select(Setting).where(Setting.key == "TARIFFS_CONFIG")
    result = await session.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        setting = Setting(
            key="TARIFFS_CONFIG",
            value=new_values,
            description="Конфигурация тарифов",
        )
        session.add(setting)
    else:
        setting.value = new_values

    await session.commit()

    tariffs_config = TARIFFS_CONFIG.copy()
    tariffs_config.update(new_values)

    TARIFFS_CONFIG.clear()
    TARIFFS_CONFIG.update(tariffs_config)


def calc_extra_devices_price(tariff: dict[str, Any], device_limit: int) -> int:
    base_device_limit = int(tariff.get("device_limit") or 1)
    extra_devices = max(0, device_limit - base_device_limit)
    if extra_devices <= 0:
        return 0

    step_price = int(tariff.get("device_step_rub") or 0)
    overrides = tariff.get("device_overrides") or {}

    override_total = overrides.get(str(device_limit))
    if override_total is not None:
        return int(ceil(float(override_total)))

    return int(ceil(extra_devices * step_price))


def calc_extra_traffic_price(tariff: dict[str, Any], traffic_gb: int | None) -> int:
    if traffic_gb is None:
        return 0

    traffic_limit_bytes = tariff.get("traffic_limit")
    if traffic_limit_bytes:
        base_traffic_gb = ceil(traffic_limit_bytes / 1024 / 1024 / 1024)
    else:
        base_traffic_gb = 0

    step_price = int(tariff.get("traffic_step_rub") or 0)
    overrides = tariff.get("traffic_overrides") or {}

    override_total = overrides.get(str(traffic_gb))
    if override_total is not None:
        return int(ceil(float(override_total)))

    if traffic_gb == 0:
        return 0

    extra_gb = max(0, traffic_gb - base_traffic_gb)
    if extra_gb <= 0:
        return 0

    return int(ceil(extra_gb * step_price))


def calculate_config_price(
    tariff: dict[str, Any],
    duration_days: int,
    device_limit: int,
    traffic_gb: int | None,
) -> int:
    base_duration = int(tariff.get("duration_days") or 0) or duration_days or 30
    if base_duration <= 0:
        base_duration = duration_days or 30

    base_price = int(tariff.get("price_rub") or 0)

    duration_multiplier = duration_days / base_duration
    base_price_scaled = base_price * duration_multiplier

    extra_devices_price = calc_extra_devices_price(tariff, device_limit)
    extra_traffic_price = calc_extra_traffic_price(tariff, traffic_gb)

    total = base_price_scaled + extra_devices_price + extra_traffic_price
    return int(ceil(total))


def normalize_tariff_config(tariff: dict[str, Any]) -> dict[str, Any]:
    raw_duration_options = tariff.get("duration_options") or []
    duration_options: list[int] = []
    for value in raw_duration_options:
        try:
            v = int(value)
        except (TypeError, ValueError):
            continue
        if v > 0:
            duration_options.append(v)
    if not duration_options:
        base_duration = int(tariff.get("duration_days") or 0) or 30
        duration_options = [base_duration]
    duration_options = sorted(set(duration_options))

    raw_device_options = tariff.get("device_options") or []
    device_options: list[int] = []
    for value in raw_device_options:
        try:
            v = int(value)
        except (TypeError, ValueError):
            continue
        if v > 0:
            device_options.append(v)
    if not device_options:
        base_device_limit = int(tariff.get("device_limit") or 0)
        if base_device_limit > 0:
            device_options = [base_device_limit]
        else:
            device_options = []
    device_options = sorted(set(device_options))

    raw_traffic_options = tariff.get("traffic_options_gb")
    traffic_options_gb: list[int] | None
    if raw_traffic_options is None:
        traffic_options_gb = None
    else:
        traffic_values: list[int] = []
        has_unlimited = False
        for value in raw_traffic_options:
            try:
                v = int(value)
            except (TypeError, ValueError):
                continue
            if v == 0:
                has_unlimited = True
            elif v > 0:
                traffic_values.append(v)
        if not traffic_values and not has_unlimited:
            traffic_options_gb = None
        else:
            unique_values = sorted(set(traffic_values))
            if has_unlimited:
                traffic_options_gb = [0] + unique_values
            else:
                traffic_options_gb = unique_values

    return {
        "duration_options": duration_options,
        "device_options": device_options,
        "traffic_options_gb": traffic_options_gb,
    }

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.settings.tariffs_config import TARIFFS_CONFIG, normalize_tariff_config
from database import get_tariff_by_id
from database.models import Key
from logger import logger
from settings.texts import key_message_success


GB = 1024 * 1024 * 1024


async def get_effective_limits_for_key(
    session: AsyncSession,
    tariff_id: int | None,
    selected_device_limit: int | None,
    selected_traffic_gb: int | None,
    tariff: dict | None = None,
) -> tuple[int, int]:
    """Возвращает лимиты устройств и трафика с учётом выбранных значений. tariff опционален — если передан, get_tariff_by_id не вызывается."""
    if tariff is None and tariff_id:
        tariff = await get_tariff_by_id(session, int(tariff_id))

    if tariff:
        base_devices = tariff.get("device_limit")
        base_traffic_gb = tariff.get("traffic_limit")
    else:
        base_devices = None
        base_traffic_gb = None

    if selected_device_limit is None:
        device_limit = int(base_devices or 0)
    elif selected_device_limit == 0:
        device_limit = 0
    else:
        device_limit = int(selected_device_limit)

    if selected_traffic_gb is None:
        traffic_limit_bytes = int(base_traffic_gb or 0) * GB
    elif selected_traffic_gb == 0:
        traffic_limit_bytes = 0
    else:
        traffic_limit_bytes = int(selected_traffic_gb) * GB

    return device_limit, traffic_limit_bytes


async def resolve_price_to_charge(session: AsyncSession, state_data: dict[str, Any]) -> int | None:
    """Считает цену к списанию по состоянию, с учётом конфигуратора и наценок."""
    price = state_data.get("selected_price_rub")
    if price is not None:
        try:
            return int(price)
        except (TypeError, ValueError):
            return None

    tariff_id = state_data.get("tariff_id")
    if not tariff_id:
        return None

    tariff = await get_tariff_by_id(session, int(tariff_id))
    if not tariff:
        return None

    try:
        base_price = int(tariff.get("price_rub") or 0)
    except (TypeError, ValueError):
        return None

    if not bool(tariff.get("configurable")):
        return base_price

    cfg = normalize_tariff_config(tariff)

    device_options = cfg.get("device_options") or []
    traffic_options_gb = cfg.get("traffic_options_gb") or []

    try:
        base_device_limit = int(min(device_options)) if device_options else int(tariff.get("device_limit") or 0)
    except (TypeError, ValueError):
        base_device_limit = 0

    try:
        base_traffic_gb = int(min(traffic_options_gb)) if traffic_options_gb else int(tariff.get("traffic_limit") or 0)
    except (TypeError, ValueError):
        base_traffic_gb = 0

    selected_device_limit = state_data.get("selected_device_limit")
    selected_traffic_gb = state_data.get("selected_traffic_limit")

    try:
        device_target = int(selected_device_limit) if selected_device_limit is not None else base_device_limit
    except (TypeError, ValueError):
        device_target = base_device_limit

    try:
        traffic_target_gb = int(selected_traffic_gb) if selected_traffic_gb is not None else base_traffic_gb
    except (TypeError, ValueError):
        traffic_target_gb = base_traffic_gb

    try:
        device_step_rub = int(tariff.get("device_step_rub") or 0)
    except (TypeError, ValueError):
        device_step_rub = 0

    try:
        traffic_step_rub = int(tariff.get("traffic_step_rub") or 0)
    except (TypeError, ValueError):
        traffic_step_rub = 0

    device_overrides = tariff.get("device_overrides") or {}
    traffic_overrides = tariff.get("traffic_overrides") or {}

    device_add_rub = 0
    if device_target > base_device_limit:
        override_value = device_overrides.get(str(device_target), device_overrides.get(device_target))
        if override_value is not None:
            try:
                device_add_rub = int(override_value)
            except (TypeError, ValueError):
                device_add_rub = 0
        else:
            device_add_rub = (device_target - base_device_limit) * device_step_rub

    traffic_add_rub = 0
    if traffic_target_gb > base_traffic_gb:
        override_value = traffic_overrides.get(str(traffic_target_gb), traffic_overrides.get(traffic_target_gb))
        if override_value is not None:
            try:
                traffic_add_rub = int(override_value)
            except (TypeError, ValueError):
                traffic_add_rub = 0
        else:
            traffic_add_rub = (traffic_target_gb - base_traffic_gb) * traffic_step_rub

    return int(base_price + device_add_rub + traffic_add_rub)


async def get_key_tariff_display(
    session: AsyncSession,
    key_record: dict[str, Any],
    selected_device_limit_override: int | None = None,
    selected_traffic_gb_override: int | None = None,
) -> tuple[str, str, int, int, bool]:
    """Возвращает отображение тарифа и эффективные лимиты из БД."""
    tariff_id = key_record.get("tariff_id")
    if not tariff_id:
        return "", "", 0, 0, False, None

    tariff = await get_tariff_by_id(session, int(tariff_id))
    selected_device_limit = selected_device_limit_override
    selected_traffic_gb = selected_traffic_gb_override

    if selected_device_limit is None:
        value = key_record.get("selected_device_limit")
        if value is not None:
            try:
                selected_device_limit = int(value)
            except (TypeError, ValueError):
                selected_device_limit = None

    if selected_traffic_gb is None:
        value = key_record.get("selected_traffic_limit")
        if value is not None:
            try:
                selected_traffic_gb = int(value)
            except (TypeError, ValueError):
                selected_traffic_gb = None

    device_limit, traffic_limit_bytes = await get_effective_limits_for_key(
        session=session,
        tariff_id=int(tariff_id),
        selected_device_limit=selected_device_limit,
        selected_traffic_gb=selected_traffic_gb,
        tariff=tariff,
    )

    traffic_limit_gb = int(traffic_limit_bytes / GB) if traffic_limit_bytes else 0

    if tariff:
        tariff_name = tariff.get("name", "—")
        subgroup_title = tariff.get("subgroup_title") or ""
        vless_enabled = bool(tariff.get("vless"))
    else:
        tariff_name = "—"
        subgroup_title = ""
        vless_enabled = False

    return tariff_name, subgroup_title, traffic_limit_gb, device_limit, vless_enabled, tariff


async def get_key_tariff_addons_state(
    session: AsyncSession,
    key_record: dict[str, Any],
    db_key: Key | None,
) -> tuple[str, str, int, int, bool, bool, bool, bool]:
    """Возвращает параметры тарифа и допы для ключа."""
    tariff_id = key_record.get("tariff_id")
    if not tariff_id:
        return "", "", 0, 0, False, False, False, False

    selected_device_limit_override: int | None = None
    selected_traffic_gb_override: int | None = None

    if db_key:
        if db_key.selected_device_limit is not None:
            try:
                selected_device_limit_override = int(db_key.selected_device_limit)
            except (TypeError, ValueError):
                selected_device_limit_override = None
        if db_key.selected_traffic_limit is not None:
            try:
                selected_traffic_gb_override = int(db_key.selected_traffic_limit)
            except (TypeError, ValueError):
                selected_traffic_gb_override = None

    (
        tariff_name,
        subgroup_title,
        traffic_limit_gb,
        device_limit,
        vless_enabled,
        tariff,
    ) = await get_key_tariff_display(
        session=session,
        key_record=key_record,
        selected_device_limit_override=selected_device_limit_override,
        selected_traffic_gb_override=selected_traffic_gb_override,
    )

    unlimited_devices = device_limit == 0
    unlimited_traffic = traffic_limit_gb == 0

    if unlimited_devices or unlimited_traffic:
        suffix_parts: list[str] = []
        if unlimited_traffic:
            suffix_parts.append("безлимит трафика")
        if unlimited_devices:
            suffix_parts.append("безлимит устройств")
        tariff_name = f"{tariff_name} ({', '.join(suffix_parts)})"

    is_tariff_configurable = False
    addons_devices_enabled = False
    addons_traffic_enabled = False

    if tariff and tariff.get("configurable"):
        is_tariff_configurable = True

        cfg = normalize_tariff_config(tariff)
        device_options = cfg.get("device_options") or []
        traffic_options = cfg.get("traffic_options_gb") or []

        addons_devices_enabled = bool(device_options)
        addons_traffic_enabled = bool(traffic_options)

        mode = TARIFFS_CONFIG.get("KEY_ADDONS_PACK_MODE") or ""
        if not mode:
            pass
        elif mode == "traffic":
            addons_devices_enabled = False
        elif mode == "devices":
            addons_traffic_enabled = False
        elif mode == "all":
            pass
        else:
            logger.warning(f"Некорректный KEY_ADDONS_PACK_MODE: {mode!r}")

        if unlimited_devices:
            addons_devices_enabled = False
        if unlimited_traffic:
            addons_traffic_enabled = False

    return (
        tariff_name,
        subgroup_title,
        traffic_limit_gb,
        device_limit,
        vless_enabled,
        is_tariff_configurable,
        addons_devices_enabled,
        addons_traffic_enabled,
    )


async def build_key_created_message(
    session: AsyncSession,
    key_record: dict[str, Any],
    final_link: str,
    selected_device_limit: int | None = None,
    selected_traffic_gb: int | None = None,
) -> str:
    """Собирает сообщение об успешном создании ключа с отображением выбранных лимитов."""
    tariff_id = key_record.get("tariff_id")
    tariff = await get_tariff_by_id(session, int(tariff_id)) if tariff_id else None

    if tariff:
        tariff_name = tariff.get("name", "—")
        subgroup_title = tariff.get("subgroup_title") or ""
    else:
        tariff_name = "—"
        subgroup_title = ""

    selected_device_limit_effective = selected_device_limit
    if selected_device_limit_effective is None:
        value = key_record.get("selected_device_limit")
        if value is not None:
            try:
                selected_device_limit_effective = int(value)
            except (TypeError, ValueError):
                selected_device_limit_effective = None

    selected_traffic_gb_effective = selected_traffic_gb
    if selected_traffic_gb_effective is None:
        value = key_record.get("selected_traffic_limit")
        if value is not None:
            try:
                selected_traffic_gb_effective = int(value)
            except (TypeError, ValueError):
                selected_traffic_gb_effective = None

    device_limit, traffic_limit_bytes = await get_effective_limits_for_key(
        session=session,
        tariff_id=int(tariff_id) if tariff_id else None,
        selected_device_limit=selected_device_limit_effective,
        selected_traffic_gb=selected_traffic_gb_effective,
    )

    traffic_to_show = int(traffic_limit_bytes / GB) if traffic_limit_bytes else 0
    devices_to_show = int(device_limit) if device_limit else 0

    return key_message_success(
        final_link or "Ссылка не найдена",
        tariff_name=tariff_name,
        traffic_limit=traffic_to_show,
        device_limit=devices_to_show,
        subgroup_title=subgroup_title,
    )

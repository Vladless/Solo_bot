from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from config import REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD
from core.settings.tariffs_config import TARIFFS_CONFIG, normalize_tariff_config
from database import get_servers, get_tariff_by_id
from database.models import Key
from handlers.texts import key_message_success
from logger import logger


GB = 1024 * 1024 * 1024


async def get_effective_limits_for_key(
    session: AsyncSession,
    tariff_id: int | None,
    selected_device_limit: int | None,
    selected_traffic_gb: int | None,
) -> tuple[int, int]:
    """Возвращает лимиты устройств и трафика с учётом выбранных значений."""
    tariff = await get_tariff_by_id(session, int(tariff_id)) if tariff_id else None

    if tariff:
        base_devices = tariff.get("device_limit")
        base_traffic_bytes = tariff.get("traffic_limit")
    else:
        base_devices = None
        base_traffic_bytes = None

    if selected_device_limit is None:
        device_limit = int(base_devices or 0)
    elif selected_device_limit == 0:
        device_limit = 0
    else:
        device_limit = int(selected_device_limit)

    if selected_traffic_gb is None:
        traffic_limit_bytes = int(base_traffic_bytes or 0) * GB
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
        device_step_rub = int(cfg.get("device_step_rub") or 0)
    except (TypeError, ValueError):
        device_step_rub = 0

    try:
        traffic_step_rub = int(cfg.get("traffic_step_rub") or 0)
    except (TypeError, ValueError):
        traffic_step_rub = 0

    device_overrides = cfg.get("device_overrides") or {}
    traffic_overrides = cfg.get("traffic_overrides") or {}

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


async def resolve_vless_enabled(session: AsyncSession, tariff_id: int | None) -> bool:
    """Проверяет, включён ли VLESS в тарифе."""
    if not tariff_id:
        return False

    tariff = await get_tariff_by_id(session, int(tariff_id))
    if not tariff:
        return False

    return bool(tariff.get("vless"))


async def get_key_tariff_display(
    session: AsyncSession,
    key_record: dict[str, Any],
    selected_device_limit_override: int | None = None,
    selected_traffic_gb_override: int | None = None,
) -> tuple[str, str, int, int, bool]:
    """Возвращает отображение тарифа и эффективные лимиты, приоритет — данные панели."""
    tariff_id = key_record.get("tariff_id")
    if not tariff_id:
        return "", "", 0, 0, False

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
    )

    server_cluster_id = key_record.get("server_id")
    client_id = key_record.get("client_id")

    if server_cluster_id and client_id:
        try:
            servers = await get_servers(session)
            cluster_servers = servers.get(server_cluster_id) or servers.get(str(server_cluster_id)) or []
            remna_server = next((srv for srv in cluster_servers if srv.get("panel_type") == "remnawave"), None)
            if not remna_server:
                remna_server = next(
                    (srv for cl in servers.values() for srv in cl if srv.get("panel_type") == "remnawave"),
                    None,
                )

            if remna_server:
                from panels.remnawave import RemnawaveAPI

                api = RemnawaveAPI(remna_server["api_url"])
                try:
                    ok = await api.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD)
                except Exception as e:
                    logger.warning(f"[KeyTariffDisplay] Remnawave login error for {client_id}: {e}")
                    ok = False

                if ok:
                    try:
                        user_data = await api.get_user_by_uuid(client_id)
                    except Exception as e:
                        logger.warning(f"[KeyTariffDisplay] Remnawave get_user_by_uuid error for {client_id}: {e}")
                        user_data = None

                    if user_data:
                        panel_traffic_limit_bytes = user_data.get("trafficLimitBytes")
                        panel_device_limit = user_data.get("hwidDeviceLimit")

                        if panel_traffic_limit_bytes is not None:
                            try:
                                traffic_limit_bytes = int(panel_traffic_limit_bytes)
                            except (TypeError, ValueError):
                                logger.warning(
                                    f"[KeyTariffDisplay] Invalid trafficLimitBytes from Remnawave for {client_id}: {panel_traffic_limit_bytes}"
                                )

                        if panel_device_limit is not None:
                            try:
                                device_limit = int(panel_device_limit)
                            except (TypeError, ValueError):
                                logger.warning(
                                    f"[KeyTariffDisplay] Invalid hwidDeviceLimit from Remnawave for {client_id}: {panel_device_limit}"
                                )

                try:
                    await api.aclose()
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"[KeyTariffDisplay] Error while overriding limits from panel: {e}")

    traffic_limit_gb = int(traffic_limit_bytes / GB) if traffic_limit_bytes else 0

    tariff = await get_tariff_by_id(session, int(tariff_id))
    if tariff:
        tariff_name = tariff.get("name", "—")
        subgroup_title = tariff.get("subgroup_title") or ""
        vless_enabled = bool(tariff.get("vless"))
    else:
        tariff_name = "—"
        subgroup_title = ""
        vless_enabled = False

    return tariff_name, subgroup_title, traffic_limit_gb, device_limit, vless_enabled


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

    tariff = await get_tariff_by_id(session, int(tariff_id))
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
        base_traffic = tariff.get("traffic_limit")
        base_devices = tariff.get("device_limit")
    else:
        tariff_name = "—"
        subgroup_title = ""
        base_traffic = None
        base_devices = None

    if selected_traffic_gb is not None:
        try:
            traffic_to_show = int(selected_traffic_gb)
        except (TypeError, ValueError):
            traffic_to_show = 0
    else:
        selected_traffic_limit = key_record.get("selected_traffic_limit")
        if selected_traffic_limit is not None:
            try:
                traffic_to_show = int(selected_traffic_limit)
            except (TypeError, ValueError):
                traffic_to_show = 0
        elif base_traffic is not None:
            try:
                traffic_to_show = int(base_traffic)
            except (TypeError, ValueError):
                traffic_to_show = 0
        else:
            traffic_to_show = 0

    if selected_device_limit is not None:
        try:
            devices_to_show = int(selected_device_limit)
        except (TypeError, ValueError):
            devices_to_show = 0
    else:
        selected_device_limit_db = key_record.get("selected_device_limit")
        if selected_device_limit_db is not None:
            try:
                devices_to_show = int(selected_device_limit_db)
            except (TypeError, ValueError):
                devices_to_show = 0
        else:
            try:
                devices_to_show = int(base_devices) if base_devices is not None else 0
            except (TypeError, ValueError):
                devices_to_show = 0

    return key_message_success(
        final_link or "Ссылка не найдена",
        tariff_name=tariff_name,
        traffic_limit=traffic_to_show,
        device_limit=devices_to_show,
        subgroup_title=subgroup_title,
    )

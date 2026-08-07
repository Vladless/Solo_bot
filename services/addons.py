from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import TYPE_CHECKING, Any

from core.bootstrap import TARIFFS_CONFIG
from core.settings.tariffs_config import get_override_value, normalize_tariff_config
from database import get_balance, get_key_details, get_tariff_by_id, save_key_config_with_mode
from database.coupons import mark_coupon_used
from database.users import update_balance
from logger import logger

from .errors import InsufficientFundsError, NotFoundError, ValidationError
from .keys import resolve_cluster_name


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def get_pack_flags() -> tuple[bool, bool, str]:
    """Определяет доступные опции pack mode из конфига."""
    mode = TARIFFS_CONFIG.get("KEY_ADDONS_PACK_MODE") or ""
    if not mode:
        return False, False, ""
    if mode == "traffic":
        return False, True, mode
    if mode == "devices":
        return True, False, mode
    if mode == "all":
        return True, True, mode

    logger.warning(f"Некорректный KEY_ADDONS_PACK_MODE: {mode!r}")
    return False, False, mode


def calc_pack_devices_price_rub(tariff: dict[str, Any], pack_devices: int | None) -> int:
    if pack_devices is None:
        return 0
    pack_devices = int(pack_devices)
    overrides = tariff.get("device_overrides") or {}
    if pack_devices == 0:
        override = get_override_value(overrides, 0)
        return int(ceil(float(override))) if override is not None else 0
    if pack_devices < 0:
        return 0
    override = get_override_value(overrides, pack_devices)
    if override is not None:
        return int(ceil(float(override)))
    step_price = int(tariff.get("device_step_rub") or 0)
    return int(ceil(pack_devices * step_price))


def calc_pack_traffic_price_rub(tariff: dict[str, Any], pack_traffic_gb: int | None) -> int:
    if pack_traffic_gb is None:
        return 0
    pack_traffic_gb = int(pack_traffic_gb)
    overrides = tariff.get("traffic_overrides") or {}
    if pack_traffic_gb == 0:
        override = get_override_value(overrides, 0)
        return int(ceil(float(override))) if override is not None else 0
    if pack_traffic_gb < 0:
        return 0
    override = get_override_value(overrides, pack_traffic_gb)
    if override is not None:
        return int(ceil(float(override)))
    step_price = int(tariff.get("traffic_step_rub") or 0)
    return int(ceil(pack_traffic_gb * step_price))


def calc_pack_full_price_rub(
    tariff: dict[str, Any],
    has_device_option: bool,
    has_traffic_option: bool,
    selected_devices: int | None,
    selected_traffic_gb: int | None,
) -> int:
    total = 0
    if has_device_option:
        total += calc_pack_devices_price_rub(tariff, selected_devices)
    if has_traffic_option:
        total += calc_pack_traffic_price_rub(tariff, selected_traffic_gb)
    return int(total)


@dataclass
class CarriedAddons:
    """Докупленные сверх базы тарифа опции, переносимые на следующий срок."""

    device_limit: int | None = None
    traffic_gb: int | None = None
    price_rub: int = 0

    @property
    def has_any(self) -> bool:
        return self.device_limit is not None or self.traffic_gb is not None


def is_addons_carry_enabled() -> bool:
    """Включён ли перенос докупленных опций при продлении."""
    if not bool(TARIFFS_CONFIG.get("KEY_ADDONS_CARRY_ON_RENEWAL", False)):
        return False
    has_device, has_traffic, _ = get_pack_flags()
    return has_device or has_traffic


def resolve_carried_addons(
    tariff: dict[str, Any],
    key_details: dict[str, Any],
    new_base_devices: int | None,
    new_base_traffic: int | None,
) -> CarriedAddons:
    """Опции, докупленные сверх базы тарифа, и их цена на новый срок.

    Пусто, если перенос выключен, клиент ничего не докупал, тариф меняется или
    база тарифа меняется этим продлением — тогда докупки не переносятся.
    """
    if not tariff or not key_details or not is_addons_carry_enabled():
        return CarriedAddons()

    current_tariff_id = key_details.get("tariff_id")
    if current_tariff_id is None or int(current_tariff_id) != int(tariff.get("id") or 0):
        return CarriedAddons()

    has_device, has_traffic, _ = get_pack_flags()
    base_devices = key_details.get("selected_device_limit")
    base_traffic = key_details.get("selected_traffic_limit")
    if new_base_devices != base_devices or new_base_traffic != base_traffic:
        return CarriedAddons()

    current_devices = key_details.get("current_device_limit")
    current_traffic = key_details.get("current_traffic_limit")

    carried_devices = current_devices if has_device and current_devices != base_devices else None
    carried_traffic = current_traffic if has_traffic and current_traffic != base_traffic else None
    if carried_devices is None and carried_traffic is None:
        return CarriedAddons()

    return CarriedAddons(
        device_limit=carried_devices,
        traffic_gb=carried_traffic,
        price_rub=calc_carried_addons_price_rub(
            tariff, base_devices, base_traffic, current_devices, current_traffic
        ),
    )


def calc_carried_addons_price_rub(
    tariff: dict[str, Any],
    base_devices: int | None,
    base_traffic: int | None,
    current_devices: int | None,
    current_traffic: int | None,
) -> int:
    """Цена докупленных сверх базы тарифа опций на новый срок."""
    has_device, has_traffic, _ = get_pack_flags()
    effective_devices = current_devices if has_device and current_devices is not None else base_devices
    effective_traffic = current_traffic if has_traffic and current_traffic is not None else base_traffic
    price = calc_pack_full_price_rub(
        tariff, has_device, has_traffic, effective_devices, effective_traffic
    ) - calc_pack_full_price_rub(tariff, has_device, has_traffic, base_devices, base_traffic)
    return max(0, int(price))


@dataclass
class AddonsPreviewResult:
    """Результат предпросмотра аддонов."""

    total_price_rub: int
    extra_price_rub: int
    balance: float
    required_amount: int
    payment_required: bool
    current_device_limit: int | None
    current_traffic_gb: int | None
    selected_device_limit: int | None
    selected_traffic_gb: int | None
    has_device_option: bool
    has_traffic_option: bool


@dataclass
class AddonsApplyResult:
    """Результат применения аддонов."""

    ok: bool
    client_id: str
    tariff_id: int
    total_price_rub: int
    extra_price_rub: int
    charged_rub: int
    balance_rub: float


async def preview_addons(
    session: AsyncSession,
    billing_user_id: int,
    client_id: str,
    key_email: str,
    tariff_id: int,
    selected_device_limit: int | None = None,
    selected_traffic_gb: int | None = None,
) -> AddonsPreviewResult:
    """Считает стоимость аддонов без применения.

    Raises: NotFoundError, ValidationError
    """
    tariff = await get_tariff_by_id(session, tariff_id)
    if not tariff:
        raise NotFoundError("Тариф не найден")

    normalize_tariff_config(tariff)
    has_device, has_traffic, _ = get_pack_flags()

    key_details = await get_key_details(session, key_email)
    if not key_details:
        raise NotFoundError("Подписка не найдена")

    current_device = key_details.get("current_device_limit")
    current_traffic = key_details.get("current_traffic_limit")

    total_price = calc_pack_full_price_rub(
        tariff,
        has_device,
        has_traffic,
        selected_device_limit,
        selected_traffic_gb,
    )

    current_price = calc_pack_full_price_rub(
        tariff,
        has_device,
        has_traffic,
        current_device,
        current_traffic,
    )
    extra_price = max(0, total_price - current_price)

    balance = float(await get_balance(session, billing_user_id))
    required = max(0, int(ceil(float(extra_price) - balance)))

    return AddonsPreviewResult(
        total_price_rub=total_price,
        extra_price_rub=extra_price,
        balance=balance,
        required_amount=required,
        payment_required=required > 0,
        current_device_limit=current_device,
        current_traffic_gb=current_traffic,
        selected_device_limit=selected_device_limit,
        selected_traffic_gb=selected_traffic_gb,
        has_device_option=has_device,
        has_traffic_option=has_traffic,
    )


async def apply_addons(
    session: AsyncSession,
    billing_user_id: int,
    client_id: str,
    key_email: str,
    key_server_id: str,
    tariff_id: int,
    extra_price_rub: int,
    selected_device_limit: int | None = None,
    selected_traffic_gb: int | None = None,
    coupon_id: int | None = None,
) -> AddonsApplyResult:
    """Применяет аддоны к ключу: обновляет лимиты на кластере и в БД.

    Raises: NotFoundError, InsufficientFundsError
    """
    tariff = await get_tariff_by_id(session, tariff_id)
    if not tariff:
        raise NotFoundError("Тариф не найден")

    key_details = await get_key_details(session, key_email)
    if not key_details:
        raise NotFoundError("Подписка не найдена")

    from services.operations import renew_key_in_cluster
    from services.tariffs.tariff_display import GB, get_effective_limits_for_key

    dev_eff, trf_bytes = await get_effective_limits_for_key(
        session=session,
        tariff_id=tariff_id,
        selected_device_limit=selected_device_limit,
        selected_traffic_gb=selected_traffic_gb if selected_traffic_gb is not None else 0,
    )
    total_gb = int(trf_bytes / GB) if trf_bytes else 0

    cluster_id = await resolve_cluster_name(session, key_server_id)
    if not cluster_id:
        raise NotFoundError(f"Кластер для {key_server_id} не найден")

    expiry_ms = int(key_details.get("expiry_time") or 0)

    await renew_key_in_cluster(
        cluster_id=cluster_id,
        email=key_email,
        client_id=client_id,
        new_expiry_time=expiry_ms,
        total_gb=total_gb,
        session=session,
        hwid_device_limit=dev_eff,
        reset_traffic=False,
        target_subgroup=tariff.get("subgroup_title"),
        old_subgroup=tariff.get("subgroup_title"),
        plan=tariff_id,
    )

    if extra_price_rub > 0:
        debited = await update_balance(session, billing_user_id, -extra_price_rub)
        if debited is None:
            raise InsufficientFundsError("Недостаточно средств на балансе")
        try:
            from database.subscription_events import record_subscription_event

            await record_subscription_event(
                session,
                event_type="addons",
                user_id=billing_user_id,
                client_id=client_id,
                tariff_id=tariff_id,
                price_rub=float(extra_price_rub),
                expiry_time=expiry_ms,
                source="bot",
            )
        except Exception:
            pass

    normalize_tariff_config(tariff)
    has_device, has_traffic, _ = get_pack_flags()
    total_price = calc_pack_full_price_rub(
        tariff,
        has_device,
        has_traffic,
        selected_device_limit,
        selected_traffic_gb,
    )

    await save_key_config_with_mode(
        session=session,
        email=key_email,
        selected_devices=selected_device_limit,
        selected_traffic_gb=selected_traffic_gb,
        total_price=total_price,
        has_device_choice=has_device,
        has_traffic_choice=has_traffic,
        config_mode="addons",
    )

    if coupon_id is not None:
        await mark_coupon_used(session, coupon_id, billing_user_id)

    new_balance = float(await get_balance(session, billing_user_id))

    return AddonsApplyResult(
        ok=True,
        client_id=client_id,
        tariff_id=tariff_id,
        total_price_rub=total_price,
        extra_price_rub=extra_price_rub,
        charged_rub=extra_price_rub,
        balance_rub=new_balance,
    )

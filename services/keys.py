from __future__ import annotations

import uuid

from dataclasses import dataclass, field
from datetime import datetime
from math import ceil
from typing import TYPE_CHECKING, Any

from core.settings.tariffs_config import normalize_tariff_config
from database import (
    get_balance,
    get_key_details,
    get_tariff_by_id,
    reset_key_current_limits_to_selected,
    save_key_config_with_mode,
    update_balance,
    update_key_expiry,
    update_trial,
)
from database.access.resolution import resolve_user_optional
from database.coupons import mark_coupon_used
from database.keys import (
    update_key_post_creation_snapshot,
    update_key_renewal_snapshot,
)
from database.servers import cluster_name_exists, get_cluster_name_for_server_name
from database.users import get_trial
from logger import logger

from .errors import InsufficientFundsError, NotFoundError, ServiceError, ValidationError


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class RenewalPricing:
    """Расчёт цены продления (без фактического продления)."""

    base_price_rub: int
    discount_rub: int
    final_price_rub: int
    coupon_id: int | None
    applied_coupon_code: str | None
    total_gb: int
    balance: float
    required_amount: int
    payment_required: bool
    duration_days: int
    selected_device_limit: int | None
    selected_traffic_limit: int | None
    carried_addons_rub: int = 0
    carried_device_limit: int | None = None
    carried_traffic_limit: int | None = None


@dataclass
class RenewalQuote:
    """Расчет продления/смены тарифа.

    Смена тарифа, остаток → баланс: net_cost_rub = new_full_price_rub − credit_rub
    (>0 списать, <0 вернуть на баланс).
    Смена тарифа, остаток → дни (RENEWAL_CREDIT_AS_DAYS): credit_rub=0,
    net_cost_rub=полная цена, бонус-дни new_expiry_ms (credit_days).
    Смена тарифа с сохранением срока (RENEWAL_SWITCH_KEEP_PERIOD, приоритетнее
    режима дней): expiry не меняется, net_cost_rub = цена нового за остаток
    периода − credit_rub, keeps_period=True. На уход с триала не действует:
    там сохранять нечего, клиент получает полный период нового тарифа.
    """

    is_switch: bool
    duration_days: int
    selected_device_limit: int | None
    selected_traffic_limit: int | None
    total_gb: int
    new_full_price_rub: int
    credit_rub: int
    net_cost_rub: int
    refund_to_balance_rub: int
    new_expiry_ms: int
    coupon_id: int | None = None
    applied_coupon_code: str | None = None
    credit_days: int = 0
    credit_value_rub: int = 0
    keeps_period: bool = False
    carried_addons_rub: int = 0
    carried_device_limit: int | None = None
    carried_traffic_limit: int | None = None


@dataclass
class RenewalResult:
    """Результат фактического продления."""

    ok: bool
    client_id: str
    tariff_id: int
    charged_rub: int
    balance_rub: float
    new_expiry_time: int
    base_price_rub: int = 0
    discount_rub: int = 0
    final_price_rub: int = 0
    applied_coupon_code: str | None = None


async def resolve_cluster_name(session: AsyncSession, server_or_cluster: str) -> str | None:
    """Определяет имя кластера: если имя — уже кластер, возвращаем его;
    иначе ищем сервер с таким именем и отдаём его ``cluster_name``."""
    if await cluster_name_exists(session, server_or_cluster):
        return server_or_cluster
    return await get_cluster_name_for_server_name(session, server_or_cluster)


def normalize_expiry_ms(raw_value: int | float | None) -> int:
    """Нормализует таймстамп истечения в миллисекунды.

    Единая реализация — обрабатывает секунды, миллисекунды и микросекунды.
    """
    if not raw_value:
        return 0
    value = int(raw_value)
    if value > 10**13:
        value //= 1000
    elif value < 10**10:
        value *= 1000
    return value


def _resolve_effective_limits(
    tariff: dict[str, Any],
    selected_device_limit: int | None,
    selected_traffic_limit: int | None,
) -> tuple[int | None, int | None]:
    """Определяет финальные device/traffic лимиты по тарифу и выбору пользователя."""
    new_tariff_device = tariff.get("device_limit")
    new_tariff_traffic = tariff.get("traffic_limit")

    if new_tariff_device is None:
        final_device = None
    elif selected_device_limit is not None:
        final_device = int(selected_device_limit)
    else:
        final_device = new_tariff_device

    if new_tariff_traffic is None:
        final_traffic = None
    elif selected_traffic_limit is not None:
        final_traffic = int(selected_traffic_limit)
    else:
        final_traffic = int(new_tariff_traffic)

    return final_device, final_traffic


async def calculate_renewal_pricing(
    session: AsyncSession,
    billing_user_id: int,
    key_email: str,
    tariff_id: int,
    coupon_code: str | None = None,
    selected_device_limit: int | None = None,
    selected_traffic_limit: int | None = None,
    credit_rub: float = 0.0,
) -> RenewalPricing:
    """Считает цену продления без фактического выполнения.

    Если тариф configurable и переданы selected_device_limit / selected_traffic_limit —
    они используются как явный выбор и валидируются по device_options / traffic_options_gb.
    Иначе берутся текущие значения из подписки.

    Raises: NotFoundError, ValidationError
    """
    from services.coupons import resolve_percent_coupon

    tariff = await get_tariff_by_id(session, int(tariff_id))
    if not tariff or not tariff.get("is_active", True):
        raise NotFoundError("Тариф не найден")

    duration_days = int(tariff.get("duration_days") or 0)
    if duration_days <= 0:
        raise ValidationError("Некорректная длительность тарифа")

    key_details = await get_key_details(session, key_email)
    if not key_details:
        raise NotFoundError("Подписка не найдена")

    is_configurable = bool(tariff.get("configurable"))
    current_tariff_id = key_details.get("tariff_id")
    same_tariff = current_tariff_id is not None and int(current_tariff_id) == int(tariff_id)

    if is_configurable and (selected_device_limit is not None or selected_traffic_limit is not None):
        device_options_raw = tariff.get("device_options") or []
        traffic_options_raw = tariff.get("traffic_options_gb") or []
        device_options = {int(v) for v in device_options_raw if str(v).lstrip("-").isdigit()}
        traffic_options = {int(v) for v in traffic_options_raw if str(v).lstrip("-").isdigit()}

        if selected_device_limit is not None:
            if device_options and int(selected_device_limit) not in device_options:
                raise ValidationError(f"Недопустимое количество устройств. Доступно: {sorted(device_options)}")
            sel_dev_int = int(selected_device_limit)
        else:
            existing = key_details.get("selected_device_limit") if same_tariff else None
            sel_dev_int = int(existing) if existing is not None else None

        if selected_traffic_limit is not None:
            if traffic_options and int(selected_traffic_limit) not in traffic_options:
                raise ValidationError(f"Недопустимый объём трафика. Доступно (ГБ): {sorted(traffic_options)}")
            sel_trf_int = int(selected_traffic_limit)
        else:
            existing = key_details.get("selected_traffic_limit") if same_tariff else None
            sel_trf_int = int(existing) if existing is not None else None
    elif same_tariff:
        selected_device = key_details.get("selected_device_limit")
        selected_traffic = key_details.get("selected_traffic_limit")
        sel_dev_int = int(selected_device) if selected_device is not None else None
        sel_trf_int = int(selected_traffic) if selected_traffic is not None else None
    else:
        sel_dev_int = None
        sel_trf_int = None

    from services.tariffs import calculate_config_price as calc_price_buy

    total_price_rub = int(
        calc_price_buy(
            tariff=tariff,
            selected_device_limit=sel_dev_int,
            selected_traffic_gb=sel_trf_int,
        )
    )
    if total_price_rub <= 0:
        raise ValidationError("Некорректная стоимость продления")

    from services.addons import resolve_carried_addons

    carried = resolve_carried_addons(tariff, key_details, sel_dev_int, sel_trf_int)
    total_price_rub += carried.price_rub

    final_price_rub, discount_rub, coupon_id, applied_code = await resolve_percent_coupon(
        session=session,
        billing_user_id=billing_user_id,
        base_price_rub=total_price_rub,
        coupon_code=coupon_code,
    )

    if credit_rub and credit_rub > 0:
        final_price_rub = int(max(0, round(float(final_price_rub) - float(credit_rub))))

    from services.tariffs.tariff_display import GB, get_effective_limits_for_key

    _, traffic_bytes = await get_effective_limits_for_key(
        session=session,
        tariff_id=int(tariff_id),
        selected_device_limit=sel_dev_int,
        selected_traffic_gb=sel_trf_int,
    )
    total_gb = int(traffic_bytes / GB) if traffic_bytes else 0

    balance = float(await get_balance(session, billing_user_id))
    required = int(max(0, ceil(float(final_price_rub) - balance)))

    return RenewalPricing(
        base_price_rub=total_price_rub,
        discount_rub=discount_rub,
        final_price_rub=final_price_rub,
        coupon_id=coupon_id,
        applied_coupon_code=applied_code,
        total_gb=total_gb,
        balance=balance,
        required_amount=required,
        payment_required=required > 0,
        duration_days=duration_days,
        selected_device_limit=sel_dev_int,
        selected_traffic_limit=sel_trf_int,
        carried_addons_rub=carried.price_rub,
        carried_device_limit=carried.device_limit,
        carried_traffic_limit=carried.traffic_gb,
    )


_DAY_MS = 86_400_000


async def compute_remaining_credit(
    session: AsyncSession,
    *,
    now_ms: int,
    current_expiry_ms: int,
    current_tariff_id: int | None,
    current_selected_device: int | None,
    current_selected_traffic: int | None,
) -> float:
    """Стоимость остатка текущей подписки в рублях."""
    if current_tariff_id is None:
        return 0.0
    remaining_ms = max(0, int(current_expiry_ms) - int(now_ms))
    if remaining_ms <= 0:
        return 0.0
    old_tariff = await get_tariff_by_id(session, int(current_tariff_id))
    if not old_tariff:
        return 0.0
    old_duration = int(old_tariff.get("duration_days") or 0)
    if old_duration <= 0:
        return 0.0
    from services.tariffs import calculate_config_price

    old_price = float(
        calculate_config_price(
            tariff=old_tariff,
            selected_device_limit=int(current_selected_device) if current_selected_device is not None else None,
            selected_traffic_gb=int(current_selected_traffic) if current_selected_traffic is not None else None,
        )
    )
    remaining_days = remaining_ms / _DAY_MS
    return round(remaining_days * (old_price / old_duration), 2)


async def _is_trial_tariff(session: AsyncSession, tariff_id: int | None) -> bool:
    """Триал ли текущий тариф."""
    if not tariff_id:
        return False
    tariff = await get_tariff_by_id(session, int(tariff_id))
    return bool(tariff) and (tariff.get("group_code") or "").strip() == "trial"


async def _is_same_config(
    session: AsyncSession,
    *,
    current_tariff_id: int | None,
    current_selected_device: int | None,
    current_selected_traffic: int | None,
    new_tariff_id: int,
    new_selected_device: int | None,
    new_selected_traffic: int | None,
) -> bool:
    """Совпадают ли эффективные лимиты (устройства+трафик); tariff_id/длительность не учитываются."""
    if current_tariff_id is None:
        return False

    from services.tariffs.tariff_display import get_effective_limits_for_key

    old_dev, old_trf = await get_effective_limits_for_key(
        session,
        tariff_id=int(current_tariff_id),
        selected_device_limit=current_selected_device,
        selected_traffic_gb=current_selected_traffic,
    )
    new_dev, new_trf = await get_effective_limits_for_key(
        session,
        tariff_id=int(new_tariff_id),
        selected_device_limit=new_selected_device,
        selected_traffic_gb=new_selected_traffic,
    )
    return old_dev == new_dev and old_trf == new_trf


async def compute_renewal_expiry(
    session: AsyncSession,
    *,
    now_ms: int,
    current_expiry_ms: int,
    current_tariff_id: int | None,
    current_selected_device: int | None,
    current_selected_traffic: int | None,
    new_tariff_id: int,
    new_selected_device: int | None,
    new_selected_traffic: int | None,
    new_duration_days: int | None = None,
) -> int:
    """Новый expiry: та же конфигурация или истекший ключ — стек к остатку, иначе период от now."""
    now_ms = int(now_ms)
    current_expiry_ms = int(current_expiry_ms)
    new_tariff = await get_tariff_by_id(session, int(new_tariff_id))
    new_duration = (
        int(new_duration_days) if new_duration_days is not None else int((new_tariff or {}).get("duration_days") or 0)
    )
    new_dur_ms = new_duration * _DAY_MS
    remaining_ms = max(0, current_expiry_ms - now_ms)

    if remaining_ms <= 0:
        return now_ms + new_dur_ms

    same_config = await _is_same_config(
        session,
        current_tariff_id=current_tariff_id,
        current_selected_device=current_selected_device,
        current_selected_traffic=current_selected_traffic,
        new_tariff_id=new_tariff_id,
        new_selected_device=new_selected_device,
        new_selected_traffic=new_selected_traffic,
    )
    if same_config:
        return current_expiry_ms + new_dur_ms

    return now_ms + new_dur_ms


async def compute_renewal_quote(
    session: AsyncSession,
    *,
    billing_user_id: int,
    key_email: str,
    current_tariff_id: int | None,
    current_selected_device: int | None,
    current_selected_traffic: int | None,
    current_expiry_ms: int,
    now_ms: int,
    new_tariff_id: int,
    new_selected_device: int | None,
    new_selected_traffic: int | None,
    coupon_code: str | None = None,
) -> RenewalQuote:
    """Единый расчёт: продление это или смена, цена нового, остаток на баланс и нетто."""
    pricing = await calculate_renewal_pricing(
        session,
        billing_user_id=billing_user_id,
        key_email=key_email,
        tariff_id=int(new_tariff_id),
        coupon_code=coupon_code,
        selected_device_limit=new_selected_device,
        selected_traffic_limit=new_selected_traffic,
        credit_rub=0.0,
    )
    duration_days = pricing.duration_days
    eff_dev = pricing.selected_device_limit
    eff_trf = pricing.selected_traffic_limit

    is_switch = not await _is_same_config(
        session,
        current_tariff_id=current_tariff_id,
        current_selected_device=current_selected_device,
        current_selected_traffic=current_selected_traffic,
        new_tariff_id=new_tariff_id,
        new_selected_device=eff_dev,
        new_selected_traffic=eff_trf,
    )

    new_expiry = await compute_renewal_expiry(
        session,
        now_ms=now_ms,
        current_expiry_ms=current_expiry_ms,
        current_tariff_id=current_tariff_id,
        current_selected_device=current_selected_device,
        current_selected_traffic=current_selected_traffic,
        new_tariff_id=new_tariff_id,
        new_selected_device=eff_dev,
        new_selected_traffic=eff_trf,
        new_duration_days=duration_days,
    )

    full_price = int(pricing.final_price_rub)

    if not is_switch:
        return RenewalQuote(
            is_switch=False,
            duration_days=duration_days,
            selected_device_limit=eff_dev,
            selected_traffic_limit=eff_trf,
            total_gb=pricing.total_gb,
            new_full_price_rub=full_price,
            credit_rub=0,
            net_cost_rub=full_price,
            refund_to_balance_rub=0,
            new_expiry_ms=new_expiry,
            coupon_id=pricing.coupon_id,
            applied_coupon_code=pricing.applied_coupon_code,
            carried_addons_rub=pricing.carried_addons_rub,
            carried_device_limit=pricing.carried_device_limit,
            carried_traffic_limit=pricing.carried_traffic_limit,
        )

    credit = int(
        round(
            await compute_remaining_credit(
                session,
                now_ms=now_ms,
                current_expiry_ms=current_expiry_ms,
                current_tariff_id=current_tariff_id,
                current_selected_device=current_selected_device,
                current_selected_traffic=current_selected_traffic,
            )
        )
    )
    from core.bootstrap import MODES_CONFIG

    keep_period = bool(MODES_CONFIG.get("RENEWAL_SWITCH_KEEP_PERIOD", False))
    if keep_period and await _is_trial_tariff(session, current_tariff_id):
        keep_period = False
    remaining_ms = max(0, int(current_expiry_ms) - int(now_ms))
    if keep_period and remaining_ms > 0 and duration_days > 0:
        remaining_days = remaining_ms / _DAY_MS
        cost_for_remaining = int(round(full_price * remaining_days / duration_days))
        net_cost = cost_for_remaining - credit
        return RenewalQuote(
            is_switch=True,
            duration_days=duration_days,
            selected_device_limit=eff_dev,
            selected_traffic_limit=eff_trf,
            total_gb=pricing.total_gb,
            new_full_price_rub=full_price,
            credit_rub=credit,
            net_cost_rub=net_cost,
            refund_to_balance_rub=max(0, -net_cost),
            new_expiry_ms=int(current_expiry_ms),
            coupon_id=pricing.coupon_id,
            applied_coupon_code=pricing.applied_coupon_code,
            credit_value_rub=credit,
            keeps_period=True,
        )

    credit_as_days = bool(MODES_CONFIG.get("RENEWAL_CREDIT_AS_DAYS", False))
    if credit_as_days and credit > 0 and full_price > 0 and duration_days > 0:
        extra_days = int(credit * duration_days // full_price)
        if extra_days > 0:
            return RenewalQuote(
                is_switch=True,
                duration_days=duration_days,
                selected_device_limit=eff_dev,
                selected_traffic_limit=eff_trf,
                total_gb=pricing.total_gb,
                new_full_price_rub=full_price,
                credit_rub=0,
                net_cost_rub=full_price,
                refund_to_balance_rub=0,
                new_expiry_ms=new_expiry + extra_days * _DAY_MS,
                coupon_id=pricing.coupon_id,
                applied_coupon_code=pricing.applied_coupon_code,
                credit_days=extra_days,
                credit_value_rub=credit,
            )

    net_cost = full_price - credit

    return RenewalQuote(
        is_switch=True,
        duration_days=duration_days,
        selected_device_limit=eff_dev,
        selected_traffic_limit=eff_trf,
        total_gb=pricing.total_gb,
        new_full_price_rub=full_price,
        credit_rub=credit,
        net_cost_rub=net_cost,
        refund_to_balance_rub=max(0, -net_cost),
        new_expiry_ms=new_expiry,
        coupon_id=pricing.coupon_id,
        applied_coupon_code=pricing.applied_coupon_code,
        credit_value_rub=credit,
    )


async def execute_renewal(
    session: AsyncSession,
    billing_user_id: int,
    client_id: str,
    key_email: str,
    key_server_id: str,
    tariff_id: int,
    new_expiry_time: int,
    total_gb: int,
    cost: float,
    selected_device_limit: int | None = None,
    selected_traffic_limit: int | None = None,
    selected_price_rub: int | None = None,
    coupon_id: int | None = None,
) -> RenewalResult:
    """Выполняет продление ключа на кластере и обновляет БД.

    Не отправляет сообщений в Telegram — это делает вызывающий код.
    Raises: NotFoundError, ValidationError
    """
    tariff = await get_tariff_by_id(session, tariff_id)
    if not tariff:
        raise NotFoundError(f"Тариф с id={tariff_id} не найден")

    key_info = await get_key_details(session, key_email)
    if not key_info:
        raise NotFoundError(f"Ключ {client_id} не найден в БД")

    final_device, final_traffic = _resolve_effective_limits(
        tariff,
        selected_device_limit,
        selected_traffic_limit,
    )

    from services.addons import resolve_carried_addons
    from services.operations import renew_key_in_cluster
    from services.tariffs.tariff_display import GB, get_effective_limits_for_key

    carried = resolve_carried_addons(tariff, key_info, final_device, final_traffic)
    carried_device = carried.device_limit if carried.device_limit is not None else final_device
    carried_traffic = carried.traffic_gb if carried.traffic_gb is not None else final_traffic

    if tariff.get("configurable"):
        sel_trf = int(carried_traffic) if carried_traffic is not None else None
        sel_dev = int(carried_device) if carried_device is not None else None
        device_eff, traffic_bytes_eff = await get_effective_limits_for_key(
            session=session,
            tariff_id=tariff_id,
            selected_device_limit=sel_dev,
            selected_traffic_gb=sel_trf,
        )
        traffic_gb_eff = int(traffic_bytes_eff / GB) if traffic_bytes_eff else 0
        total_gb = traffic_gb_eff
    else:
        device_eff = final_device
        traffic_gb_eff = int(final_traffic) if final_traffic is not None else 0
        total_gb = traffic_gb_eff

    current_subgroup = None
    try:
        cur_tariff_id = key_info.get("tariff_id")
        if cur_tariff_id:
            cur_tariff = await get_tariff_by_id(session, int(cur_tariff_id))
            if cur_tariff:
                current_subgroup = cur_tariff.get("subgroup_title")
    except Exception as e:
        logger.warning("[Keys] Ошибка получения subgroup текущего тарифа: {}", e)

    target_subgroup = tariff.get("subgroup_title")

    cluster_id = await resolve_cluster_name(session, key_server_id)
    if not cluster_id:
        raise NotFoundError(f"Кластер для {key_server_id} не найден")

    renewed_ok = await renew_key_in_cluster(
        cluster_id=cluster_id,
        email=key_email,
        client_id=client_id,
        new_expiry_time=new_expiry_time,
        total_gb=total_gb,
        session=session,
        hwid_device_limit=device_eff,
        reset_traffic=True,
        target_subgroup=target_subgroup,
        old_subgroup=current_subgroup,
        plan=tariff_id,
    )
    if not renewed_ok:
        raise ServiceError("Не удалось продлить подписку на сервере. Средства не списаны.")

    key_row = await get_key_details(session, key_email)
    effective_client_id = key_row["client_id"] if key_row else client_id

    await update_key_expiry(session, effective_client_id, new_expiry_time, price_rub=float(cost))
    from database.keys import invalidate_keys_list

    await invalidate_keys_list(session, billing_user_id)

    new_dev = tariff.get("device_limit")
    new_trf = tariff.get("traffic_limit")

    if tariff.get("configurable"):
        await update_key_renewal_snapshot(
            session,
            key_email,
            tariff_id=tariff_id,
            apply_limits=False,
        )
    else:
        await update_key_renewal_snapshot(
            session,
            key_email,
            tariff_id=tariff_id,
            selected_device_limit=None if new_dev is None else new_dev,
            current_device_limit=None if new_dev is None else final_device,
            selected_traffic_limit=None if new_trf is None else new_trf,
            current_traffic_limit=None if new_trf is None else final_traffic,
            apply_limits=True,
        )
    if cost:
        debited = await update_balance(session, billing_user_id, -cost)
        if cost > 0 and debited is None:
            raise InsufficientFundsError("Недостаточно средств для продления. Средства не списаны.")

    if tariff.get("configurable"):
        cfg = normalize_tariff_config(tariff)
        raw_device_opts = cfg.get("device_options") or tariff.get("device_options") or []
        raw_traffic_opts = cfg.get("traffic_options_gb") or tariff.get("traffic_options_gb") or []
        has_device = len([v for v in raw_device_opts if _try_int(v) is not None]) > 1
        has_traffic = len([v for v in raw_traffic_opts if _try_int(v) is not None]) > 1

        await save_key_config_with_mode(
            session=session,
            email=key_email,
            selected_devices=final_device,
            selected_traffic_gb=final_traffic,
            total_price=int(selected_price_rub or cost),
            has_device_choice=has_device,
            has_traffic_choice=has_traffic,
            config_mode="renewal",
        )
        if carried.has_any:
            await update_key_renewal_snapshot(
                session,
                key_email,
                tariff_id=tariff_id,
                selected_device_limit=final_device,
                current_device_limit=carried_device,
                selected_traffic_limit=final_traffic,
                current_traffic_limit=carried_traffic,
                apply_limits=True,
            )
        elif has_device or has_traffic:
            await reset_key_current_limits_to_selected(session, effective_client_id)

    if coupon_id is not None:
        await mark_coupon_used(session, coupon_id, billing_user_id)

    new_balance = float(await get_balance(session, billing_user_id))

    return RenewalResult(
        ok=True,
        client_id=effective_client_id,
        tariff_id=tariff_id,
        charged_rub=int(cost),
        balance_rub=new_balance,
        new_expiry_time=new_expiry_time,
        base_price_rub=int(selected_price_rub or cost),
        final_price_rub=int(cost),
    )


def _try_int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


@dataclass
class CreatedVpnKey:
    """Результат headless-создания ключа (без UI-ответа)."""

    client_id: str
    email: str
    cluster_id: str
    final_link: str
    key_record: dict
    price_charged: int


async def create_vpn_key_headless(
    session: AsyncSession,
    tg_id: int,
    expiry_time: datetime,
    *,
    plan: int | None = None,
    selected_device_limit: int | None = None,
    selected_traffic_gb: int | None = None,
    selected_price_rub: int | None = None,
    skip_balance_charge: bool = False,
    is_trial: bool = False,
    forced_cluster: str | None = None,
) -> CreatedVpnKey:
    """Создаёт VPN-ключ для пользователя без aiogram/FSM зависимостей.

    Используется из service-слоя (gift redemption, web tariff purchase, webhook
    completion) — везде, где нет Message/CallbackQuery. Доменная логика та же,
    что и у `handlers.keys.key_mode.key_cluster_mode`, но без построения
    клавиатуры и отправки ответа.

    Raises:
        NotFoundError: пользователь не найден.
        ValidationError: не удалось определить кластер или создать ключ.
    """
    from handlers.utils import generate_random_email
    from services.clusters import select_cluster
    from services.operations import create_key_on_cluster
    from services.tariffs.tariff_display import (
        get_effective_limits_for_key,
        resolve_price_to_charge,
    )

    owner = await resolve_user_optional(session, tg_id)
    if owner is None:
        raise NotFoundError(f"Пользователь не найден: {tg_id}")

    key_name = await generate_random_email(session=session)
    client_id = str(uuid.uuid4())
    email = key_name.lower()
    expiry_timestamp = int(expiry_time.timestamp() * 1000)

    device_limit, traffic_limit_bytes = await get_effective_limits_for_key(
        session=session,
        tariff_id=plan,
        selected_device_limit=selected_device_limit,
        selected_traffic_gb=selected_traffic_gb,
    )
    if device_limit is None:
        device_limit = 0
    if traffic_limit_bytes is None:
        traffic_limit_bytes = 0

    if forced_cluster:
        cluster_id = forced_cluster
    else:
        cluster_result = await select_cluster(session)
        cluster_id = cluster_result.cluster_name

    if selected_price_rub is not None:
        price_to_charge = int(selected_price_rub)
    else:
        resolved = await resolve_price_to_charge(session, {})
        price_to_charge = int(resolved or 0)

    await create_key_on_cluster(
        cluster_id=cluster_id,
        tg_id=tg_id,
        client_id=client_id,
        email=email,
        expiry_timestamp=expiry_timestamp,
        plan=plan,
        session=session,
        hwid_limit=device_limit,
        traffic_limit_bytes=traffic_limit_bytes,
        is_trial=is_trial,
    )
    logger.info(f"[Key Creation] Ключ создан на кластере {cluster_id} для пользователя {tg_id}")

    await update_key_post_creation_snapshot(
        session,
        user_id=owner.id,
        email=email,
        selected_device_limit=selected_device_limit,
        selected_traffic_limit=selected_traffic_gb,
        selected_price_rub=price_to_charge,
    )

    key_record = await get_key_details(session, email)
    if not key_record:
        raise ValidationError(f"Ключ не найден после создания: {email}")
    final_link = key_record.get("link", "") or ""

    if is_trial:
        trial_status = await get_trial(session, tg_id)
        if trial_status in (0, -1):
            await update_trial(session, tg_id, 1)

    if price_to_charge and not skip_balance_charge:
        logger.info(f"[Key Creation] Списание с баланса user={tg_id}: -{price_to_charge} ₽")
        debited = await update_balance(session, tg_id, -int(price_to_charge))
        if debited is None:
            raise InsufficientFundsError("Недостаточно средств на балансе")
    elif skip_balance_charge:
        logger.info(f"[Key Creation] Пропуск списания (skip_balance_charge) user={tg_id}")
    else:
        logger.info(f"[Key Creation] Списание не требуется (price=0) user={tg_id}")

    return CreatedVpnKey(
        client_id=client_id,
        email=email,
        cluster_id=cluster_id,
        final_link=final_link,
        key_record=key_record,
        price_charged=int(price_to_charge or 0),
    )

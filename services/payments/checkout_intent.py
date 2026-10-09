from __future__ import annotations

import copy
import uuid

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from math import ceil

from sqlalchemy.ext.asyncio import AsyncSession

from core.settings.modes_config import MODES_CONFIG
from core.settings.tariffs_config import TARIFFS_CONFIG, normalize_tariff_config
from core.settings.yookassa_autopay_config import YOOKASSA_AUTOPAY_CONFIG
from database.access.resolution import UserId
from database.coupons import check_coupon_usage, mark_coupon_used
from database.keys import get_key_by_server, get_key_by_user_and_email, get_key_for_checkout, save_key_config_with_mode
from database.tariffs import get_tariff_by_id
from database.temporary_data import clear_temporary_data, get_temporary_data
from database.users import get_balance, get_trial, update_balance
from services.addons import calc_pack_full_price_rub
from services.clusters import select_cluster
from services.coupons import resolve_percent_coupon_soft
from services.errors import InsufficientFundsError, ValidationError
from services.gifts import create_gift
from services.keys import compute_renewal_quote, create_vpn_key_headless, execute_renewal, normalize_expiry_ms
from services.operations import renew_key_in_cluster
from services.tariffs import calculate_config_price
from services.tariffs.cooldown import get_tariff_cooldown_remaining
from services.tariffs.renewal_groups import DEFAULT_RENEWAL_FORBIDDEN_GROUPS
from services.tariffs.tariff_display import GB, get_effective_limits_for_key
from services.tariffs.visibility import is_tariff_visible_for


_FLOW_STATES = {
    "tariff_purchase": "waiting_for_payment",
    "trial_purchase": "waiting_for_payment",
    "key_renewal": "waiting_for_renewal_payment",
    "key_addons": "waiting_for_addons_payment",
    "gift_create": "waiting_for_gift_payment",
}


def checkout_key_snapshot(key) -> dict:
    """Фиксирует параметры подписки, от которых зависела цена."""
    fields = (
        "client_id",
        "email",
        "server_id",
        "expiry_time",
        "tariff_id",
        "selected_device_limit",
        "selected_traffic_limit",
        "selected_price_rub",
        "current_device_limit",
        "current_traffic_limit",
        "is_frozen",
    )
    return {field: getattr(key, field, None) for field in fields}


async def prepare_checkout_completion(session: AsyncSession, user_id: UserId, intent: dict) -> dict:
    """Проверяет оплаченные условия перед выдачей под блокировкой владельца."""
    intent = copy.deepcopy(intent)
    data = intent.get("data") or {}
    state = intent.get("state")
    if state == "balance_topup" or not state:
        return intent
    coupon_id = data.get("coupon_id")
    if coupon_id is not None and await check_coupon_usage(session, int(coupon_id), user_id):
        raise ValidationError("Купон уже использован; оплата оставлена на балансе")
    if state in {"waiting_for_renewal_payment", "waiting_for_addons_payment"}:
        expected = intent.get("key_snapshot")
        key = await get_key_for_checkout(session, user_id, client_id=data.get("client_id"), email=data.get("email"))
        if key is None or key.is_frozen:
            raise ValidationError("Оплаченная подписка недоступна; оплата оставлена на балансе")
        if isinstance(expected, dict) and checkout_key_snapshot(key) != expected:
            raise ValidationError("Подписка изменилась после создания счёта; оплата оставлена на балансе")
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        if state == "waiting_for_renewal_payment":
            if data.get("renewal_is_switch") is False:
                duration = int(data.get("renewal_duration_days") or 0)
                if duration <= 0:
                    raise ValidationError("В оплаченном продлении отсутствует срок подписки")
                data["new_expiry_time"] = max(normalize_expiry_ms(key.expiry_time), now_ms) + duration * 86400000
            elif int(data.get("new_expiry_time") or 0) <= now_ms:
                raise ValidationError("Срок оплаченной смены тарифа истёк; оплата оставлена на балансе")
        elif normalize_expiry_ms(key.expiry_time) <= now_ms:
            raise ValidationError("Подписка для покупки дополнений истекла; оплата оставлена на балансе")
    if state == "waiting_for_payment":
        if data.get("payment_flow") == "trial_purchase" and await get_trial(session, user_id) not in (0, -1):
            raise ValidationError("Пробный период уже использован; оплата оставлена на балансе")
        tariff = await get_tariff_by_id(session, int(data["tariff_id"]))
        if not tariff:
            raise ValidationError("Оплаченный тариф удалён; оплата оставлена на балансе")
        if (
            await get_tariff_cooldown_remaining(
                session, user_id, int(data["tariff_id"]), int(tariff.get("cooldown_days") or 0)
            )
            > 0
        ):
            raise ValidationError("Повторная покупка тарифа временно недоступна; оплата оставлена на балансе")
        duration = int(data.get("selected_duration_days") or 0)
        if duration <= 0:
            raise ValidationError("В оплаченном тарифе отсутствует срок подписки")
        data["new_expiry_time"] = int(datetime.now(UTC).timestamp() * 1000) + duration * 86400000
    return intent


def _optional_int(value):
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number != number.to_integral_value():
            raise ValueError
        return int(number)
    except (InvalidOperation, TypeError, ValueError, OverflowError) as exc:
        raise ValidationError("Некорректное целое значение в условиях оплаты") from exc


def _validate_choices(tariff: dict, devices: int | None, traffic: int | None, *, addons: bool = False):
    cfg = normalize_tariff_config(tariff)
    for value, field in ((devices, "device_options"), (traffic, "traffic_options_gb")):
        if value is None:
            continue
        options = cfg.get(field) or []
        if addons:
            options = cfg.get(f"addon_{field}") or options
        if value < 0 or (options and value not in [int(option) for option in options]):
            raise ValidationError("Выбранная конфигурация тарифа недоступна")
        if not options:
            fixed = tariff.get("device_limit" if field == "device_options" else "traffic_limit")
            if value != int(fixed or 0):
                raise ValidationError("Тариф не поддерживает выбранную конфигурацию")


def autopay_terms_for_tariff(tariff: dict, devices: int | None, traffic: int | None) -> dict | None:
    """Рассчитывает сумму и период автоплатежа по тарифу."""
    if str(tariff.get("group_code") or "").strip() in DEFAULT_RENEWAL_FORBIDDEN_GROUPS:
        return None
    full_price = calculate_config_price(tariff, devices, traffic)
    discount = max(0, min(100, float(YOOKASSA_AUTOPAY_CONFIG.get("AUTOPAY_DISCOUNT_PERCENT", 0))))
    period = int(tariff.get("duration_days") or 0)
    factor = (Decimal("100") - Decimal(str(discount))) / Decimal("100")
    amount = float((Decimal(str(full_price)) * factor).quantize(Decimal("0.01")))
    return {"amount": amount, "period_days": period} if period > 0 and amount > 0 else None


async def preview_checkout_autopay_terms(session: AsyncSession, user_id: int, metadata: dict) -> dict | None:
    snapshot = metadata.get("checkout_intent") or {}
    data = {**(snapshot.get("data") or {}), **metadata}
    if data.get("payment_flow") not in {"tariff_purchase", "key_renewal"}:
        return None
    tariff_id = _optional_int(data.get("tariff_id"))
    tariff = await get_tariff_by_id(session, tariff_id) if tariff_id else None
    if not tariff:
        raise ValidationError("Тариф не найден")
    devices = _optional_int(data.get("selected_device_limit"))
    traffic = _optional_int(
        data.get("selected_traffic_gb", data.get("selected_traffic_limit_gb", data.get("selected_traffic_limit")))
    )
    if data.get("payment_flow") == "key_renewal":
        key = await get_key_by_server(session, UserId(user_id), str(data.get("client_id") or ""))
        if not key or key.is_frozen:
            raise ValidationError("Подписка не найдена")
        devices = devices if devices is not None else key.selected_device_limit
        traffic = traffic if traffic is not None else key.selected_traffic_limit
    _validate_choices(tariff, devices, traffic)
    return autopay_terms_for_tariff(tariff, devices, traffic)


async def freeze_checkout_intent(
    session: AsyncSession,
    user_id: int,
    metadata: dict | None,
    amount: float,
    *,
    trusted_legacy: bool = False,
) -> dict:
    """Фиксирует условия оплачиваемой операции по серверным ценам."""
    user_id = UserId(user_id)
    meta = copy.deepcopy(metadata or {})
    snapshot = meta.pop("checkout_intent", None)
    if isinstance(snapshot, dict) and isinstance(snapshot.get("data"), dict):
        meta = {**snapshot["data"], **meta}
    temp = await get_temporary_data(session, user_id)
    flow = str(meta.get("payment_flow") or "").strip().lower()
    if not flow and temp:
        flow = {
            "waiting_for_payment": (temp.get("data") or {}).get("payment_flow", "tariff_purchase"),
            "waiting_for_renewal_payment": "key_renewal",
            "waiting_for_addons_payment": "key_addons",
            "waiting_for_gift_payment": "gift_create",
        }.get(temp.get("state"), "balance_topup")
    if flow not in _FLOW_STATES:
        return {"state": "balance_topup", "data": {"payment_flow": "balance_topup"}}

    state = _FLOW_STATES[flow]
    data = copy.deepcopy((temp or {}).get("data") or {}) if temp and temp.get("state") == state and not snapshot else {}
    data.update(meta)
    legacy_cost = _optional_int(data.get("cost", data.get("selected_price_rub"))) if trusted_legacy else None
    legacy_expiry = _optional_int(data.get("new_expiry_time")) if trusted_legacy else None
    legacy_terms = {
        name: data[name]
        for name in ("selected_price_rub", "total_gb", "coupon_id")
        if trusted_legacy and data.get(name) not in (None, "")
    }
    legacy_duration = _optional_int(data.get("selected_duration_days")) if trusted_legacy else None
    data["payment_flow"] = flow
    data["required_amount"] = float(amount)
    data["selected_traffic_limit_gb"] = data.get(
        "selected_traffic_gb", data.get("selected_traffic_limit_gb", data.get("selected_traffic_limit"))
    )
    tariff_id = _optional_int(data.get("tariff_id"))
    tariff = await get_tariff_by_id(session, tariff_id) if tariff_id else None
    if not tariff:
        raise ValidationError("Тариф не найден")
    devices = _optional_int(data.get("selected_device_limit"))
    traffic = _optional_int(data.get("selected_traffic_limit_gb"))
    if not trusted_legacy:
        if flow == "key_addons" and not tariff.get("configurable"):
            raise ValidationError("Тариф не поддерживает изменение конфигурации")
        _validate_choices(tariff, devices, traffic, addons=flow == "key_addons")
    now_ms = int(datetime.now(UTC).timestamp() * 1000)

    key = None
    if flow == "key_renewal":
        key = await get_key_for_checkout(session, user_id, client_id=data.get("client_id"), email=data.get("email"))
        if not key or key.is_frozen:
            raise ValidationError("Подписка не найдена")
        quote = await compute_renewal_quote(
            session,
            billing_user_id=user_id,
            key_email=key.email,
            current_tariff_id=key.tariff_id,
            current_selected_device=key.selected_device_limit,
            current_selected_traffic=key.selected_traffic_limit,
            current_expiry_ms=normalize_expiry_ms(key.expiry_time),
            now_ms=now_ms,
            new_tariff_id=int(tariff_id),
            new_selected_device=devices,
            new_selected_traffic=traffic,
            coupon_code=data.get("applied_coupon_code"),
        )
        data.update(
            email=key.email,
            client_id=key.client_id,
            server_id=key.server_id,
            cost=int(quote.net_cost_rub),
            selected_price_rub=int(quote.new_full_price_rub),
            new_expiry_time=int(quote.new_expiry_ms),
            total_gb=int(quote.total_gb),
            selected_device_limit=quote.selected_device_limit,
            selected_traffic_limit=quote.selected_traffic_limit,
            coupon_id=quote.coupon_id,
            renewal_is_switch=quote.is_switch,
            renewal_duration_days=legacy_duration if legacy_duration is not None else quote.duration_days,
        )
    elif flow == "key_addons":
        key = await get_key_for_checkout(session, user_id, client_id=data.get("client_id"), email=data.get("email"))
        if not key or key.is_frozen or int(key.tariff_id or 0) != int(tariff_id):
            raise ValidationError("Подписка не найдена")
        await _freeze_addons(session, data, key, tariff, devices, traffic, now_ms)
    else:
        if not trusted_legacy and not await is_tariff_visible_for(session, user_id, tariff):
            raise ValidationError("Тариф недоступен")
        if flow == "gift_create" and tariff.get("group_code") != "gifts":
            raise ValidationError("Тариф подарка не найден")
        if flow != "gift_create" and tariff.get("group_code") == "gifts":
            raise ValidationError("Для подарка требуется отдельное оформление")
        if flow == "trial_purchase" and await get_trial(session, user_id) not in (0, -1):
            raise ValidationError("Пробный период уже использован")
        if (
            not trusted_legacy
            and await get_tariff_cooldown_remaining(
                session, user_id, int(tariff_id), int(tariff.get("cooldown_days") or 0)
            )
            > 0
        ):
            raise ValidationError("Тариф временно недоступен для повторной покупки")
        duration = legacy_duration if legacy_duration is not None else int(tariff.get("duration_days") or 0)
        if duration <= 0:
            raise ValidationError("Некорректная длительность тарифа")
        price = int(calculate_config_price(tariff, devices, traffic))
        price, _, coupon_id, _ = await resolve_percent_coupon_soft(
            session=session,
            billing_user_id=user_id,
            base_price_rub=price,
            coupon_code=data.get("applied_coupon_code"),
        )
        data.update(
            cost=int(price),
            selected_price_rub=int(price),
            coupon_id=coupon_id,
            new_expiry_time=now_ms + duration * 86400000,
            selected_duration_days=duration,
        )
        if flow != "gift_create":
            operation_id = uuid.uuid4()
            data["created_client_id"] = str(operation_id)
            data["created_email"] = f"yk{operation_id.hex[:16]}"
            data["created_cluster"] = (await select_cluster(session)).cluster_name
    if legacy_cost is not None and legacy_cost >= 0:
        data["cost"] = legacy_cost
        data["selected_price_rub"] = legacy_cost
    for field, value in legacy_terms.items():
        data[field] = _optional_int(value)
    if legacy_expiry is not None and legacy_expiry > 0:
        data["new_expiry_time"] = normalize_expiry_ms(legacy_expiry)
    if not trusted_legacy and flow in {"tariff_purchase", "key_renewal"}:
        terms = await preview_checkout_autopay_terms(session, user_id, data)
        if terms:
            data["autopay_amount"] = terms["amount"]
            data["autopay_period_days"] = terms["period_days"]
    cost = max(0, int(data.get("cost") or 0))
    shortfall = max(0, ceil(cost - float(await get_balance(session, user_id))))
    if not trusted_legacy and float(amount) < shortfall:
        raise InsufficientFundsError("Сумма платежа недостаточна для выбранной операции")
    result = {"state": state, "data": data}
    if key is not None:
        result["key_snapshot"] = checkout_key_snapshot(key)
    if temp and not snapshot:
        result["temporary_checkout"] = copy.deepcopy(temp)
    return result


async def normalize_legacy_checkout_intent(
    session: AsyncSession, user_id: int, saved_metadata: dict, amount: float
) -> dict:
    """Приводит сохранённые условия прежней оплаты к текущему формату."""
    metadata = copy.deepcopy(saved_metadata or {})
    old_flow = str(metadata.get("flow_type") or "").lower()
    if (
        not old_flow
        and metadata.get("fast_flow") in {True, "true"}
        and all(
            metadata.get(field) not in (None, "", "None", "null")
            for field in ("client_id", "tariff_id", "new_expiry_time", "cost")
        )
    ):
        old_flow = "renewal"
    flow = metadata.get("payment_flow") or {
        "renewal": "key_renewal",
        "purchase": "tariff_purchase",
        "addons": "key_addons",
        "gift": "gift_create",
        "gift_create": "gift_create",
    }.get(old_flow, "balance_topup")
    metadata["payment_flow"] = flow
    for field in (
        "tariff_id",
        "cost",
        "selected_price_rub",
        "selected_duration_days",
        "new_expiry_time",
        "selected_device_limit",
        "selected_traffic_limit",
        "selected_traffic_gb",
        "coupon_id",
    ):
        if metadata.get(field) in {"None", "null", ""}:
            metadata[field] = None
    if flow == "key_addons" and metadata.get("cost") is None:
        metadata["cost"] = metadata.get("agreed_extra_price", metadata.get("extra_price"))
    return await freeze_checkout_intent(session, UserId(user_id), metadata, amount, trusted_legacy=True)


async def _freeze_addons(session, data, key, tariff, devices, traffic, now_ms):
    cfg = normalize_tariff_config(tariff)
    pack_mode = str(TARIFFS_CONFIG.get("KEY_ADDONS_PACK_MODE") or "")
    has_device = bool(cfg.get("device_options")) and (not pack_mode or pack_mode in {"devices", "all"})
    has_traffic = bool(cfg.get("traffic_options_gb")) and (not pack_mode or pack_mode in {"traffic", "all"})
    cur_devices = key.current_device_limit if key.current_device_limit is not None else key.selected_device_limit
    cur_traffic = key.current_traffic_limit if key.current_traffic_limit is not None else key.selected_traffic_limit
    base = int(calculate_config_price(tariff, cur_devices, cur_traffic))
    if pack_mode:
        extra = int(
            calc_pack_full_price_rub(
                tariff, has_device and devices is not None, has_traffic and traffic is not None, devices, traffic
            )
        )
        total = base + extra
        if MODES_CONFIG.get("KEY_ADDONS_RECALC_PRICE", TARIFFS_CONFIG.get("KEY_ADDONS_RECALC_PRICE", False)):
            duration_ms = max(1, int(tariff.get("duration_days") or 30) * 86400000)
            remaining_ms = min(duration_ms, max(0, normalize_expiry_ms(key.expiry_time) - now_ms))
            extra = ceil(extra * remaining_ms / duration_ms)
        effective_devices, traffic_bytes = await get_effective_limits_for_key(
            session,
            int(tariff["id"]),
            cur_devices,
            cur_traffic,
            tariff=tariff,
        )
        effective_traffic = int(traffic_bytes / GB) if traffic_bytes else 0
        if has_device and devices is not None:
            effective_devices = 0 if devices <= 0 or not effective_devices else int(effective_devices) + devices
        if has_traffic and traffic is not None:
            effective_traffic = 0 if traffic <= 0 or effective_traffic <= 0 else effective_traffic + traffic
    else:
        total = int(calculate_config_price(tariff, devices if has_device else None, traffic if has_traffic else None))
        extra = max(0, total - base)
        effective_devices, traffic_bytes = await get_effective_limits_for_key(
            session,
            int(tariff["id"]),
            devices if has_device else None,
            traffic if has_traffic and traffic is not None else 0,
            tariff=tariff,
        )
        effective_traffic = int(traffic_bytes / GB) if traffic_bytes else 0
    extra, _, coupon_id, _ = await resolve_percent_coupon_soft(
        session=session,
        billing_user_id=UserId(key.user_id),
        base_price_rub=extra,
        coupon_code=data.get("applied_coupon_code"),
    )
    data.update(
        cost=int(extra),
        coupon_id=coupon_id,
        client_id=key.client_id,
        email=key.email,
        agreed_extra_price=int(extra),
        original_price=base,
        current_device_limit=cur_devices,
        current_traffic_gb=cur_traffic,
        server_id=key.server_id,
        new_expiry_time=normalize_expiry_ms(key.expiry_time),
        target_device_limit=int(effective_devices or 0),
        target_traffic_limit=int(effective_traffic),
        total_price=int(total),
        pack_mode=pack_mode,
        has_device_choice=has_device,
        has_traffic_choice=has_traffic,
    )


async def apply_checkout_intent(
    session: AsyncSession,
    user_id: int,
    intent: dict,
    paid_amount: float,
    *,
    result_sink: dict | None = None,
) -> str | None:
    """Выполняет сохранённую оплаченную операцию клиента."""
    user_id = UserId(user_id)
    data = intent.get("data") or {}
    state = intent.get("state")
    if state == "balance_topup" or not state:
        return None
    cost = int(data.get("cost") or 0)
    if float(await get_balance(session, user_id)) < cost:
        raise InsufficientFundsError("Средства для завершения оплаченной операции недоступны")
    flow = data.get("payment_flow")
    client_id = None
    if state == "waiting_for_renewal_payment":
        key = await get_key_by_server(session, user_id, str(data.get("client_id") or ""))
        if not key:
            raise ValidationError("Подписка не найдена у владельца платежа")
        result = await execute_renewal(
            session=session,
            billing_user_id=user_id,
            client_id=key.client_id,
            key_email=key.email,
            key_server_id=key.server_id,
            tariff_id=int(data["tariff_id"]),
            new_expiry_time=max(int(data["new_expiry_time"]), normalize_expiry_ms(key.expiry_time)),
            total_gb=int(data.get("total_gb") or 0),
            cost=cost,
            selected_device_limit=_optional_int(data.get("selected_device_limit")),
            selected_traffic_limit=_optional_int(data.get("selected_traffic_limit")),
            selected_price_rub=int(data.get("selected_price_rub") or cost),
            coupon_id=_optional_int(data.get("coupon_id")),
            expected_expiry_time=normalize_expiry_ms(key.expiry_time),
            expected_key_snapshot=intent.get("key_snapshot") or checkout_key_snapshot(key),
        )
        client_id = result.client_id
    elif state == "waiting_for_addons_payment":
        client_id = await _apply_addons(session, user_id, data)
    elif state == "waiting_for_gift_payment" or flow == "gift_create":
        gift = await create_gift(
            session,
            sender_user_ref=user_id,
            tariff_id=int(data["tariff_id"]),
            selected_device_limit=_optional_int(data.get("selected_device_limit")),
            selected_traffic_gb=_optional_int(data.get("selected_traffic_limit_gb")),
            selected_price_rub=cost,
        )
        if result_sink is not None:
            result_sink["gift"] = {
                "gift_id": gift.gift_id,
                "gift_link": gift.gift_link,
                "site_gift_link": gift.site_gift_link,
                "tariff_name": gift.tariff_name,
                "duration_text": gift.duration_text,
                "expiry_time": gift.expiry_time.isoformat(),
            }
    elif state == "waiting_for_payment":
        result = await create_vpn_key_headless(
            session,
            tg_id=user_id,
            expiry_time=datetime.fromtimestamp(int(data["new_expiry_time"]) / 1000, tz=UTC),
            plan=int(data["tariff_id"]),
            selected_device_limit=_optional_int(data.get("selected_device_limit")),
            selected_traffic_gb=_optional_int(data.get("selected_traffic_limit_gb")),
            selected_price_rub=cost,
            forced_cluster=data["created_cluster"],
            forced_client_id=data["created_client_id"],
            forced_email=data["created_email"],
            is_trial=flow == "trial_purchase",
        )
        client_id = result.client_id
    else:
        raise ValidationError("Неизвестная оплаченная операция")
    coupon_id = _optional_int(data.get("coupon_id"))
    if coupon_id is not None and state != "waiting_for_renewal_payment":
        await mark_coupon_used(session, coupon_id, user_id)
    previous = intent.get("temporary_checkout")
    if previous and await get_temporary_data(session, user_id) == previous:
        await clear_temporary_data(session, user_id)
    return client_id


async def _apply_addons(session, user_id, data):
    key = await get_key_by_user_and_email(session, UserId(user_id), data["email"])
    if not key or key.client_id != data["client_id"]:
        raise ValidationError("Подписка не найдена у владельца платежа")
    tariff = await get_tariff_by_id(session, int(data["tariff_id"]))
    if not tariff:
        raise ValidationError("Тариф не найден")
    succeeded = await renew_key_in_cluster(
        cluster_id=key.server_id,
        email=key.email,
        client_id=key.client_id,
        new_expiry_time=max(int(data["new_expiry_time"]), normalize_expiry_ms(key.expiry_time)),
        total_gb=int(data["target_traffic_limit"]),
        session=session,
        hwid_device_limit=int(data["target_device_limit"]),
        reset_traffic=False,
        target_subgroup=tariff.get("subgroup_title"),
        old_subgroup=tariff.get("subgroup_title"),
        plan=int(data["tariff_id"]),
    )
    if not succeeded:
        raise ValidationError("Не удалось применить оплаченные дополнения")
    if await update_balance(session, user_id, -int(data["cost"])) is None:
        raise InsufficientFundsError("Недостаточно средств для дополнений")
    await save_key_config_with_mode(
        session=session,
        email=key.email,
        selected_devices=int(data["target_device_limit"])
        if data.get("pack_mode")
        else _optional_int(data.get("selected_device_limit")),
        selected_traffic_gb=int(data["target_traffic_limit"])
        if data.get("pack_mode")
        else _optional_int(data.get("selected_traffic_limit_gb")),
        total_price=int(data["total_price"]),
        has_device_choice=bool(data["has_device_choice"]),
        has_traffic_choice=bool(data["has_traffic_choice"]),
        config_mode="pack" if data.get("pack_mode") else "addon",
    )
    return key.client_id

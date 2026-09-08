from .._common import *  # noqa: F401,F403 — подтягиваем все имена для endpoints
from .._common import (
    _key_actions_config,
    _normalize_expiry_ms,
    _resolve_available_location_servers,
    _resolve_billing_user_id,
    _resolve_default_web_payment_provider,
    _resolve_public_base_url,
    router,
    user_router,
)


@user_router.get("/{client_id}/addons-preview", response_model=AccountKeyAddonsPreviewResponse)
async def user_key_addons_preview(
    client_id: str,
    request: Request,
    selected_device_limit: int | None = Query(None),
    selected_traffic_gb: int | None = Query(None),
    include_device: bool | None = Query(None),
    include_traffic: bool | None = Query(None),
    coupon_code: str | None = Query(None),
    force_web: bool = Query(False),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    actions = _key_actions_config()
    if not force_web and not actions.addons_enabled:
        raise HTTPException(status_code=403, detail="Доп. опции отключены в настройках")
    billing_user_id = await _resolve_billing_user_id(request, identity, session)
    db_key = (
        await session.execute(select(Key).where(Key.user_id == billing_user_id, Key.client_id == client_id).limit(1))
    ).scalar_one_or_none()
    if db_key is None:
        raise HTTPException(status_code=404, detail="Подписка не найдена")
    tariff_id = getattr(db_key, "tariff_id", None)
    if not tariff_id:
        raise HTTPException(status_code=400, detail="Для подписки не назначен тариф")
    tariff = await get_tariff_by_id(session, int(tariff_id))
    if not tariff:
        raise HTTPException(status_code=404, detail="Тариф не найден")
    key_details = await get_key_details(session, str(getattr(db_key, "email", "") or ""))
    if not key_details:
        raise HTTPException(status_code=404, detail="Подписка не найдена")
    (
        _tariff_name,
        _subgroup_title,
        _traffic_limit_gb,
        _device_limit,
        _panel,
        is_tariff_configurable,
        addons_devices_enabled,
        addons_traffic_enabled,
    ) = await get_key_tariff_addons_state(session=session, key_record=key_details, db_key=db_key)
    if not is_tariff_configurable:
        raise HTTPException(status_code=400, detail="Тариф не поддерживает доп. опции")
    cfg = normalize_tariff_config(tariff)
    raw_device_options = cfg.get("device_options") or tariff.get("device_options") or []
    raw_traffic_options = cfg.get("traffic_options_gb") or tariff.get("traffic_options_gb") or []
    device_options: list[int] = []
    for value in raw_device_options:
        try:
            device_options.append(int(value))
        except (TypeError, ValueError):
            continue
    traffic_options: list[int] = []
    for value in raw_traffic_options:
        try:
            traffic_options.append(int(value))
        except (TypeError, ValueError):
            continue
    device_options = sorted(set(device_options), key=lambda val: (int(val == 0), val))
    traffic_options = sorted(set(traffic_options), key=lambda val: (int(val == 0), val))
    has_device_option = bool(device_options) and bool(addons_devices_enabled)
    has_traffic_option = bool(traffic_options) and bool(addons_traffic_enabled)
    pack_devices, pack_traffic, pack_mode = get_pack_flags()
    if pack_mode:
        has_device_option = has_device_option and bool(pack_devices)
        has_traffic_option = has_traffic_option and bool(pack_traffic)
    if not has_device_option:
        device_options = []
    if not has_traffic_option:
        traffic_options = []
    if not has_device_option and not has_traffic_option:
        raise HTTPException(status_code=400, detail="Доп. опции для этой подписки недоступны")
    selected_device_limit_db = key_details.get("selected_device_limit")
    selected_traffic_limit_db = key_details.get("selected_traffic_limit")
    current_device_limit_db = key_details.get("current_device_limit")
    current_traffic_limit_db = key_details.get("current_traffic_limit")
    base_devices = tariff.get("device_limit")
    base_devices = int(base_devices) if base_devices is not None else None
    base_traffic_bytes = tariff.get("traffic_limit")
    base_traffic_gb_from_tariff = int(base_traffic_bytes / GB) if base_traffic_bytes else None
    current_device_limit = (
        int(current_device_limit_db)
        if current_device_limit_db is not None
        else (int(selected_device_limit_db) if selected_device_limit_db is not None else base_devices)
    )
    current_traffic_gb = (
        int(current_traffic_limit_db)
        if current_traffic_limit_db is not None
        else (int(selected_traffic_limit_db) if selected_traffic_limit_db is not None else base_traffic_gb_from_tariff)
    )
    if pack_mode and current_device_limit is not None and int(current_device_limit) == 0:
        has_device_option = False
        device_options = []
    if pack_mode and current_traffic_gb is not None and int(current_traffic_gb) == 0:
        has_traffic_option = False
        traffic_options = []
    if not has_device_option and not has_traffic_option:
        raise HTTPException(status_code=400, detail="Доп. опции для этой подписки недоступны")
    if pack_mode:
        include_device_effective = (
            bool(include_device) if include_device is not None else selected_device_limit is not None
        )
        include_traffic_effective = (
            bool(include_traffic) if include_traffic is not None else selected_traffic_gb is not None
        )
        selected_device = selected_device_limit if selected_device_limit is not None else None
        selected_traffic = selected_traffic_gb if selected_traffic_gb is not None else None
    else:
        include_device_effective = has_device_option
        include_traffic_effective = has_traffic_option
        selected_device = selected_device_limit if selected_device_limit is not None else current_device_limit
        selected_traffic = selected_traffic_gb if selected_traffic_gb is not None else current_traffic_gb
    if (
        has_device_option
        and include_device_effective
        and selected_device is not None
        and int(selected_device) not in device_options
    ):
        raise HTTPException(status_code=400, detail="Выбранный пакет устройств недоступен")
    if (
        has_traffic_option
        and include_traffic_effective
        and selected_traffic is not None
        and int(selected_traffic) not in traffic_options
    ):
        raise HTTPException(status_code=400, detail="Выбранный пакет трафика недоступен")
    current_devices_for_price = int(current_device_limit) if current_device_limit is not None else None
    current_traffic_for_price = int(current_traffic_gb) if current_traffic_gb is not None else None
    base_price_for_current = int(
        calculate_config_price(
            tariff=tariff,
            selected_device_limit=current_devices_for_price,
            selected_traffic_gb=current_traffic_for_price,
        )
    )
    if pack_mode:
        diff_full = int(
            calc_pack_full_price_rub(
                tariff=tariff,
                has_device_option=bool(has_device_option and include_device_effective),
                has_traffic_option=bool(has_traffic_option and include_traffic_effective),
                selected_devices=int(selected_device)
                if has_device_option and include_device_effective and selected_device is not None
                else None,
                selected_traffic_gb=int(selected_traffic)
                if has_traffic_option and include_traffic_effective and selected_traffic is not None
                else None,
            )
        )
        recalc_enabled = bool(
            MODES_CONFIG.get(
                "KEY_ADDONS_RECALC_PRICE",
                TARIFFS_CONFIG.get("KEY_ADDONS_RECALC_PRICE", False),
            )
        )
        if recalc_enabled:
            remaining_seconds, total_seconds = calc_remaining_ratio_seconds(
                key_details.get("expiry_time"),
                tariff,
            )
            extra_price_rub = int((diff_full * remaining_seconds + total_seconds - 1) // total_seconds)
        else:
            extra_price_rub = diff_full
        total_price_rub = int(base_price_for_current + diff_full)
    else:
        total_price_rub = int(
            calculate_config_price(
                tariff=tariff,
                selected_device_limit=int(selected_device)
                if has_device_option and include_device_effective and selected_device is not None
                else None,
                selected_traffic_gb=int(selected_traffic)
                if has_traffic_option and include_traffic_effective and selected_traffic is not None
                else None,
            )
        )
        extra_price_rub = int(max(0, total_price_rub - base_price_for_current))
    final_extra_price_rub, discount_rub, _coupon_id, applied_coupon_code = await resolve_percent_coupon_pricing(
        session=session,
        billing_user_id=int(billing_user_id),
        base_price_rub=int(max(0, extra_price_rub)),
        coupon_code=coupon_code,
    )
    return AccountKeyAddonsPreviewResponse(
        client_id=str(getattr(db_key, "client_id", "") or ""),
        tariff_id=int(tariff_id),
        addons_mode=str(pack_mode or ""),
        has_device_option=bool(has_device_option),
        has_traffic_option=bool(has_traffic_option),
        current_device_limit=int(current_device_limit) if current_device_limit is not None else None,
        current_traffic_gb=int(current_traffic_gb) if current_traffic_gb is not None else None,
        selected_device_limit=int(selected_device)
        if has_device_option and include_device_effective and selected_device is not None
        else None,
        selected_traffic_gb=int(selected_traffic)
        if has_traffic_option and include_traffic_effective and selected_traffic is not None
        else None,
        device_options=[
            AccountKeyAddonOptionResponse(
                value=int(val),
                label=(
                    "Безлимит устройств"
                    if int(val) <= 0
                    else (f"+{int(val)} устройств" if pack_mode else f"{int(val)} устройств")
                ),
            )
            for val in device_options
        ],
        traffic_options=[
            AccountKeyAddonOptionResponse(
                value=int(val),
                label=("Безлимит трафика" if int(val) <= 0 else (f"+{int(val)} ГБ" if pack_mode else f"{int(val)} ГБ")),
            )
            for val in traffic_options
        ],
        total_price_rub=int(total_price_rub),
        extra_price_rub=int(max(0, extra_price_rub)),
        discount_rub=int(discount_rub),
        final_price_rub=int(max(0, final_extra_price_rub)),
        applied_coupon_code=applied_coupon_code,
        balance_rub=float(await get_balance(session, int(billing_user_id))),
    )


@user_router.post("/{client_id}/apply-addons", response_model=AccountKeyApplyAddonsResponse)
async def user_key_apply_addons(
    client_id: str,
    body: AccountKeyAddonsPreviewRequest,
    request: Request,
    force_web: bool = Query(False),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    actions = _key_actions_config()
    if not force_web and not actions.addons_enabled:
        raise HTTPException(status_code=403, detail="Доп. опции отключены в настройках")
    billing_user_id = await _resolve_billing_user_id(request, identity, session)
    db_key = (
        await session.execute(select(Key).where(Key.user_id == billing_user_id, Key.client_id == client_id).limit(1))
    ).scalar_one_or_none()
    if db_key is None:
        raise HTTPException(status_code=404, detail="Подписка не найдена")
    tariff_id = getattr(db_key, "tariff_id", None)
    if not tariff_id:
        raise HTTPException(status_code=400, detail="Для подписки не назначен тариф")
    tariff = await get_tariff_by_id(session, int(tariff_id))
    if not tariff:
        raise HTTPException(status_code=404, detail="Тариф не найден")
    key_details = await get_key_details(session, str(getattr(db_key, "email", "") or ""))
    if not key_details:
        raise HTTPException(status_code=404, detail="Подписка не найдена")
    (
        _tariff_name,
        _subgroup_title,
        _traffic_limit_gb,
        _device_limit,
        _panel,
        is_tariff_configurable,
        addons_devices_enabled,
        addons_traffic_enabled,
    ) = await get_key_tariff_addons_state(session=session, key_record=key_details, db_key=db_key)
    if not is_tariff_configurable:
        raise HTTPException(status_code=400, detail="Тариф не поддерживает доп. опции")
    cfg = normalize_tariff_config(tariff)
    raw_device_options = cfg.get("device_options") or tariff.get("device_options") or []
    raw_traffic_options = cfg.get("traffic_options_gb") or tariff.get("traffic_options_gb") or []
    device_options: list[int] = []
    for value in raw_device_options:
        try:
            device_options.append(int(value))
        except (TypeError, ValueError):
            continue
    traffic_options: list[int] = []
    for value in raw_traffic_options:
        try:
            traffic_options.append(int(value))
        except (TypeError, ValueError):
            continue
    device_options = sorted(set(device_options), key=lambda val: (int(val == 0), val))
    traffic_options = sorted(set(traffic_options), key=lambda val: (int(val == 0), val))
    has_device_option = bool(device_options) and bool(addons_devices_enabled)
    has_traffic_option = bool(traffic_options) and bool(addons_traffic_enabled)
    pack_devices, pack_traffic, pack_mode = get_pack_flags()
    if pack_mode:
        has_device_option = has_device_option and bool(pack_devices)
        has_traffic_option = has_traffic_option and bool(pack_traffic)
    if not has_device_option:
        device_options = []
    if not has_traffic_option:
        traffic_options = []
    if not has_device_option and not has_traffic_option:
        raise HTTPException(status_code=400, detail="Доп. опции для этой подписки недоступны")
    selected_device_limit_db = key_details.get("selected_device_limit")
    selected_traffic_limit_db = key_details.get("selected_traffic_limit")
    current_device_limit_db = key_details.get("current_device_limit")
    current_traffic_limit_db = key_details.get("current_traffic_limit")
    base_devices = tariff.get("device_limit")
    base_devices = int(base_devices) if base_devices is not None else None
    base_traffic_bytes = tariff.get("traffic_limit")
    base_traffic_gb_from_tariff = int(base_traffic_bytes / GB) if base_traffic_bytes else None
    current_device_limit = (
        int(current_device_limit_db)
        if current_device_limit_db is not None
        else (int(selected_device_limit_db) if selected_device_limit_db is not None else base_devices)
    )
    current_traffic_gb = (
        int(current_traffic_limit_db)
        if current_traffic_limit_db is not None
        else (int(selected_traffic_limit_db) if selected_traffic_limit_db is not None else base_traffic_gb_from_tariff)
    )
    if pack_mode and current_device_limit is not None and int(current_device_limit) == 0:
        has_device_option = False
        device_options = []
    if pack_mode and current_traffic_gb is not None and int(current_traffic_gb) == 0:
        has_traffic_option = False
        traffic_options = []
    if not has_device_option and not has_traffic_option:
        raise HTTPException(status_code=400, detail="Доп. опции для этой подписки недоступны")
    if pack_mode:
        include_device_effective = (
            bool(body.include_device) if body.include_device is not None else body.selected_device_limit is not None
        )
        include_traffic_effective = (
            bool(body.include_traffic) if body.include_traffic is not None else body.selected_traffic_gb is not None
        )
        selected_device = body.selected_device_limit if body.selected_device_limit is not None else None
        selected_traffic = body.selected_traffic_gb if body.selected_traffic_gb is not None else None
    else:
        include_device_effective = has_device_option
        include_traffic_effective = has_traffic_option
        selected_device = body.selected_device_limit if body.selected_device_limit is not None else current_device_limit
        selected_traffic = body.selected_traffic_gb if body.selected_traffic_gb is not None else current_traffic_gb
    if (
        has_device_option
        and include_device_effective
        and selected_device is not None
        and int(selected_device) not in device_options
    ):
        raise HTTPException(status_code=400, detail="Выбранный пакет устройств недоступен")
    if (
        has_traffic_option
        and include_traffic_effective
        and selected_traffic is not None
        and int(selected_traffic) not in traffic_options
    ):
        raise HTTPException(status_code=400, detail="Выбранный пакет трафика недоступен")
    current_devices_for_price = int(current_device_limit) if current_device_limit is not None else None
    current_traffic_for_price = int(current_traffic_gb) if current_traffic_gb is not None else None
    base_price_for_current = int(
        calculate_config_price(
            tariff=tariff,
            selected_device_limit=current_devices_for_price,
            selected_traffic_gb=current_traffic_for_price,
        )
    )
    total_price_after_purchase = base_price_for_current
    if pack_mode:
        diff_full = int(
            calc_pack_full_price_rub(
                tariff=tariff,
                has_device_option=bool(has_device_option and include_device_effective),
                has_traffic_option=bool(has_traffic_option and include_traffic_effective),
                selected_devices=int(selected_device)
                if has_device_option and include_device_effective and selected_device is not None
                else None,
                selected_traffic_gb=int(selected_traffic)
                if has_traffic_option and include_traffic_effective and selected_traffic is not None
                else None,
            )
        )
        recalc_enabled = bool(
            MODES_CONFIG.get(
                "KEY_ADDONS_RECALC_PRICE",
                TARIFFS_CONFIG.get("KEY_ADDONS_RECALC_PRICE", False),
            )
        )
        if recalc_enabled:
            remaining_seconds, total_seconds = calc_remaining_ratio_seconds(
                key_details.get("expiry_time"),
                tariff,
            )
            extra_price_rub = int((diff_full * remaining_seconds + total_seconds - 1) // total_seconds)
        else:
            extra_price_rub = diff_full
        total_price_after_purchase = int(base_price_for_current + diff_full)
    else:
        selected_total_price = int(
            calculate_config_price(
                tariff=tariff,
                selected_device_limit=int(selected_device)
                if has_device_option and selected_device is not None
                else None,
                selected_traffic_gb=int(selected_traffic)
                if has_traffic_option and selected_traffic is not None
                else None,
            )
        )
        extra_price_rub = int(max(0, selected_total_price - base_price_for_current))
        total_price_after_purchase = selected_total_price
        allow_downgrade = bool(TARIFFS_CONFIG.get("ALLOW_DOWNGRADE", True))
        device_downgrade = (
            allow_downgrade
            and has_device_option
            and current_device_limit is not None
            and selected_device is not None
            and not is_not_downgrade(current_device_limit, selected_device)
        )
        traffic_downgrade = (
            allow_downgrade
            and has_traffic_option
            and current_traffic_gb is not None
            and selected_traffic is not None
            and not is_not_downgrade(current_traffic_gb, selected_traffic)
        )
        if device_downgrade or traffic_downgrade:
            raise HTTPException(status_code=400, detail="Снижение параметров через сайт пока не поддерживается")
    final_extra_price_rub, discount_rub, coupon_id, applied_coupon_code = await resolve_percent_coupon_pricing(
        session=session,
        billing_user_id=int(billing_user_id),
        base_price_rub=int(max(0, extra_price_rub)),
        coupon_code=body.coupon_code,
    )
    balance = float(await get_balance(session, int(billing_user_id)))
    required_amount = int(max(0, ceil(float(final_extra_price_rub) - balance)))
    if extra_price_rub <= 0:
        return AccountKeyApplyAddonsResponse(
            ok=True,
            message="Доплата не требуется",
            client_id=str(getattr(db_key, "client_id", "") or ""),
            tariff_id=int(tariff_id),
            total_price_rub=int(total_price_after_purchase),
            extra_price_rub=0,
            discount_rub=0,
            final_price_rub=0,
            applied_coupon_code=None,
            charged_rub=0,
            balance_rub=balance,
        )
    expiry_time = int(getattr(db_key, "expiry_time", 0) or 0)
    email = str(getattr(db_key, "email", "") or "")
    server_id = str(getattr(db_key, "server_id", "") or "")
    if not email or not server_id:
        raise HTTPException(status_code=400, detail="Некорректные данные подписки")
    if required_amount > 0:
        provider_id = str(body.provider_id or _resolve_default_web_payment_provider() or "").strip().upper()
        if not provider_id:
            raise HTTPException(status_code=503, detail="Нет доступных провайдеров оплаты")
        base_url = _resolve_public_base_url(request)
        success_url = validate_redirect_url(str(body.success_url or ""), f"{base_url}/payment-success")
        failure_url = validate_redirect_url(str(body.failure_url or ""), f"{base_url}/payment-failure")
        payment_request = PaymentLinkRequest(
            legacy_user_ref=int(billing_user_id),
            amount=required_amount,
            currency="RUB",
            provider_id=provider_id,
            success_url=success_url,
            failure_url=failure_url,
            metadata={
                "payment_flow": "key_addons",
                "tariff_id": int(tariff_id),
                "email": email,
                "selected_device_limit": int(selected_device)
                if has_device_option and include_device_effective and selected_device is not None
                else None,
                "selected_traffic_gb": int(selected_traffic)
                if has_traffic_option and include_traffic_effective and selected_traffic is not None
                else None,
                "current_device_limit": int(current_device_limit) if current_device_limit is not None else None,
                "current_traffic_gb": int(current_traffic_gb) if current_traffic_gb is not None else None,
                "original_price": int(base_price_for_current),
                "base_price_rub": int(max(0, extra_price_rub)),
                "discount_rub": int(discount_rub),
                "applied_coupon_code": applied_coupon_code,
                "coupon_id": int(coupon_id) if coupon_id is not None else None,
            },
        )
        payment_result = await create_payment_link(session, payment_request)
        if not payment_result.success or not payment_result.payment_url or not payment_result.payment_id:
            raise HTTPException(status_code=400, detail=payment_result.error or "Не удалось создать ссылку оплаты")
        await create_temporary_data(
            session,
            int(billing_user_id),
            "waiting_for_addons_payment",
            {
                "tariff_id": int(tariff_id),
                "email": email,
                "required_amount": int(required_amount),
                "selected_device_limit": int(selected_device)
                if has_device_option and include_device_effective and selected_device is not None
                else None,
                "selected_traffic_gb": int(selected_traffic)
                if has_traffic_option and include_traffic_effective and selected_traffic is not None
                else None,
                "current_device_limit": int(current_device_limit) if current_device_limit is not None else None,
                "current_traffic_gb": int(current_traffic_gb) if current_traffic_gb is not None else None,
                "original_price": int(base_price_for_current),
                "base_price_rub": int(max(0, extra_price_rub)),
                "discount_rub": int(discount_rub),
                "applied_coupon_code": applied_coupon_code,
                "coupon_id": int(coupon_id) if coupon_id is not None else None,
            },
        )
        return AccountKeyApplyAddonsResponse(
            ok=True,
            message="Требуется оплата для применения доп. опций",
            client_id=str(getattr(db_key, "client_id", "") or ""),
            tariff_id=int(tariff_id),
            total_price_rub=int(total_price_after_purchase),
            extra_price_rub=int(extra_price_rub),
            discount_rub=int(discount_rub),
            final_price_rub=int(final_extra_price_rub),
            applied_coupon_code=applied_coupon_code,
            charged_rub=0,
            balance_rub=balance,
            payment_required=True,
            required_amount_rub=required_amount,
            payment_id=payment_result.payment_id,
            payment_url=payment_result.payment_url,
        )
    target_subgroup = tariff.get("subgroup_title")
    current_subgroup = None
    current_tariff_id = key_details.get("tariff_id")
    if current_tariff_id:
        current_tariff = await get_tariff_by_id(session, int(current_tariff_id))
        if current_tariff:
            current_subgroup = current_tariff.get("subgroup_title")
    if pack_mode:
        device_limit_effective_current, traffic_limit_bytes_effective_current = await get_effective_limits_for_key(
            session=session,
            tariff_id=int(tariff_id),
            selected_device_limit=int(current_device_limit) if current_device_limit is not None else None,
            selected_traffic_gb=int(current_traffic_gb) if current_traffic_gb is not None else None,
        )
        traffic_limit_gb_effective_current = (
            int(traffic_limit_bytes_effective_current / GB) if traffic_limit_bytes_effective_current else 0
        )
        new_device_limit_effective = device_limit_effective_current
        new_traffic_limit_gb_effective = traffic_limit_gb_effective_current
        if has_device_option and include_device_effective and selected_device is not None:
            pack_devices_val = int(selected_device)
            if pack_devices_val <= 0 or (
                new_device_limit_effective is not None and int(new_device_limit_effective) <= 0
            ):
                new_device_limit_effective = 0
            else:
                if new_device_limit_effective is None:
                    new_device_limit_effective = pack_devices_val
                else:
                    new_device_limit_effective = int(new_device_limit_effective) + pack_devices_val
        if has_traffic_option and include_traffic_effective and selected_traffic is not None:
            pack_traffic_val = int(selected_traffic)
            if pack_traffic_val <= 0 or int(new_traffic_limit_gb_effective) <= 0:
                new_traffic_limit_gb_effective = 0
            else:
                new_traffic_limit_gb_effective = int(new_traffic_limit_gb_effective) + pack_traffic_val
        await renew_key_in_cluster(
            cluster_id=server_id,
            email=email,
            client_id=str(getattr(db_key, "client_id", "") or ""),
            new_expiry_time=expiry_time,
            total_gb=int(new_traffic_limit_gb_effective),
            session=session,
            hwid_device_limit=int(new_device_limit_effective) if new_device_limit_effective is not None else 0,
            reset_traffic=False,
            target_subgroup=target_subgroup,
            old_subgroup=current_subgroup,
            plan=int(tariff_id),
        )
        await save_key_config_with_mode(
            session=session,
            email=email,
            selected_devices=int(new_device_limit_effective) if new_device_limit_effective is not None else None,
            selected_traffic_gb=int(new_traffic_limit_gb_effective)
            if new_traffic_limit_gb_effective is not None
            else None,
            total_price=int(total_price_after_purchase),
            has_device_choice=bool(has_device_option and include_device_effective),
            has_traffic_choice=bool(has_traffic_option and include_traffic_effective),
            config_mode="pack",
        )
    else:
        selected_device_for_effective = (
            int(selected_device)
            if has_device_option and include_device_effective and selected_device is not None
            else None
        )
        selected_traffic_for_effective = (
            int(selected_traffic)
            if has_traffic_option and include_traffic_effective and selected_traffic is not None
            else 0
        )
        device_limit_effective_new, traffic_limit_bytes_effective_new = await get_effective_limits_for_key(
            session=session,
            tariff_id=int(tariff_id),
            selected_device_limit=selected_device_for_effective,
            selected_traffic_gb=selected_traffic_for_effective,
        )
        traffic_limit_gb_effective = (
            int(traffic_limit_bytes_effective_new / GB) if traffic_limit_bytes_effective_new else 0
        )
        await renew_key_in_cluster(
            cluster_id=server_id,
            email=email,
            client_id=str(getattr(db_key, "client_id", "") or ""),
            new_expiry_time=expiry_time,
            total_gb=int(traffic_limit_gb_effective),
            session=session,
            hwid_device_limit=int(device_limit_effective_new) if device_limit_effective_new is not None else 0,
            reset_traffic=False,
            target_subgroup=target_subgroup,
            old_subgroup=current_subgroup,
            plan=int(tariff_id),
        )
        await save_key_config_with_mode(
            session=session,
            email=email,
            selected_devices=int(selected_device)
            if has_device_option and include_device_effective and selected_device is not None
            else None,
            selected_traffic_gb=int(selected_traffic)
            if has_traffic_option and include_traffic_effective and selected_traffic is not None
            else None,
            total_price=int(total_price_after_purchase),
            has_device_choice=bool(has_device_option and include_device_effective),
            has_traffic_choice=bool(has_traffic_option and include_traffic_effective),
            config_mode="addon",
        )
    if int(final_extra_price_rub) > 0:
        debited = await update_balance(session, int(billing_user_id), -int(final_extra_price_rub))
        if debited is None:
            raise HTTPException(status_code=402, detail="Недостаточно средств на балансе")
    if coupon_id is not None:
        await mark_coupon_used(session, int(coupon_id), int(billing_user_id))
    return AccountKeyApplyAddonsResponse(
        ok=True,
        message="Доп. опции применены",
        client_id=str(getattr(db_key, "client_id", "") or ""),
        tariff_id=int(tariff_id),
        total_price_rub=int(total_price_after_purchase),
        extra_price_rub=int(extra_price_rub),
        discount_rub=int(discount_rub),
        final_price_rub=int(final_extra_price_rub),
        applied_coupon_code=applied_coupon_code,
        charged_rub=int(final_extra_price_rub),
        balance_rub=float(await get_balance(session, int(billing_user_id))),
    )

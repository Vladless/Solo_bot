"""User-facing key endpoints (/api/keys/*).

Регистрирует эндпоинты на ``user_router`` из ``_common``. Импорт этого модуля
из ``__init__.py`` запускает регистрацию декораторов.
"""

from .._common import *  # noqa: F401,F403 — подтягиваем все имена для endpoints
from .._common import (
    _is_renew_available,
    _key_actions_config,
    _normalize_expiry_ms,
    _renew_available_from_ms,
    _resolve_available_location_servers,
    _resolve_billing_user_id,
    _resolve_default_web_payment_provider,
    _resolve_public_base_url,
    router,
    user_router,
)


@user_router.post("/{client_id}/renew", response_model=AccountKeyRenewResponse)
async def user_key_renew(
    client_id: str,
    body: AccountKeyRenewRequest,
    request: Request,
    force_web: bool = Query(False),
    preview: bool = Query(False),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    from api.ratelimit import enforce_rate_limit
    from services.errors import ServiceError
    from services.keys import (
        calculate_renewal_pricing,
        execute_renewal,
        normalize_expiry_ms as _svc_normalize_expiry,
    )

    if not preview:
        await enforce_rate_limit(request, session, bucket="key_renew", max_per_window=10, window_sec=60)

    actions = _key_actions_config()
    if not force_web and not actions.renew_enabled:
        raise HTTPException(status_code=403, detail="Продление подписки отключено в настройках")
    billing_user_id = await _resolve_billing_user_id(request, identity, session)
    db_key = (
        await session.execute(select(Key).where(Key.user_id == billing_user_id, Key.client_id == client_id).limit(1))
    ).scalar_one_or_none()
    if db_key is None:
        raise HTTPException(status_code=404, detail="Подписка не найдена")
    if bool(getattr(db_key, "is_frozen", False)):
        raise HTTPException(status_code=400, detail="Продление для замороженной подписки недоступно")
    tariff_id = getattr(db_key, "tariff_id", None)
    is_tariff_switch = body.tariff_id is not None and int(body.tariff_id or 0) != int(tariff_id or 0)
    key_expiry_ms = _svc_normalize_expiry(getattr(db_key, "expiry_time", None))
    if key_expiry_ms and not is_tariff_switch and not _is_renew_available(int(key_expiry_ms)):
        available_msk = datetime.fromtimestamp(
            _renew_available_from_ms(int(key_expiry_ms)) / 1000, tz=timezone(timedelta(hours=3))
        )
        raise HTTPException(
            status_code=403,
            detail=f"Продление доступно с {available_msk.strftime('%d.%m.%Y %H:%M')}",
        )
    if not tariff_id:
        raise HTTPException(status_code=400, detail="Для подписки не назначен тариф")
    key_email = str(getattr(db_key, "email", "") or "")
    key_server_id = str(getattr(db_key, "server_id", "") or "")

    forbidden_renewal_groups = {"trial", "discounts", "discounts_max", "cold_discounts", "cold_discounts_max", "gifts"}
    server_tariff_group_row = await session.execute(
        select(Server.tariff_group)
        .where((Server.server_name == key_server_id) | (Server.cluster_name == key_server_id))
        .limit(1)
    )
    server_tariff_group = (server_tariff_group_row.scalar() or "").strip()

    if body.tariff_id:
        chosen_tariff = await get_tariff_by_id(session, int(body.tariff_id))
        if not chosen_tariff or not chosen_tariff.get("is_active", True):
            raise HTTPException(status_code=404, detail="Тариф не найден")
        chosen_group_code = (chosen_tariff.get("group_code") or "").strip()
        if (
            not chosen_group_code
            or chosen_group_code in forbidden_renewal_groups
            or (server_tariff_group and chosen_group_code != server_tariff_group)
        ):
            raise HTTPException(status_code=400, detail="Тариф недоступен для этой подписки")
        effective_tariff_id = int(body.tariff_id)
    else:
        key_tariff = await get_tariff_by_id(session, int(tariff_id))
        key_tariff_group = (key_tariff.get("group_code") or "").strip() if key_tariff else ""
        if not key_tariff_group or key_tariff_group in forbidden_renewal_groups:
            return AccountKeyRenewResponse(
                ok=True,
                message="Для продления выберите тариф",
                client_id=str(client_id),
                tariff_id=0,
                requires_tariff_selection=True,
                available_tariff_group=server_tariff_group or None,
                payment_required=False,
            )
        effective_tariff_id = int(tariff_id)

    try:
        pricing = await calculate_renewal_pricing(
            session=session,
            billing_user_id=int(billing_user_id),
            key_email=key_email,
            tariff_id=effective_tariff_id,
            coupon_code=body.coupon_code,
            selected_device_limit=body.selected_device_limit,
            selected_traffic_limit=body.selected_traffic_limit,
        )
    except ServiceError as e:
        raise HTTPException(status_code=400, detail=e.message)

    from services.keys import compute_renewal_quote

    quote = await compute_renewal_quote(
        session,
        billing_user_id=int(billing_user_id),
        key_email=key_email,
        current_tariff_id=getattr(db_key, "tariff_id", None),
        current_selected_device=getattr(db_key, "selected_device_limit", None),
        current_selected_traffic=getattr(db_key, "selected_traffic_limit", None),
        current_expiry_ms=_normalize_expiry_ms(getattr(db_key, "expiry_time", None)),
        now_ms=int(datetime.utcnow().timestamp() * 1000),
        new_tariff_id=effective_tariff_id,
        new_selected_device=body.selected_device_limit,
        new_selected_traffic=body.selected_traffic_limit,
        coupon_code=body.coupon_code,
    )
    net_cost = quote.net_cost_rub
    required_amount = max(0, int(round(net_cost - pricing.balance)))
    payment_required = required_amount > 0

    if preview:
        return AccountKeyRenewResponse(
            ok=True,
            message="Расчет обновлен",
            client_id=str(client_id),
            tariff_id=effective_tariff_id,
            charged_rub=0,
            balance_rub=pricing.balance,
            base_price_rub=pricing.base_price_rub,
            discount_rub=pricing.discount_rub,
            final_price_rub=net_cost,
            applied_coupon_code=pricing.applied_coupon_code,
            payment_required=payment_required,
            required_amount_rub=required_amount,
            payment_id=None,
            payment_url=None,
            is_switch=quote.is_switch,
            keeps_period=quote.keeps_period,
            credit_to_balance_rub=quote.credit_rub,
            refund_to_balance_rub=quote.refund_to_balance_rub,
            credit_days=quote.credit_days,
            credit_value_rub=quote.credit_value_rub,
            new_device_limit=quote.selected_device_limit,
            new_traffic_gb=quote.total_gb,
            carried_addons_rub=quote.carried_addons_rub,
            carried_device_limit=quote.carried_device_limit,
            carried_traffic_gb=quote.carried_traffic_limit,
        )

    if payment_required:
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
                "payment_flow": "key_renewal",
                "tariff_id": effective_tariff_id,
                "client_id": str(client_id),
                "email": key_email,
                "cost": net_cost,
                "selected_duration_days": quote.duration_days,
                "selected_device_limit": quote.selected_device_limit,
                "selected_traffic_limit": quote.selected_traffic_limit,
                "selected_price_rub": quote.new_full_price_rub,
                "total_gb": quote.total_gb,
                "base_price_rub": pricing.base_price_rub,
                "discount_rub": pricing.discount_rub,
                "applied_coupon_code": quote.applied_coupon_code,
                "coupon_id": quote.coupon_id,
            },
        )
        payment_result = await create_payment_link(session, payment_request)
        if not payment_result.success or not payment_result.payment_url or not payment_result.payment_id:
            raise HTTPException(status_code=400, detail=payment_result.error or "Не удалось создать ссылку оплаты")
        await create_temporary_data(
            session,
            int(billing_user_id),
            "waiting_for_renewal_payment",
            {
                "tariff_id": effective_tariff_id,
                "client_id": str(client_id),
                "email": key_email,
                "cost": net_cost,
                "required_amount": required_amount,
                "new_expiry_time": int(quote.new_expiry_ms),
                "selected_duration_days": quote.duration_days,
                "selected_device_limit": quote.selected_device_limit,
                "selected_traffic_limit": quote.selected_traffic_limit,
                "selected_price_rub": quote.new_full_price_rub,
                "total_gb": quote.total_gb,
                "base_price_rub": pricing.base_price_rub,
                "discount_rub": pricing.discount_rub,
                "applied_coupon_code": quote.applied_coupon_code,
                "coupon_id": quote.coupon_id,
            },
        )
        return AccountKeyRenewResponse(
            ok=True,
            message="Требуется оплата для продления подписки",
            client_id=str(client_id),
            tariff_id=effective_tariff_id,
            charged_rub=0,
            balance_rub=pricing.balance,
            base_price_rub=pricing.base_price_rub,
            discount_rub=pricing.discount_rub,
            final_price_rub=net_cost,
            applied_coupon_code=pricing.applied_coupon_code,
            payment_required=True,
            required_amount_rub=required_amount,
            payment_id=payment_result.payment_id,
            payment_url=payment_result.payment_url,
            is_switch=quote.is_switch,
            keeps_period=quote.keeps_period,
            credit_to_balance_rub=quote.credit_rub,
            refund_to_balance_rub=quote.refund_to_balance_rub,
            credit_days=quote.credit_days,
            credit_value_rub=quote.credit_value_rub,
            new_device_limit=quote.selected_device_limit,
            new_traffic_gb=quote.total_gb,
        )
    if not key_email or not key_server_id:
        raise HTTPException(status_code=400, detail="Некорректные данные подписки")
    try:
        result = await execute_renewal(
            session=session,
            billing_user_id=int(billing_user_id),
            client_id=str(client_id),
            key_email=key_email,
            key_server_id=key_server_id,
            tariff_id=effective_tariff_id,
            new_expiry_time=int(quote.new_expiry_ms),
            total_gb=quote.total_gb,
            cost=float(net_cost),
            selected_device_limit=quote.selected_device_limit,
            selected_traffic_limit=quote.selected_traffic_limit,
            selected_price_rub=quote.new_full_price_rub,
            coupon_id=quote.coupon_id,
        )
    except ServiceError as e:
        raise HTTPException(status_code=400, detail=e.message)
    return AccountKeyRenewResponse(
        ok=True,
        message="Подписка продлена",
        client_id=result.client_id,
        tariff_id=result.tariff_id,
        charged_rub=result.charged_rub,
        balance_rub=result.balance_rub,
        base_price_rub=pricing.base_price_rub,
        discount_rub=pricing.discount_rub,
        final_price_rub=net_cost,
        applied_coupon_code=pricing.applied_coupon_code,
        is_switch=quote.is_switch,
        keeps_period=quote.keeps_period,
        credit_to_balance_rub=quote.credit_rub,
        refund_to_balance_rub=quote.refund_to_balance_rub,
        credit_days=quote.credit_days,
        credit_value_rub=quote.credit_value_rub,
        new_device_limit=quote.selected_device_limit,
        new_traffic_gb=quote.total_gb,
    )

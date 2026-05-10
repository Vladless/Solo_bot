"""User-facing key endpoints (/api/keys/*).

Регистрирует эндпоинты на ``user_router`` из ``_common``. Импорт этого модуля
из ``__init__.py`` запускает регистрацию декораторов.
"""

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
    db_tariff_id = getattr(db_key, "tariff_id", None)
    # Клиент может явно указать тариф (например, чтобы продлить с другим
    # сроком). Тогда валидируем, что он активен и относится к той же
    # group_code/subgroup_title, что и текущий тариф ключа — ровно так же,
    # как процессит выбор тарифа Telegram-handler `process_callback_renew_plan`.
    if body.tariff_id is not None and int(body.tariff_id) != int(db_tariff_id or 0):
        requested = await get_tariff_by_id(session, int(body.tariff_id))
        if not requested or not requested.get("is_active", True):
            raise HTTPException(status_code=404, detail="Тариф не найден")
        current = await get_tariff_by_id(session, int(db_tariff_id or 0)) if db_tariff_id else None
        if current and (
            (requested.get("group_code") or "") != (current.get("group_code") or "")
            or (requested.get("subgroup_title") or "") != (current.get("subgroup_title") or "")
        ):
            raise HTTPException(status_code=400, detail="Тариф недоступен для этой подписки")
        tariff_id = int(body.tariff_id)
    else:
        tariff_id = db_tariff_id
    if not tariff_id:
        raise HTTPException(status_code=400, detail="Для подписки не назначен тариф")
    key_email = str(getattr(db_key, "email", "") or "")
    key_server_id = str(getattr(db_key, "server_id", "") or "")

    try:
        pricing = await calculate_renewal_pricing(
            session=session,
            billing_user_id=int(billing_user_id),
            key_email=key_email,
            tariff_id=int(tariff_id),
            coupon_code=body.coupon_code,
        )
    except ServiceError as e:
        raise HTTPException(status_code=400, detail=e.message)

    if preview:
        return AccountKeyRenewResponse(
            ok=True,
            message="Расчет обновлен",
            client_id=str(client_id),
            tariff_id=int(tariff_id),
            charged_rub=0,
            balance_rub=pricing.balance,
            base_price_rub=pricing.base_price_rub,
            discount_rub=pricing.discount_rub,
            final_price_rub=pricing.final_price_rub,
            applied_coupon_code=pricing.applied_coupon_code,
            payment_required=pricing.payment_required,
            required_amount_rub=pricing.required_amount,
            payment_id=None,
            payment_url=None,
        )
    if pricing.payment_required:
        provider_id = str(body.provider_id or _resolve_default_web_payment_provider() or "").strip().upper()
        if not provider_id:
            raise HTTPException(status_code=503, detail="Нет доступных провайдеров оплаты")
        base_url = _resolve_public_base_url(request)
        success_url = validate_redirect_url(str(body.success_url or ""), f"{base_url}/payment-success")
        failure_url = validate_redirect_url(str(body.failure_url or ""), f"{base_url}/payment-failure")
        payment_request = PaymentLinkRequest(
            legacy_user_ref=int(billing_user_id),
            amount=pricing.required_amount,
            currency="RUB",
            provider_id=provider_id,
            success_url=success_url,
            failure_url=failure_url,
            metadata={
                "payment_flow": "key_renewal",
                "tariff_id": int(tariff_id),
                "client_id": str(client_id),
                "email": key_email,
                "cost": pricing.final_price_rub,
                "selected_duration_days": pricing.duration_days,
                "selected_device_limit": pricing.selected_device_limit,
                "selected_traffic_limit": pricing.selected_traffic_limit,
                "selected_price_rub": pricing.final_price_rub,
                "total_gb": pricing.total_gb,
                "base_price_rub": pricing.base_price_rub,
                "discount_rub": pricing.discount_rub,
                "applied_coupon_code": pricing.applied_coupon_code,
                "coupon_id": pricing.coupon_id,
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
                "tariff_id": int(tariff_id),
                "client_id": str(client_id),
                "email": key_email,
                "cost": pricing.final_price_rub,
                "required_amount": pricing.required_amount,
                "selected_duration_days": pricing.duration_days,
                "selected_device_limit": pricing.selected_device_limit,
                "selected_traffic_limit": pricing.selected_traffic_limit,
                "selected_price_rub": pricing.final_price_rub,
                "total_gb": pricing.total_gb,
                "base_price_rub": pricing.base_price_rub,
                "discount_rub": pricing.discount_rub,
                "applied_coupon_code": pricing.applied_coupon_code,
                "coupon_id": pricing.coupon_id,
            },
        )
        return AccountKeyRenewResponse(
            ok=True,
            message="Требуется оплата для продления подписки",
            client_id=str(client_id),
            tariff_id=int(tariff_id),
            charged_rub=0,
            balance_rub=pricing.balance,
            base_price_rub=pricing.base_price_rub,
            discount_rub=pricing.discount_rub,
            final_price_rub=pricing.final_price_rub,
            applied_coupon_code=pricing.applied_coupon_code,
            payment_required=True,
            required_amount_rub=pricing.required_amount,
            payment_id=payment_result.payment_id,
            payment_url=payment_result.payment_url,
        )
    expiry_raw = _normalize_expiry_ms(getattr(db_key, "expiry_time", None))
    now_ms = int(datetime.utcnow().timestamp() * 1000)
    base_expiry = now_ms if expiry_raw <= now_ms else expiry_raw
    new_expiry_time = int(base_expiry + pricing.duration_days * 24 * 60 * 60 * 1000)
    if not key_email or not key_server_id:
        raise HTTPException(status_code=400, detail="Некорректные данные подписки")
    try:
        result = await execute_renewal(
            session=session,
            billing_user_id=int(billing_user_id),
            client_id=str(client_id),
            key_email=key_email,
            key_server_id=key_server_id,
            tariff_id=int(tariff_id),
            new_expiry_time=new_expiry_time,
            total_gb=pricing.total_gb,
            cost=float(pricing.final_price_rub),
            selected_device_limit=pricing.selected_device_limit,
            selected_traffic_limit=pricing.selected_traffic_limit,
            selected_price_rub=pricing.final_price_rub,
            coupon_id=pricing.coupon_id,
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
        final_price_rub=pricing.final_price_rub,
        applied_coupon_code=pricing.applied_coupon_code,
    )

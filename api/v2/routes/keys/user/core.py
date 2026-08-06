"""User-facing key endpoints (/api/keys/*).

Регистрирует эндпоинты на ``user_router`` из ``_common``. Импорт этого модуля
из ``__init__.py`` запускает регистрацию декораторов.
"""

import time

from .._common import *  # noqa: F401,F403 — подтягиваем все имена для endpoints
from .._common import (
    _is_renew_available,
    _key_actions_config,
    _normalize_expiry_ms,
    _resolve_available_location_servers,
    _resolve_billing_user_id,
    _resolve_default_web_payment_provider,
    _resolve_public_base_url,
    router,
    user_router,
)


@user_router.get("", response_model=list[AccountKeyResponse])
async def user_keys(
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    from core.redis_cache import cache_get, cache_key, cache_set

    billing_user_id = await _resolve_billing_user_id(request, identity, session)
    keys = await get_keys(session, billing_user_id)
    result: list[AccountKeyResponse] = []
    for key in keys:
        key_actions = AccountKeyActionsAvailability()
        key_ref = str(getattr(key, "client_id", "") or getattr(key, "email", "") or "")
        actions_key = cache_key("key_actions", key_ref)
        cached_actions = await cache_get(actions_key)
        if isinstance(cached_actions, dict):
            try:
                key_actions = AccountKeyActionsAvailability.model_validate(cached_actions)
            except Exception:
                cached_actions = None
        if not isinstance(cached_actions, dict):
            try:
                _, markup, _ = await build_key_view_payload(session, int(billing_user_id), key_ref)
                key_actions = _extract_key_actions_from_markup(markup)
                await cache_set(actions_key, key_actions.model_dump(), 60)
            except Exception:
                key_actions = AccountKeyActionsAvailability()
        if key_actions.can_renew and not _is_renew_available(int(getattr(key, "expiry_time", 0) or 0)):
            key_actions.can_renew = False
        result.append(
            AccountKeyResponse(
                email=str(getattr(key, "email", "") or ""),
                alias=getattr(key, "alias", None),
                client_id=str(getattr(key, "client_id", "") or ""),
                tariff_id=getattr(key, "tariff_id", None),
                server_id=str(getattr(key, "server_id", "") or ""),
                created_at=int(getattr(key, "created_at", 0) or 0),
                expiry_time=int(getattr(key, "expiry_time", 0) or 0),
                key=getattr(key, "key", None),
                remnawave_link=getattr(key, "remnawave_link", None),
                is_frozen=bool(getattr(key, "is_frozen", False)),
                actions=key_actions,
            )
        )
    return result


@user_router.get("/actions-config", response_model=AccountKeyActionsConfigResponse)
async def user_keys_actions_config(
    identity=Depends(verify_identity_token),
):
    _ = identity
    return _key_actions_config()


@user_router.get("/{client_id}/connection", response_model=AccountKeyConnectionResponse)
async def user_key_connection(
    client_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    """Лёгкая инфо о текущей подписке: онлайн/offline, сервер, протокол, дни до окончания."""
    billing_user_id = await _resolve_billing_user_id(request, identity, session)
    db_key = next(
        (k for k in await get_keys(session, billing_user_id) if str(getattr(k, "client_id", "")) == client_id),
        None,
    )
    if db_key is None:
        raise HTTPException(status_code=404, detail="Подписка не найдена")
    server_name = str(getattr(db_key, "server_id", "") or "")
    cluster_name = ""
    panel_type = ""
    if server_name:
        from database.servers import get_servers

        grouped = await get_servers(session, include_enabled=True)
        for cl_name, servers in grouped.items():
            for s in servers:
                if s.get("server_name") == server_name:
                    cluster_name = str(cl_name or "")
                    panel_type = str(s.get("panel_type") or "").lower()
                    break
            if panel_type or cluster_name:
                break
    expiry_ms = int(getattr(db_key, "expiry_time", 0) or 0)
    is_frozen = bool(getattr(db_key, "is_frozen", False))
    now_ms = int(time.time() * 1000)
    online = not is_frozen and expiry_ms > now_ms
    expires_in_days = max(0, int((expiry_ms - now_ms) / (1000 * 60 * 60 * 24))) if expiry_ms > 0 else 0

    is_online: bool | None = None
    online_at: str | None = None
    connected_devices: int | None = None
    if panel_type == "remnawave" and not is_frozen:
        try:
            from panels.remnawave_runtime import get_remnawave_profile

            profile = await get_remnawave_profile(
                session, server_name, client_id, fallback_any=True,
                username=str(getattr(key, "email", "") or "") or None,
            )
            if profile:
                is_online = bool(profile.get("is_online"))
                raw_online_at = profile.get("online_at")
                online_at = str(raw_online_at) if raw_online_at else None
                hwid = profile.get("hwid_count")
                connected_devices = int(hwid) if isinstance(hwid, int) else None
        except Exception:
            pass
    if panel_type == "remnawave":
        protocol = "VLESS"
    elif panel_type == "marzban":
        protocol = "VLESS"
    elif panel_type == "3xui":
        protocol = "VLESS"
    else:
        protocol = panel_type.upper() or "VLESS"
    return AccountKeyConnectionResponse(
        client_id=str(getattr(db_key, "client_id", "") or ""),
        online=online,
        is_frozen=is_frozen,
        expiry_time=expiry_ms,
        expires_in_days=expires_in_days,
        server_name=server_name,
        cluster_name=cluster_name,
        panel_type=panel_type,
        protocol=protocol,
        is_online=is_online,
        online_at=online_at,
        connected_devices=connected_devices,
    )


@user_router.get("/{client_id}/traffic-history")
async def user_key_traffic_history(
    client_id: str,
    request: Request,
    days: int = Query(default=30, ge=1, le=365),
    granularity: str = Query(default="day"),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    """История использования трафика для графика в кабинете.

    granularity=day — по дням (по умолчанию); granularity=hour — по часам за сутки.
    """
    from api.ratelimit import enforce_rate_limit

    await enforce_rate_limit(request, session, bucket="traffic_history", max_per_window=60, window_sec=60)
    billing_user_id = await _resolve_billing_user_id(request, identity, session)
    owns = (
        await session.execute(
            select(Key.client_id).where(Key.user_id == billing_user_id, Key.client_id == client_id).limit(1)
        )
    ).scalar_one_or_none()
    if owns is None:
        raise HTTPException(status_code=404, detail="Подписка не найдена")

    if granularity == "hour":
        from services.traffic_history import get_traffic_history_hourly

        points = await get_traffic_history_hourly(session, client_id, hours=24)
        return {"client_id": client_id, "days": 1, "granularity": "hour", "points": points}

    from services.traffic_history import get_traffic_history

    points = await get_traffic_history(session, client_id, days)
    return {"client_id": client_id, "days": days, "granularity": "day", "points": points}


@user_router.get("/{client_id}/details", response_model=AccountKeyDetailsResponse)
async def user_key_details(
    client_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    billing_user_id = await _resolve_billing_user_id(request, identity, session)
    db_key = (
        await session.execute(select(Key).where(Key.user_id == billing_user_id, Key.client_id == client_id).limit(1))
    ).scalar_one_or_none()
    if db_key is None:
        raise HTTPException(status_code=404, detail="Подписка не найдена")
    key_details = await get_key_details(session, str(getattr(db_key, "email", "") or ""))
    if not key_details:
        raise HTTPException(status_code=404, detail="Подписка не найдена")
    tariff_name = ""
    subgroup_title = ""
    traffic_limit_gb = 0
    device_limit = 0
    is_tariff_configurable = False
    addons_devices_enabled = False
    addons_traffic_enabled = False
    (
        tariff_name,
        subgroup_title,
        traffic_limit_gb,
        device_limit,
        _,
        is_tariff_configurable,
        addons_devices_enabled,
        addons_traffic_enabled,
    ) = await get_key_tariff_addons_state(
        session=session,
        key_record=key_details,
        db_key=db_key,
    )
    connected_devices = 0
    used_traffic_gb = None
    try:
        profile = await get_remnawave_profile(
            session,
            str(getattr(db_key, "server_id", "") or ""),
            client_id,
            fallback_any=True,
            username=str(getattr(db_key, "email", "") or "") or None,
        )
        if profile:
            connected_devices = int(profile.get("hwid_count") or 0)
            used_raw = profile.get("used_gb")
            used_traffic_gb = float(used_raw) if used_raw is not None else None
            traffic_limit_bytes_actual = profile.get("traffic_limit_bytes")
            if traffic_limit_bytes_actual is not None:
                try:
                    traffic_limit_bytes_actual = int(traffic_limit_bytes_actual)
                    traffic_limit_gb = int(traffic_limit_bytes_actual / GB) if traffic_limit_bytes_actual > 0 else 0
                except (TypeError, ValueError):
                    pass
    except Exception:
        connected_devices = 0
        used_traffic_gb = None
    return AccountKeyDetailsResponse(
        client_id=str(getattr(db_key, "client_id", "") or ""),
        email=str(getattr(db_key, "email", "") or ""),
        alias=getattr(db_key, "alias", None),
        expiry_time=int(getattr(db_key, "expiry_time", 0) or 0),
        is_frozen=bool(getattr(db_key, "is_frozen", False)),
        tariff_name=str(tariff_name or ""),
        subgroup_title=str(subgroup_title or ""),
        traffic_limit_gb=int(traffic_limit_gb or 0),
        used_traffic_gb=used_traffic_gb,
        device_limit=int(device_limit or 0),
        connected_devices=int(connected_devices or 0),
        is_tariff_configurable=bool(is_tariff_configurable),
        addons_devices_enabled=bool(addons_devices_enabled),
        addons_traffic_enabled=bool(addons_traffic_enabled),
        can_renew=_is_renew_available(int(getattr(db_key, "expiry_time", 0) or 0)),
    )


@user_router.get("/{client_id}/qr", response_model=AccountKeyQrResponse)
async def user_key_qr(
    client_id: str,
    request: Request,
    force_web: bool = Query(False),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    from api.ratelimit import enforce_rate_limit

    await enforce_rate_limit(request, session, bucket="key_qr", max_per_window=30, window_sec=60)
    actions = _key_actions_config()
    if not force_web and not actions.qr_enabled:
        raise HTTPException(status_code=403, detail="QR для подписок отключен в настройках")
    billing_user_id = await _resolve_billing_user_id(request, identity, session)
    db_key = (
        await session.execute(select(Key).where(Key.user_id == billing_user_id, Key.client_id == client_id).limit(1))
    ).scalar_one_or_none()
    if db_key is None:
        raise HTTPException(status_code=404, detail="Подписка не найдена")
    qr_data = str(getattr(db_key, "key", "") or "").strip() or str(getattr(db_key, "remnawave_link", "") or "").strip()
    if not qr_data:
        raise HTTPException(status_code=400, detail="Ссылка для подключения отсутствует")
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(qr_data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    image_data = b64encode(buffer.getvalue()).decode("ascii")
    return AccountKeyQrResponse(
        ok=True,
        message="QR-код готов",
        link=qr_data,
        image_data_url=f"data:image/png;base64,{image_data}",
    )


@user_router.patch("/{client_id}/alias", response_model=AccountKeyResponse)
async def user_key_update_alias(
    client_id: str,
    body: AccountKeyAliasUpdateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    from api.ratelimit import enforce_rate_limit

    await enforce_rate_limit(request, session, bucket="key_alias", max_per_window=20, window_sec=60)
    alias = str(body.alias or "").strip()
    if not alias:
        raise HTTPException(status_code=400, detail="Укажите alias")
    if len(alias) > 10:
        raise HTTPException(status_code=400, detail="Alias должен быть не длиннее 10 символов")
    if not re.match(r"^[a-zA-Zа-яА-ЯёЁ0-9@._-]+$", alias):
        raise HTTPException(status_code=400, detail="Alias содержит недопустимые символы")
    billing_user_id = await _resolve_billing_user_id(request, identity, session)
    db_key = (
        await session.execute(select(Key).where(Key.user_id == billing_user_id, Key.client_id == client_id).limit(1))
    ).scalar_one_or_none()
    if db_key is None:
        raise HTTPException(status_code=404, detail="Подписка не найдена")
    db_key.alias = alias
    await session.flush()
    from database.keys import invalidate_key_details, invalidate_keys_list

    await invalidate_keys_list(session, billing_user_id)
    await invalidate_key_details(str(getattr(db_key, "email", "") or ""))
    return AccountKeyResponse(
        email=str(getattr(db_key, "email", "") or ""),
        alias=getattr(db_key, "alias", None),
        client_id=str(getattr(db_key, "client_id", "") or ""),
        tariff_id=getattr(db_key, "tariff_id", None),
        server_id=str(getattr(db_key, "server_id", "") or ""),
        created_at=int(getattr(db_key, "created_at", 0) or 0),
        expiry_time=int(getattr(db_key, "expiry_time", 0) or 0),
        key=getattr(db_key, "key", None),
        remnawave_link=getattr(db_key, "remnawave_link", None),
        is_frozen=bool(getattr(db_key, "is_frozen", False)),
    )


@user_router.delete("/{client_id}", response_model=AccountKeyActionResponse)
async def user_key_delete(
    client_id: str,
    request: Request,
    force_web: bool = Query(False),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    from api.ratelimit import enforce_rate_limit

    await enforce_rate_limit(request, session, bucket="key_delete", max_per_window=10, window_sec=60)
    actions = _key_actions_config()
    if not force_web and not actions.delete_enabled:
        raise HTTPException(status_code=403, detail="Удаление подписки отключено в настройках")
    billing_user_id = await _resolve_billing_user_id(request, identity, session)
    db_key = (
        await session.execute(select(Key).where(Key.user_id == billing_user_id, Key.client_id == client_id).limit(1))
    ).scalar_one_or_none()
    if db_key is None:
        raise HTTPException(status_code=404, detail="Подписка не найдена")
    cluster_id = str(getattr(db_key, "server_id", "") or "")
    email = str(getattr(db_key, "email", "") or "")
    if cluster_id and email:
        await delete_key_from_cluster(
            cluster_id=cluster_id,
            email=email,
            client_id=client_id,
            session=session,
        )
    await session.delete(db_key)
    return AccountKeyActionResponse(ok=True, message="Подписка удалена")

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


@user_router.get("/{client_id}/locations", response_model=AccountKeyLocationsResponse)
async def user_key_locations(
    client_id: str,
    request: Request,
    force_web: bool = Query(False),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    actions = _key_actions_config()
    if not force_web and not actions.country_change_enabled:
        raise HTTPException(status_code=403, detail="Смена локации отключена в настройках")
    billing_user_id = await _resolve_billing_user_id(request, identity, session)
    db_key = (
        await session.execute(select(Key).where(Key.user_id == billing_user_id, Key.client_id == client_id).limit(1))
    ).scalar_one_or_none()
    if db_key is None:
        raise HTTPException(status_code=404, detail="Подписка не найдена")
    names = await _resolve_available_location_servers(session, db_key)
    return AccountKeyLocationsResponse(
        client_id=str(getattr(db_key, "client_id", "") or ""),
        current_server=str(getattr(db_key, "server_id", "") or ""),
        locations=[AccountKeyLocationOptionResponse(server_name=name) for name in names],
    )


@user_router.post("/{client_id}/change-location", response_model=AccountKeyChangeLocationResponse)
async def user_key_change_location(
    client_id: str,
    body: AccountKeyChangeLocationRequest,
    request: Request,
    force_web: bool = Query(False),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    actions = _key_actions_config()
    if not force_web and not actions.country_change_enabled:
        raise HTTPException(status_code=403, detail="Смена локации отключена в настройках")
    target_server = str(body.server_name or "").strip()
    if not target_server:
        raise HTTPException(status_code=400, detail="Укажите целевую локацию")
    billing_user_id = await _resolve_billing_user_id(request, identity, session)
    db_key = (
        await session.execute(select(Key).where(Key.user_id == billing_user_id, Key.client_id == client_id).limit(1))
    ).scalar_one_or_none()
    if db_key is None:
        raise HTTPException(status_code=404, detail="Подписка не найдена")
    current_server = str(getattr(db_key, "server_id", "") or "")
    if not current_server:
        raise HTTPException(status_code=400, detail="У подписки не указан текущий сервер")
    if current_server == target_server:
        raise HTTPException(status_code=400, detail="Подписка уже в этой локации")
    available_names = await _resolve_available_location_servers(session, db_key)
    if target_server not in available_names:
        raise HTTPException(status_code=400, detail="Выбранная локация недоступна")
    email = str(getattr(db_key, "email", "") or "")
    if not email:
        raise HTTPException(status_code=400, detail="У подписки отсутствует email")
    key_details = await get_key_details(session, email)
    if not key_details:
        raise HTTPException(status_code=404, detail="Подписка не найдена")
    old_server_info = (
        await session.execute(select(Server).where(Server.server_name == current_server).limit(1))
    ).scalar_one_or_none()
    if old_server_info:
        old_panel_type = str(getattr(old_server_info, "panel_type", "") or "").lower()
        try:
            if old_panel_type == "3x-ui":
                xui = await get_xui_instance(str(getattr(old_server_info, "api_url", "") or ""))
                await delete_client(
                    xui,
                    int(getattr(old_server_info, "inbound_id", 0) or 0),
                    email,
                    str(getattr(db_key, "client_id", "") or ""),
                )
            elif old_panel_type == "remnawave":
                remna_del = RemnawaveAPI(str(getattr(old_server_info, "api_url", "") or ""))
                if await remna_del.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
                    await remna_del.delete_user(
                        str(getattr(db_key, "client_id", "") or ""),
                        username=str(getattr(db_key, "email", "") or "") or None,
                    )
        except Exception as exc:
            logger.warning(
                f"[location] не удалось удалить клиента {getattr(db_key, 'client_id', '')} "
                f"со старой панели {old_panel_type}: {exc}"
            )
    target_server_info = (
        await session.execute(select(Server).where(Server.server_name == target_server).limit(1))
    ).scalar_one_or_none()
    if target_server_info is None:
        raise HTTPException(status_code=404, detail="Целевая локация не найдена")
    tariff_id = getattr(db_key, "tariff_id", None)
    tariff = await get_tariff_by_id(session, int(tariff_id)) if tariff_id else None
    need_vless_key = bool(tariff.get("vless")) if tariff else False
    external_squad_uuid = (tariff.get("external_squad") if tariff else None) or None
    selected_traffic_gb = getattr(db_key, "selected_traffic_limit", None)
    selected_device_limit = getattr(db_key, "selected_device_limit", None)
    if selected_traffic_gb is not None:
        traffic_limit_bytes = int(selected_traffic_gb) * GB
    else:
        raw_traffic_limit = int(tariff.get("traffic_limit") or 0) if tariff else 0
        traffic_limit_bytes = raw_traffic_limit * GB if raw_traffic_limit > 0 else 0
    if selected_device_limit is not None:
        device_limit = int(selected_device_limit)
    else:
        device_limit = int(tariff.get("device_limit") or 0) if tariff else 0
    key_client_id = str(getattr(db_key, "client_id", "") or "")
    expiry_timestamp = int(getattr(db_key, "expiry_time", 0) or 0)
    target_cluster_info = await check_server_name_by_cluster(session, target_server)
    target_cluster_name = str((target_cluster_info or {}).get("cluster_name") or "")
    full_remnawave_cluster = (
        await is_full_remnawave_cluster(target_cluster_name, session) if target_cluster_name else False
    )
    panel_type = str(getattr(target_server_info, "panel_type", "") or "").lower()
    remnawave_link = None
    if panel_type == "remnawave" or full_remnawave_cluster:
        remna = RemnawaveAPI(str(getattr(target_server_info, "api_url", "") or ""))
        if not await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
            raise HTTPException(status_code=502, detail="Не удалось авторизоваться в Remnawave")
        expire_at = datetime.utcfromtimestamp(expiry_timestamp / 1000).isoformat() + "Z"
        user_data: dict[str, Any] = {
            "username": email,
            "trafficLimitStrategy": "NO_RESET",
            "expireAt": expire_at,
            "activeInternalSquads": [getattr(target_server_info, "inbound_id", None)],
            "uuid": key_client_id,
        }
        _identity_tg = getattr(identity, "tg_id", None)
        if _identity_tg and int(_identity_tg) > 0:
            user_data["telegramId"] = int(_identity_tg)
        _real_email = getattr(identity, "email", None)
        if _real_email:
            user_data["email"] = _real_email
        if traffic_limit_bytes > 0:
            user_data["trafficLimitBytes"] = traffic_limit_bytes
        if device_limit > 0:
            user_data["hwidDeviceLimit"] = device_limit
        if external_squad_uuid:
            user_data["externalSquadUuid"] = external_squad_uuid
        result = await remna.create_user(user_data)
        if not result:
            raise HTTPException(status_code=502, detail="Не удалось создать подписку в новой локации")
        key_client_id = str(result.get("vlessUuid") or result.get("uuid") or key_client_id)
        if need_vless_key:
            try:
                remnawave_link = await get_vless_link_for_remnawave_by_username(remna, email, email)
            except Exception:
                remnawave_link = None
        if not remnawave_link:
            try:
                sub = await remna.get_subscription_by_username(email)
            except Exception:
                sub = None
            if sub:
                links = sub.get("links") or []
                remnawave_link = (
                    next(
                        (link for link in links if isinstance(link, str) and link.lower().startswith("vless://")),
                        None,
                    )
                    if need_vless_key
                    else None
                )
                if not remnawave_link:
                    remnawave_link = sub.get("subscriptionUrl")
    if panel_type == "3x-ui":
        await create_client_on_server(
            {
                "api_url": str(getattr(target_server_info, "api_url", "") or ""),
                "inbound_id": getattr(target_server_info, "inbound_id", None),
                "server_name": str(getattr(target_server_info, "server_name", "") or ""),
                "panel_type": str(getattr(target_server_info, "panel_type", "") or ""),
            },
            int(key_details.get("tg_id") or 0),
            key_client_id,
            email,
            expiry_timestamp,
            asyncio.Semaphore(1),
            plan=int(tariff_id) if tariff_id else None,
            session=session,
            is_trial=False,
            total_traffic_limit_bytes=traffic_limit_bytes,
            device_limit_value=device_limit,
        )
    subgroup_code = tariff.get("subgroup_title") if tariff and tariff.get("subgroup_title") else None
    public_link = await make_aggregated_link(
        session=session,
        cluster_all=[
            {
                "server_name": str(getattr(target_server_info, "server_name", "") or ""),
                "api_url": str(getattr(target_server_info, "api_url", "") or ""),
                "panel_type": str(getattr(target_server_info, "panel_type", "") or ""),
                "inbound_id": getattr(target_server_info, "inbound_id", None),
                "enabled": True,
                "max_keys": getattr(target_server_info, "max_keys", None),
            }
        ],
        cluster_id=target_cluster_name or target_server,
        email=email,
        client_id=key_client_id,
        tg_id=int(key_details.get("tg_id") or 0),
        subgroup_code=subgroup_code,
        remna_link_override=remnawave_link,
        plan=int(tariff_id) if tariff_id else None,
    )
    db_key.server_id = target_server
    db_key.client_id = key_client_id
    db_key.key = public_link if isinstance(public_link, str) and public_link.strip() else None
    db_key.remnawave_link = remnawave_link
    return AccountKeyChangeLocationResponse(
        ok=True,
        message="Локация успешно изменена",
        client_id=str(getattr(db_key, "client_id", "") or ""),
        server_id=str(getattr(db_key, "server_id", "") or ""),
        link=str(getattr(db_key, "key", "") or ""),
        remnawave_link=getattr(db_key, "remnawave_link", None),
    )

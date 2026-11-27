import asyncio

from fastapi import HTTPException

from database.access.resolution import UserId, panel_identity_fields, subscription_owner_ref
from database.cache_purge import flush_purges
from database.keys import invalidate_key_details, invalidate_keys_list, lock_owned_key_for_operation
from panels.remnawave_runtime import remnawave_api
from services.key_panel_cleanup import (
    key_placement_configuration,
    panel_server_snapshot,
    prepare_key_panel_cleanup,
    reconcile_key_panel_cleanup,
    record_key_panel_target,
)

from .._common import (
    GB,
    REMNAWAVE_LOGIN,
    REMNAWAVE_PASSWORD,
    AccountKeyChangeLocationRequest,
    AccountKeyChangeLocationResponse,
    AccountKeyLocationOptionResponse,
    AccountKeyLocationsResponse,
    AsyncSession,
    Depends,
    Key,
    Query,
    Request,
    Server,
    _key_actions_config,
    _resolve_available_location_servers,
    check_server_name_by_cluster,
    create_client_on_server,
    datetime,
    get_session,
    get_tariff_by_id,
    get_vless_link_for_remnawave_by_username,
    logger,
    make_aggregated_link,
    resolve_billing_user_id,
    select,
    timezone,
    user_router,
    verify_identity_token,
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
    billing_user_id = await resolve_billing_user_id(request, identity, session)
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


def _location_limits(key, tariff: dict | None) -> tuple[int, int]:
    """Берёт актуальные лимиты заблокированной подписки."""
    traffic = key.current_traffic_limit
    if traffic is None:
        traffic = key.selected_traffic_limit
    if traffic is None:
        traffic = (tariff or {}).get("traffic_limit") or 0
    devices = key.current_device_limit
    if devices is None:
        devices = key.selected_device_limit
    if devices is None:
        devices = (tariff or {}).get("device_limit") or 0
    return int(traffic) * GB, int(devices)


async def _remnawave_placement(server, key) -> dict:
    """Сохраняет все сквады общей записи перед сменой локации."""
    async with remnawave_api(server.api_url) as api:
        if not await api.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
            raise HTTPException(status_code=502, detail="Не удалось авторизоваться в Remnawave")
        profile = await api.get_user_by_uuid(key.client_id, username=key.email)
    if (
        not isinstance(profile, dict)
        or str(profile.get("vlessUuid") or profile.get("uuid") or "") != key.client_id
        or not isinstance(profile.get("activeInternalSquads"), list)
    ):
        raise HTTPException(status_code=502, detail="Не удалось сохранить текущее размещение подписки")
    squads = []
    for squad in profile["activeInternalSquads"]:
        value = squad.get("uuid") or squad.get("id") if isinstance(squad, dict) else squad
        if not isinstance(value, str) or not value:
            raise HTTPException(status_code=502, detail="Не удалось сохранить текущее размещение подписки")
        squads.append(value)
    return {"active_user_inbounds": squads, "external_squad_uuid": profile.get("externalSquadUuid") or ""}


async def _provision_location(
    session, key, old_server, target, tariff, traffic_bytes: int, devices: int, intent_id: str
):
    """Создаёт или переносит запись без удаления исходной подписки."""
    panel = str(target.panel_type or "").lower()
    if panel == "3x-ui":
        confirmed = await create_client_on_server(
            {**panel_server_snapshot(target), "enabled": True},
            UserId(key.user_id),
            key.client_id,
            key.email,
            int(key.expiry_time or 0),
            asyncio.Semaphore(1),
            plan=key.tariff_id,
            session=session,
            is_trial=False,
            total_traffic_limit_bytes=traffic_bytes,
            device_limit_value=devices,
        )
        if confirmed is not True:
            raise HTTPException(status_code=502, detail="Не удалось создать подписку в новой локации")
        await record_key_panel_target(intent_id, key.client_id, creation_finished=True)
        return None
    if panel != "remnawave":
        raise HTTPException(status_code=400, detail="Панель выбранной локации не поддерживается")
    same_api = str(old_server.panel_type or "").lower() == "remnawave" and str(old_server.api_url or "").rstrip(
        "/"
    ) == str(target.api_url or "").rstrip("/")
    expire_at = datetime.fromtimestamp(int(key.expiry_time or 0) / 1000, timezone.utc).isoformat()
    external_squad = (tariff or {}).get("external_squad") or ""
    async with remnawave_api(target.api_url) as api:
        if not await api.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
            raise HTTPException(status_code=502, detail="Не удалось авторизоваться в Remnawave")
        if same_api:
            confirmed = await api.update_user(
                uuid=key.client_id,
                lookup_username=key.email,
                expire_at=expire_at,
                active_user_inbounds=[target.inbound_id],
                traffic_limit_bytes=traffic_bytes,
                hwid_device_limit=devices,
                external_squad_uuid=external_squad,
            )
        else:
            telegram_id, real_email = await panel_identity_fields(session, UserId(key.user_id))
            payload = {
                "username": key.email,
                "uuid": key.client_id,
                "trafficLimitStrategy": "NO_RESET",
                "expireAt": expire_at,
                "activeInternalSquads": [target.inbound_id],
                "trafficLimitBytes": traffic_bytes,
                "hwidDeviceLimit": devices,
                "description": f"location-operation:{intent_id}",
            }
            if telegram_id is not None:
                payload["telegramId"] = telegram_id
            if real_email:
                payload["email"] = real_email
            if external_squad:
                payload["externalSquadUuid"] = external_squad
            result = await api.create_user(payload)
            if isinstance(result, dict) and result.get("username") == key.email:
                actual_id = str(result.get("vlessUuid") or result.get("uuid") or "")
                if actual_id and actual_id != key.client_id:
                    await record_key_panel_target(intent_id, actual_id, creation_finished=True)
            confirmed = (
                isinstance(result, dict)
                and str(result.get("vlessUuid") or result.get("uuid") or "") == key.client_id
                and str(result.get("username") or "") == key.email
            )
        if confirmed is not True:
            raise HTTPException(status_code=502, detail="Не удалось создать подписку в новой локации")
        await record_key_panel_target(intent_id, key.client_id, creation_finished=True)
        if bool((tariff or {}).get("vless")):
            link = await get_vless_link_for_remnawave_by_username(api, key.email, key.email)
            if link:
                return link
        subscription = await api.get_subscription_by_username(key.email)
        return subscription.get("subscriptionUrl") if isinstance(subscription, dict) else None


async def _reconcile_location_cleanup(intent_id: str) -> bool:
    """Продолжает очистку сразу, сохраняя очередь при временной ошибке."""
    try:
        return await reconcile_key_panel_cleanup(intent_id, allow_early=True)
    except Exception as error:
        logger.warning("[Site:Subs] Очистка смены локации {} отложена: {}", intent_id, error)
        return False


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
    billing_user_id = await resolve_billing_user_id(request, identity, session)
    key = (
        await session.execute(select(Key).where(Key.user_id == billing_user_id, Key.client_id == client_id).limit(1))
    ).scalar_one_or_none()
    if key is None:
        raise HTTPException(status_code=404, detail="Подписка не найдена")
    if key.is_frozen:
        raise HTTPException(status_code=400, detail="Сначала разморозьте подписку для смены локации")
    key = await lock_owned_key_for_operation(session, billing_user_id, client_id, key.email)
    if key is None:
        raise HTTPException(status_code=409, detail="Подписка изменилась. Обновите страницу.")
    if key.is_frozen:
        raise HTTPException(status_code=400, detail="Сначала разморозьте подписку для смены локации")
    if key.server_id == target_server:
        raise HTTPException(status_code=400, detail="Подписка уже в этой локации")
    if target_server not in await _resolve_available_location_servers(session, key):
        raise HTTPException(status_code=400, detail="Выбранная локация недоступна")
    old_server = await session.scalar(select(Server).where(Server.server_name == key.server_id))
    target = await session.scalar(select(Server).where(Server.server_name == target_server))
    if old_server is None or target is None:
        raise HTTPException(status_code=409, detail="Данные локации изменились. Обновите страницу.")
    if str(target.panel_type or "").lower() not in {"3x-ui", "remnawave"}:
        raise HTTPException(status_code=400, detail="Панель выбранной локации не поддерживается")
    tariff = await get_tariff_by_id(session, int(key.tariff_id)) if key.tariff_id else None
    traffic_bytes, devices = _location_limits(key, tariff)
    old_snapshot = panel_server_snapshot(old_server)
    old_snapshot["key_configuration"] = key_placement_configuration(key)
    if (
        str(old_server.panel_type or "").lower() == "remnawave"
        and str(target.panel_type or "").lower() == "remnawave"
        and str(old_server.api_url or "").rstrip("/") == str(target.api_url or "").rstrip("/")
    ):
        old_snapshot["remnawave_placement"] = await _remnawave_placement(old_server, key)
    intent_id = await prepare_key_panel_cleanup(
        user_id=billing_user_id,
        client_id=key.client_id,
        email=key.email,
        old_server=old_snapshot,
        target_server=panel_server_snapshot(target),
        previous_revision=key.location_revision,
    )
    key_email = key.email
    try:
        remnawave_link = await _provision_location(
            session, key, old_server, target, tariff, traffic_bytes, devices, intent_id
        )
        cluster_info = await check_server_name_by_cluster(session, target_server)
        public_link = await make_aggregated_link(
            session=session,
            cluster_all=[{**panel_server_snapshot(target), "enabled": True, "max_keys": target.max_keys}],
            cluster_id=str((cluster_info or {}).get("cluster_name") or target_server),
            email=key.email,
            client_id=key.client_id,
            tg_id=await subscription_owner_ref(session, billing_user_id),
            subgroup_code=(tariff or {}).get("subgroup_title") or None,
            remna_link_override=remnawave_link,
            plan=key.tariff_id,
        )
        if not isinstance(public_link, str) or not public_link.strip():
            raise HTTPException(status_code=502, detail="Не удалось подготовить ссылку новой локации")
        key.server_id = target_server
        key.location_revision = intent_id
        key.key = public_link
        key.remnawave_link = remnawave_link
        await invalidate_keys_list(session, billing_user_id)
        await invalidate_key_details(key_email)
        response = AccountKeyChangeLocationResponse(
            ok=True,
            message="Локация успешно изменена",
            client_id=key.client_id,
            server_id=key.server_id,
            link=key.key,
            remnawave_link=key.remnawave_link,
        )
        await session.commit()
    except BaseException:
        await session.rollback()
        await asyncio.shield(_reconcile_location_cleanup(intent_id))
        raise
    try:
        await flush_purges(session)
    except Exception as error:
        logger.warning("[Site:Subs] Кэш смены локации {} не обновлён: {}", client_id, error)
    if not await _reconcile_location_cleanup(intent_id):
        response.cleanup_pending = True
        response.message = "Локация изменена. Удаление прежней подписки будет повторено автоматически."
    return response

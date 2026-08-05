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


@user_router.post("/{client_id}/reset-hwid", response_model=AccountKeyResetHwidResponse)
async def user_key_reset_hwid(
    client_id: str,
    request: Request,
    force_web: bool = Query(False),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    actions = _key_actions_config()
    if not force_web and not actions.hwid_reset_enabled:
        raise HTTPException(status_code=403, detail="Сброс устройств отключен в настройках")
    billing_user_id = await _resolve_billing_user_id(request, identity, session)
    db_key = (
        await session.execute(select(Key).where(Key.user_id == billing_user_id, Key.client_id == client_id).limit(1))
    ).scalar_one_or_none()
    if db_key is None:
        raise HTTPException(status_code=404, detail="Подписка не найдена")
    server_id = str(getattr(db_key, "server_id", "") or "")
    if not server_id:
        raise HTTPException(status_code=400, detail="У подписки не указан сервер")

    from services.hwid_cooldown import check_delete_allowed, format_wait_time, register_deletion

    allowed, wait_days = await check_delete_allowed(client_id)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Слишком частое удаление устройств. Попробуйте через {format_wait_time(wait_days)}.",
        )

    async def _reset_devices(api):
        devices = await api.get_user_hwid_devices(client_id)
        if not devices:
            return 0, 0
        reset_local = 0
        for device in devices:
            hwid = device.get("hwid")
            if hwid and await api.delete_user_hwid_device(client_id, hwid):
                reset_local += 1
        return len(devices), reset_local

    reset_result = await with_remnawave_api(
        session,
        server_id,
        _reset_devices,
        fallback_any=True,
        timeout_sec=12.0,
    )
    if reset_result is None:
        raise HTTPException(status_code=502, detail="Не удалось выполнить сброс устройств")
    total_devices, reset_devices = reset_result
    if reset_devices > 0:
        await register_deletion(client_id)
    await invalidate_remnawave_profile(
        session,
        server_id,
        str(client_id),
        fallback_any=True,
    )
    from core.redis_cache import cache_delete, cache_key

    await cache_delete(cache_key("key_devices", client_id))
    return AccountKeyResetHwidResponse(
        ok=True,
        message="Устройства сброшены" if total_devices > 0 else "Устройства не были привязаны",
        total_devices=int(total_devices),
        reset_devices=int(reset_devices),
    )


@user_router.get("/{client_id}/devices")
async def user_key_devices(
    client_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    from core.redis_cache import cache_get, cache_key, cache_set
    from database import get_keys

    billing_user_id = await _resolve_billing_user_id(request, identity, session)
    db_key = next(
        (k for k in await get_keys(session, billing_user_id) if str(getattr(k, "client_id", "")) == client_id),
        None,
    )
    if db_key is None:
        raise HTTPException(status_code=404, detail="Подписка не найдена")
    server_id = str(getattr(db_key, "server_id", "") or "")
    if not server_id:
        return {"devices": [], "total": 0}

    devices_key = cache_key("key_devices", client_id)
    cached = await cache_get(devices_key)
    if isinstance(cached, dict):
        return cached

    async def _list(api):
        return await api.get_user_hwid_devices(client_id)

    devices = await with_remnawave_api(
        session,
        server_id,
        _list,
        fallback_any=True,
        timeout_sec=12.0,
    )
    if not devices:
        response = {"devices": [], "total": 0}
        await cache_set(devices_key, response, 60)
        return response
    normalized = []
    for device in devices:
        if not isinstance(device, dict):
            continue
        normalized.append({
            "hwid": str(device.get("hwid") or ""),
            "device_model": str(device.get("deviceModel") or device.get("device_model") or ""),
            "platform": str(device.get("platform") or ""),
            "os_version": str(device.get("osVersion") or device.get("os_version") or ""),
            "user_agent": str(device.get("userAgent") or device.get("user_agent") or ""),
            "created_at": str(device.get("createdAt") or device.get("created_at") or ""),
        })
    response = {"devices": normalized, "total": len(normalized)}
    await cache_set(devices_key, response, 60)
    return response


@user_router.post("/{client_id}/devices/delete")
async def user_key_delete_device(
    client_id: str,
    request: Request,
    hwid: str = Query(...),
    force_web: bool = Query(False),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    actions = _key_actions_config()
    if not force_web and not actions.hwid_reset_enabled:
        raise HTTPException(status_code=403, detail="Управление устройствами отключено в настройках")
    if not hwid.strip():
        raise HTTPException(status_code=400, detail="Не указано устройство")
    billing_user_id = await _resolve_billing_user_id(request, identity, session)
    db_key = (
        await session.execute(select(Key).where(Key.user_id == billing_user_id, Key.client_id == client_id).limit(1))
    ).scalar_one_or_none()
    if db_key is None:
        raise HTTPException(status_code=404, detail="Подписка не найдена")
    server_id = str(getattr(db_key, "server_id", "") or "")
    if not server_id:
        raise HTTPException(status_code=400, detail="У подписки не указан сервер")

    from services.hwid_cooldown import check_delete_allowed, format_wait_time, register_deletion

    allowed, wait_days = await check_delete_allowed(client_id)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Слишком частое удаление устройств. Попробуйте через {format_wait_time(wait_days)}.",
        )

    async def _delete(api):
        return await api.delete_user_hwid_device(client_id, hwid)

    ok = await with_remnawave_api(
        session,
        server_id,
        _delete,
        fallback_any=True,
        timeout_sec=12.0,
    )
    if not ok:
        raise HTTPException(status_code=502, detail="Не удалось отвязать устройство")
    await invalidate_remnawave_profile(
        session,
        server_id,
        str(client_id),
        fallback_any=True,
    )
    from core.redis_cache import cache_delete, cache_key

    await cache_delete(cache_key("key_devices", client_id))
    await register_deletion(client_id)
    return {"ok": True}

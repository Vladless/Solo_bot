import time

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_session, verify_identity_admin
from database.keys import get_key_details
from database.models import Key, Tariff, User
from database.tariffs import get_tariff_by_id
from logger import logger

subs_router = APIRouter()


async def _resolve(session: AsyncSession, client_id: str) -> Key:
    key = (await session.execute(select(Key).where(Key.client_id == client_id))).scalar_one_or_none()
    if key is None:
        raise HTTPException(status_code=404, detail="Подписка не найдена")
    return key


def _owner_title(u: User | None) -> str:
    if u is None:
        return "—"
    name = " ".join(x for x in (u.first_name, u.last_name) if x)
    return name or (f"@{u.username}" if u.username else f"#{u.tg_id or u.id}")


@subs_router.get("/search")
async def search_subscriptions(
    q: str = Query("", description="client_id, email, alias, tg_id, @username или имя владельца"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Поиск подписок по ключу/владельцу."""
    term = q.strip().lstrip("@")
    stmt = select(Key, User).join(User, Key.user_id == User.id, isouter=True)
    if term:
        like = f"%{term.lower()}%"
        conds = [
            func.lower(Key.client_id).like(like),
            func.lower(Key.email).like(like),
            func.lower(func.coalesce(Key.alias, "")).like(like),
            func.lower(func.coalesce(User.username, "")).like(like),
            func.lower(func.coalesce(User.first_name, "")).like(like),
            func.lower(func.coalesce(User.last_name, "")).like(like),
            cast(User.tg_id, Text).like(f"%{term.lstrip('#')}%"),
            cast(Key.tg_id, Text).like(f"%{term.lstrip('#')}%"),
        ]
        stmt = stmt.where(or_(*conds))
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    rows = (await session.execute(stmt.order_by(Key.expiry_time.desc()).limit(limit).offset(offset))).all()
    now_ms = int(time.time() * 1000)
    items = [
        {
            "client_id": k.client_id,
            "email": k.email,
            "alias": k.alias,
            "server_id": k.server_id,
            "tariff_id": k.tariff_id,
            "expiry_time": int(k.expiry_time or 0),
            "is_frozen": bool(k.is_frozen),
            "active": (not k.is_frozen) and int(k.expiry_time or 0) > now_ms,
            "owner_tg_id": u.tg_id if u else None,
            "owner_title": _owner_title(u),
        }
        for k, u in rows
    ]
    return {"total": int(total), "items": items}


@subs_router.get("/{client_id}")
async def subscription_detail(
    client_id: str = Path(...),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Детальная карточка подписки + справочники для действий (тарифы, кластеры)."""
    key = await _resolve(session, client_id)
    owner = (await session.execute(select(User).where(User.id == key.user_id))).scalar_one_or_none()
    tariff = await get_tariff_by_id(session, key.tariff_id) if key.tariff_id else None
    tariffs = (await session.execute(select(Tariff).where(Tariff.is_active.is_(True)).order_by(Tariff.group_code, Tariff.sort_order))).scalars().all()
    from database.servers import get_servers

    servers_map = await get_servers(session)
    clusters = sorted(servers_map.keys())
    now_ms = int(time.time() * 1000)
    return {
        "client_id": key.client_id,
        "email": key.email,
        "alias": key.alias,
        "server_id": key.server_id,
        "tariff_id": key.tariff_id,
        "tariff_name": (tariff.get("name") if tariff else None),
        "expiry_time": int(key.expiry_time or 0),
        "created_at": int(key.created_at or 0),
        "is_frozen": bool(key.is_frozen),
        "active": (not key.is_frozen) and int(key.expiry_time or 0) > now_ms,
        "device_limit": key.current_device_limit if key.current_device_limit is not None else (int(tariff.get("device_limit")) if tariff and tariff.get("device_limit") else None),
        "traffic_limit": key.current_traffic_limit if key.current_traffic_limit is not None else (int(tariff.get("traffic_limit")) if tariff and tariff.get("traffic_limit") else None),
        "remnawave_link": key.remnawave_link,
        "owner_tg_id": owner.tg_id if owner else None,
        "owner_title": _owner_title(owner),
        "clusters": clusters,
        "tariffs": [
            {"id": t.id, "name": t.name, "group_code": t.group_code, "duration_days": t.duration_days, "price_rub": t.price_rub, "configurable": bool(t.configurable)}
            for t in tariffs
        ],
    }


async def _renew_apply(session: AsyncSession, key: Key, *, new_expiry: int, total_gb, hwid, reset_traffic: bool) -> None:
    from services.operations import renew_key_in_cluster

    await renew_key_in_cluster(
        cluster_id=key.server_id,
        email=key.email,
        client_id=key.client_id,
        new_expiry_time=new_expiry,
        total_gb=total_gb,
        session=session,
        hwid_device_limit=hwid,
        reset_traffic=reset_traffic,
        plan=key.tariff_id,
    )


def _limits_for(key: Key, tariff) -> tuple[Any, Any]:
    total_gb = key.current_traffic_limit if key.current_traffic_limit is not None else (int(tariff.get("traffic_limit") or 0) if tariff else 0)
    hwid = key.current_device_limit if key.current_device_limit is not None else (int(tariff.get("device_limit") or 0) if tariff else 0)
    return total_gb, hwid


@subs_router.post("/{client_id}/expiry")
async def sub_change_expiry(
    client_id: str = Path(...),
    payload: dict = Body(...),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Изменение срока: op = add | take | set (без сброса трафика)."""
    key = await _resolve(session, client_id)
    op = str(payload.get("op") or "add")
    now_ms = int(time.time() * 1000)
    cur = int(key.expiry_time or 0)
    if op == "set":
        new_expiry = int(payload.get("date_ms") or 0)
        if new_expiry <= 0:
            raise HTTPException(status_code=400, detail="Некорректная дата")
    else:
        days = int(payload.get("days") or 0)
        if days <= 0:
            raise HTTPException(status_code=400, detail="Укажите число дней")
        delta = days * 86_400_000
        base = cur if cur > now_ms else now_ms
        new_expiry = base + delta if op == "add" else max(now_ms, cur - delta)
    tariff = await get_tariff_by_id(session, key.tariff_id) if key.tariff_id else None
    total_gb, hwid = _limits_for(key, tariff)
    key.expiry_time = new_expiry
    await _renew_apply(session, key, new_expiry=new_expiry, total_gb=total_gb, hwid=hwid, reset_traffic=False)
    logger.info(f"[API] Срок подписки {client_id} изменён ({op}) → {new_expiry}")
    return {"expiry_time": new_expiry}


@subs_router.post("/{client_id}/reset-traffic")
async def sub_reset_traffic(
    client_id: str = Path(...),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Сброс трафика подписки."""
    key = await _resolve(session, client_id)
    from services.operations.traffic import reset_traffic_in_cluster

    await reset_traffic_in_cluster(key.server_id, key.email, session)
    logger.info(f"[API] Трафик подписки {client_id} сброшен")
    return {"message": "Трафик сброшен"}


@subs_router.post("/{client_id}/change-tariff")
async def sub_change_tariff(
    client_id: str = Path(...),
    payload: dict = Body(...),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Смена тарифа (срок сохраняется). Для конфигурируемого можно задать devices/traffic_gb."""
    key = await _resolve(session, client_id)
    tariff_id = int(payload.get("tariff_id") or 0)
    if tariff_id <= 0:
        raise HTTPException(status_code=400, detail="Не выбран тариф")
    tariff = await get_tariff_by_id(session, tariff_id)
    if tariff is None:
        raise HTTPException(status_code=404, detail="Тариф не найден")
    from database.keys import reset_key_tariff_state, save_key_tariff_selection

    devices = payload.get("devices")
    traffic_gb = payload.get("traffic_gb")
    owner_ref = key.tg_id if key.tg_id is not None else key.user_id
    if bool(tariff.get("configurable")) and (devices is not None or traffic_gb is not None):
        await save_key_tariff_selection(session, owner_ref, key.email, tariff_id, devices, traffic_gb)
        total_gb = int(traffic_gb) if traffic_gb else int(tariff.get("traffic_limit") or 0)
        hwid = int(devices) if devices else int(tariff.get("device_limit") or 0)
    else:
        await reset_key_tariff_state(session, owner_ref, key.email, tariff_id)
        total_gb = int(tariff.get("traffic_limit") or 0)
        hwid = int(tariff.get("device_limit") or 0)
    key.tariff_id = tariff_id
    await _renew_apply(session, key, new_expiry=int(key.expiry_time or 0), total_gb=total_gb, hwid=hwid, reset_traffic=False)
    logger.info(f"[API] Тариф подписки {client_id} → {tariff_id}")
    return {"message": "Тариф изменён", "tariff_id": tariff_id}


@subs_router.post("/{client_id}/set-limits")
async def sub_set_limits(
    client_id: str = Path(...),
    payload: dict = Body(...),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Задать лимит устройств / трафика (ГБ)."""
    key = await _resolve(session, client_id)
    device_limit = payload.get("device_limit")
    traffic_limit = payload.get("traffic_limit")
    if device_limit is not None:
        key.current_device_limit = int(device_limit)
    if traffic_limit is not None:
        key.current_traffic_limit = int(traffic_limit)
    tariff = await get_tariff_by_id(session, key.tariff_id) if key.tariff_id else None
    total_gb, hwid = _limits_for(key, tariff)
    await _renew_apply(session, key, new_expiry=int(key.expiry_time or 0), total_gb=total_gb, hwid=hwid, reset_traffic=False)
    logger.info(f"[API] Лимиты подписки {client_id}: dev={device_limit} gb={traffic_limit}")
    return {"device_limit": key.current_device_limit, "traffic_limit": key.current_traffic_limit}


@subs_router.patch("/{client_id}/alias")
async def sub_set_alias(
    client_id: str = Path(...),
    payload: dict = Body(...),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Изменить псевдоним подписки."""
    key = await _resolve(session, client_id)
    alias = payload.get("alias")
    key.alias = (str(alias).strip() or None) if alias is not None else None
    from database.keys import invalidate_key_details_by_client_id

    await invalidate_key_details_by_client_id(session, client_id)
    return {"alias": key.alias}


@subs_router.post("/{client_id}/freeze")
async def sub_freeze(
    client_id: str = Path(...),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Заморозить подписку."""
    key = await _resolve(session, client_id)
    from database.keys import mark_key_as_frozen
    from services.operations.toggles import toggle_client_on_cluster

    rec = await get_key_details(session, key.email)
    result = await toggle_client_on_cluster(rec["server_id"], key.email, rec["client_id"], enable=False, session=session)
    if result.get("status") != "success":
        raise HTTPException(status_code=502, detail="Не удалось отключить клиента на панели")
    time_left = max(0, int(rec["expiry_time"]) - int(time.time() * 1000))
    await mark_key_as_frozen(session, rec["tg_id"], rec["client_id"], time_left)
    return {"message": "Подписка заморожена"}


@subs_router.post("/{client_id}/unfreeze")
async def sub_unfreeze(
    client_id: str = Path(...),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Разморозить подписку."""
    key = await _resolve(session, client_id)
    from database.keys import mark_key_as_unfrozen
    from services.operations.toggles import toggle_client_on_cluster

    rec = await get_key_details(session, key.email)
    result = await toggle_client_on_cluster(rec["server_id"], key.email, rec["client_id"], enable=True, session=session)
    if result.get("status") != "success":
        raise HTTPException(status_code=502, detail="Не удалось включить клиента на панели")
    tariff = await get_tariff_by_id(session, rec["tariff_id"]) if rec.get("tariff_id") else None
    total_gb, hwid = _limits_for(key, tariff)
    now_ms = int(time.time() * 1000)
    leftover = max(0, int(rec["expiry_time"]))
    new_expiry = leftover if leftover > now_ms else now_ms + leftover
    await mark_key_as_unfrozen(session, rec["tg_id"], rec["client_id"], new_expiry)
    await _renew_apply(session, key, new_expiry=new_expiry, total_gb=total_gb, hwid=hwid, reset_traffic=False)
    return {"message": "Подписка разморожена"}


@subs_router.post("/{client_id}/change-location")
async def sub_change_location(
    client_id: str = Path(...),
    payload: dict = Body(...),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Перенос подписки на другой кластер/страну."""
    key = await _resolve(session, client_id)
    cluster = str(payload.get("cluster") or "").strip() or None
    country = str(payload.get("country") or "").strip() or None
    if not cluster and not country:
        raise HTTPException(status_code=400, detail="Не выбран кластер/страна")
    from services.operations.update import update_subscription

    owner_ref = key.tg_id if key.tg_id is not None else key.user_id
    await update_subscription(owner_ref, key.email, session, cluster_override=cluster, country_override=country)
    logger.info(f"[API] Подписка {client_id} перенесена: cluster={cluster} country={country}")
    return {"message": "Локация изменена"}


@subs_router.post("/{client_id}/reissue")
async def sub_reissue(
    client_id: str = Path(...),
    payload: dict = Body(default={}),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Перевыпуск подписки (пересборка на кластере, ссылка обновляется)."""
    key = await _resolve(session, client_id)
    cluster = str((payload or {}).get("cluster") or "").strip() or key.server_id
    from services.operations.update import update_subscription

    owner_ref = key.tg_id if key.tg_id is not None else key.user_id
    await update_subscription(owner_ref, key.email, session, cluster_override=cluster)
    logger.info(f"[API] Подписка {client_id} перевыпущена на {cluster}")
    return {"message": "Подписка перевыпущена"}


@subs_router.delete("/{client_id}")
async def sub_delete(
    client_id: str = Path(...),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Удалить подписку с кластера и из БД."""
    key = await _resolve(session, client_id)
    from services.operations import delete_key_from_cluster

    await delete_key_from_cluster(session=session, email=key.email, client_id=key.client_id, cluster_id=key.server_id)
    await session.delete(key)
    logger.info(f"[API] Подписка {client_id} удалена")
    return {"message": "Подписка удалена"}


@subs_router.get("/{client_id}/hwid")
async def sub_hwid_devices(
    client_id: str = Path(...),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Список HWID-устройств подписки (Remnawave)."""
    key = await _resolve(session, client_id)
    from panels.remnawave import RemnawaveAPI
    from config import REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD
    from database.servers import get_servers

    servers_map = await get_servers(session)
    cluster = servers_map.get(key.server_id) or [s for lst in servers_map.values() for s in lst if s.get("server_name") == key.server_id]
    remna_node = next((s for s in (cluster or []) if str(s.get("panel_type", "")).lower() == "remnawave"), None)
    if not remna_node:
        return {"devices": [], "supported": False}
    try:
        api = RemnawaveAPI(remna_node["api_url"])
        if not await api.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
            raise HTTPException(status_code=502, detail="Remnawave недоступен")
        devices = await api.get_user_hwid_devices(client_id) or []
        return {"devices": devices, "supported": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.debug(f"[API] hwid list {client_id}: {e}")
        return {"devices": [], "supported": True}


@subs_router.post("/{client_id}/hwid/unbind")
async def sub_hwid_unbind(
    client_id: str = Path(...),
    payload: dict = Body(...),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Отвязать одно HWID-устройство."""
    key = await _resolve(session, client_id)
    hwid = str(payload.get("hwid") or "").strip()
    if not hwid:
        raise HTTPException(status_code=400, detail="Не указан hwid")
    from panels.remnawave import RemnawaveAPI
    from config import REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD
    from database.servers import get_servers

    servers_map = await get_servers(session)
    cluster = servers_map.get(key.server_id) or [s for lst in servers_map.values() for s in lst if s.get("server_name") == key.server_id]
    remna_node = next((s for s in (cluster or []) if str(s.get("panel_type", "")).lower() == "remnawave"), None)
    if not remna_node:
        raise HTTPException(status_code=400, detail="Не Remnawave-подписка")
    api = RemnawaveAPI(remna_node["api_url"])
    if not await api.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
        raise HTTPException(status_code=502, detail="Remnawave недоступен")
    await api.delete_user_hwid_device(client_id, hwid)
    return {"message": "Устройство отвязано"}


@subs_router.post("/{client_id}/hwid/reset")
async def sub_hwid_reset(
    client_id: str = Path(...),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Сбросить все HWID-устройства подписки."""
    key = await _resolve(session, client_id)
    from panels.remnawave import RemnawaveAPI
    from config import REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD
    from database.servers import get_servers

    servers_map = await get_servers(session)
    cluster = servers_map.get(key.server_id) or [s for lst in servers_map.values() for s in lst if s.get("server_name") == key.server_id]
    remna_node = next((s for s in (cluster or []) if str(s.get("panel_type", "")).lower() == "remnawave"), None)
    if not remna_node:
        raise HTTPException(status_code=400, detail="Не Remnawave-подписка")
    api = RemnawaveAPI(remna_node["api_url"])
    if not await api.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
        raise HTTPException(status_code=502, detail="Remnawave недоступен")
    devices = await api.get_user_hwid_devices(client_id) or []
    removed = 0
    for d in devices:
        h = d.get("hwid") if isinstance(d, dict) else None
        if h and await api.delete_user_hwid_device(client_id, h):
            removed += 1
    return {"message": f"Сброшено устройств: {removed}", "removed": removed}

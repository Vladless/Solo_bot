from ._common import *  # noqa: F401,F403
from ._common import router, user_router  # noqa: F401


@router.delete("/by_email/{email}", response_model=dict)
async def delete_key_by_email(
    email: str = Path(..., description="Email клиента"),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_admin),
):
    """Удаляет ключ по email с кластера и из БД."""
    result = await session.execute(select(Key).where(Key.email == email))
    db_key = result.scalar_one_or_none()
    if not db_key:
        raise HTTPException(status_code=404, detail="Ключ не найден")
    try:
        await delete_key_from_cluster(
            session=session,
            email=db_key.email,
            client_id=db_key.client_id,
            cluster_id=db_key.server_id,
        )
        await session.delete(db_key)
        logger.info(f"[Site:Subs] Подписка удалена: {db_key.client_id}")
        return {"message": "Ключ успешно удалён"}
    except Exception as e:
        logger.error(f"[Site:Subs] Не удалось удалить подписку: {e}")
        raise HTTPException(status_code=500, detail="Ошибка при удалении ключа")


@router.post("/freeze/by_email/{email}", response_model=dict)
async def freeze_key_by_email(
    email: str = Path(..., description="Email клиента"),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_admin),
):
    """Замораживает подписку: отключает клиента на панели и сохраняет остаток срока."""
    import time as _time

    from database.keys import mark_key_as_frozen
    from services.operations.toggles import toggle_client_on_cluster

    record = await get_key_details(session, email)
    if not record:
        raise HTTPException(status_code=404, detail="Ключ не найден")
    result = await toggle_client_on_cluster(
        record["server_id"], email, record["client_id"], enable=False, session=session
    )
    if result.get("status") != "success":
        raise HTTPException(status_code=502, detail="Не удалось отключить клиента на панели")
    time_left = max(0, int(record["expiry_time"]) - int(_time.time() * 1000))
    await mark_key_as_frozen(session, record["tg_id"], record["client_id"], time_left)
    logger.info(f"[Site:Subs] Подписка заморожена: {record['client_id']}")
    return {"message": "Подписка заморожена"}


@router.post("/unfreeze/by_email/{email}", response_model=dict)
async def unfreeze_key_by_email(
    email: str = Path(..., description="Email клиента"),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_admin),
):
    """Размораживает подписку: включает клиента на панели и восстанавливает срок."""
    import time as _time

    from database.keys import mark_key_as_unfrozen
    from services.operations.toggles import toggle_client_on_cluster

    record = await get_key_details(session, email)
    if not record:
        raise HTTPException(status_code=404, detail="Ключ не найден")
    result = await toggle_client_on_cluster(
        record["server_id"], email, record["client_id"], enable=True, session=session
    )
    if result.get("status") != "success":
        raise HTTPException(status_code=502, detail="Не удалось включить клиента на панели")

    tariff = await get_tariff_by_id(session, record["tariff_id"]) if record.get("tariff_id") else None
    total_gb = int(tariff.get("traffic_limit") or 0) if tariff else 0
    hwid_limit = int(tariff.get("device_limit") or 0) if tariff else 0
    if record.get("current_traffic_limit") is not None:
        total_gb = record["current_traffic_limit"]
    if record.get("current_device_limit") is not None:
        hwid_limit = record["current_device_limit"]

    now_ms = int(_time.time() * 1000)
    leftover = max(0, int(record["expiry_time"]))
    new_expiry_time = leftover if leftover > now_ms else now_ms + leftover
    await mark_key_as_unfrozen(session, record["tg_id"], record["client_id"], new_expiry_time)
    await renew_key_in_cluster(
        cluster_id=record["server_id"],
        email=email,
        client_id=record["client_id"],
        new_expiry_time=new_expiry_time,
        total_gb=total_gb,
        session=session,
        hwid_device_limit=hwid_limit,
        reset_traffic=False,
        plan=record.get("tariff_id"),
    )
    logger.info(f"[Site:Subs] Подписка разморожена: {record['client_id']}")
    return {"message": "Подписка разморожена"}


@router.get("/routers/{tg_id}", response_model=list[KeyResponse])
async def get_router_keys_by_tg_id(
    tg_id: int = Path(..., description="Telegram ID пользователя"),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_admin),
):
    """Список ключей пользователя с тарифами группы routers."""
    tariffs_result = await session.execute(select(Tariff.id).where(Tariff.group_code == "routers"))
    tariff_ids = [row[0] for row in tariffs_result.all()]
    if not tariff_ids:
        return []
    u = await resolve_user_optional(session, tg_id)
    if u is None:
        return []
    keys_result = await session.execute(select(Key).where(Key.user_id == u.id, Key.tariff_id.in_(tariff_ids)))
    return keys_result.scalars().all()


@router.patch("/edit/by_email/{email}", response_model=KeyResponse)
async def edit_key_by_email(
    email: str = Path(..., description="Email клиента"),
    key_update: KeyUpdate = Body(...),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_admin),
):
    """Обновляет ключ по email и синхронизирует с кластером."""
    result = await session.execute(select(Key).where(Key.email == email))
    db_key = result.scalar_one_or_none()
    if not db_key:
        raise HTTPException(status_code=404, detail="Ключ не найден")
    for field, value in key_update.model_dump(exclude_unset=True).items():
        if field == "expiry_time" and value is not None:
            if isinstance(value, int):
                ms = value
            elif isinstance(value, datetime):
                ms = int(value.timestamp() * 1000)
            else:
                raise HTTPException(status_code=400, detail="Некорректный формат времени")
            setattr(db_key, field, ms)
        else:
            setattr(db_key, field, value)
    try:
        new_expiry_time = db_key.expiry_time
        await renew_key_in_cluster(
            cluster_id=db_key.server_id,
            email=db_key.email,
            client_id=db_key.client_id,
            new_expiry_time=new_expiry_time,
            total_gb=getattr(db_key, "traffic_limit", None),
            session=session,
            hwid_device_limit=getattr(db_key, "device_limit", None),
            reset_traffic=True,
        )
        logger.info(f"[Site:Subs] Подписка обновлена: {db_key.client_id}")
        return db_key
    except Exception as e:
        logger.error(f"[Site:Subs] Не удалось обновить подписку: {e}")
        raise HTTPException(status_code=500, detail="Ошибка при обновлении ключа")


@router.post("/create", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_key_api(
    payload: KeyCreateRequest = Body(...),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_admin),
):
    """Создаёт ключ на кластере."""
    try:
        await create_key_on_cluster(
            cluster_id=payload.cluster_id,
            tg_id=payload.tg_id,
            client_id=payload.client_id,
            email=payload.email or f"{payload.tg_id}_key",
            expiry_timestamp=payload.expiry_timestamp,
            plan=payload.tariff_id,
            session=session,
            remnawave_link=payload.remnawave_link,
            hwid_limit=payload.hwid_limit,
            traffic_limit_bytes=payload.traffic_limit_bytes,
            is_trial=payload.is_trial or False,
        )
        return {"message": "Ключ успешно создан"}
    except Exception as e:
        logger.error(f"[Site:Subs] Не удалось создать подписку: {e}")
        raise HTTPException(status_code=500, detail="Ошибка при создании ключа")

import inspect

from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.redis_cache import cache_delete, cache_get, cache_key, cache_set
from database.access.resolution import resolve_uid_cached, resolve_user_optional
from database.models import Key, Tariff, User
from database.users import invalidate_profile_cache, invalidate_user_snapshot
from logger import logger
from settings.cache_config import (
    KEYS_LIST_CACHE_TTL_SEC,
    KEY_COUNT_CACHE_TTL_SEC,
    KEY_DETAILS_CACHE_TTL_SEC,
)


async def invalidate_key_details(email: str) -> None:
    await cache_delete(cache_key("key_details", email))


async def invalidate_key_email(client_id: str) -> None:
    await cache_delete(cache_key("key_email", client_id))


async def _purge_keys_cache_ids(session: AsyncSession | None, *ids: int) -> None:
    from database.cache_purge import defer_purge

    for i in ids:
        keys = (
            cache_key("keys_list", i),
            cache_key("key_count", i),
            cache_key("user_squads", i),
            cache_key("profile_data", i),
        )
        if defer_purge(session, *keys):
            continue
        for key in keys:
            await cache_delete(key)


async def invalidate_keys_list(session: AsyncSession, legacy_user_ref: int) -> None:
    u = await resolve_user_optional(session, legacy_user_ref)
    if u is None:
        await _purge_keys_cache_ids(session, legacy_user_ref)
        return
    if u.tg_id is not None:
        await _purge_keys_cache_ids(session, u.id, u.tg_id)
    else:
        await _purge_keys_cache_ids(session, u.id)


async def invalidate_key_details_by_client_id(session: AsyncSession, client_id: str) -> None:
    email = await cache_get(cache_key("key_email", client_id))
    await cache_delete(cache_key("key_email", client_id))
    await cache_delete(cache_key("key_actions", client_id))
    if email:
        await invalidate_key_details(str(email))
    else:
        res = await session.execute(select(Key.email).where(Key.client_id == client_id).limit(1))
        row = res.scalar_one_or_none()
        if row is not None:
            await invalidate_key_details(str(row))


async def store_key(
    session: AsyncSession,
    legacy_user_ref: int,
    client_id: str,
    email: str,
    expiry_time: int,
    key: str,
    server_id: str,
    remnawave_link: str = None,
    tariff_id: int | None = None,
    alias: str | None = None,
    selected_device_limit: int | None = None,
    selected_traffic_limit: int | None = None,
    selected_price_rub: int | None = None,
    current_device_limit: int | None = None,
    current_traffic_limit: int | None = None,
):
    """Сохраняет или обновляет ключ подписки."""
    u = await resolve_user_optional(session, legacy_user_ref)
    if u is None:
        raise ValueError(f"Пользователь не найден для ключа: {legacy_user_ref}")
    uid = u.id
    exists = await session.execute(select(Key).where(Key.user_id == uid, Key.client_id == client_id))
    existing_key = exists.scalar_one_or_none()

    if existing_key:
        values: dict = {
            "email": email,
            "expiry_time": expiry_time,
            "key": key,
            "server_id": server_id,
            "remnawave_link": remnawave_link,
            "tariff_id": tariff_id,
            "alias": alias,
            "tg_id": u.tg_id,
        }

        if selected_device_limit is not None:
            values["selected_device_limit"] = selected_device_limit
        if selected_traffic_limit is not None:
            values["selected_traffic_limit"] = selected_traffic_limit
        if selected_price_rub is not None:
            values["selected_price_rub"] = selected_price_rub
        if current_device_limit is not None:
            values["current_device_limit"] = current_device_limit
        if current_traffic_limit is not None:
            values["current_traffic_limit"] = current_traffic_limit

        await session.execute(update(Key).where(Key.user_id == uid, Key.client_id == client_id).values(**values))
        logger.info(f"[Store Key] Ключ обновлён: user_id={uid}, client_id={client_id}, server_id={server_id}")
    else:
        if current_device_limit is None:
            current_device_limit = selected_device_limit
        if current_traffic_limit is None:
            current_traffic_limit = selected_traffic_limit

        new_key = Key(
            user_id=uid,
            tg_id=u.tg_id,
            client_id=client_id,
            email=email,
            created_at=int(datetime.now(UTC).timestamp() * 1000),
            expiry_time=expiry_time,
            key=key,
            server_id=server_id,
            remnawave_link=remnawave_link,
            tariff_id=tariff_id,
            alias=alias,
            selected_device_limit=selected_device_limit,
            selected_traffic_limit=selected_traffic_limit,
            selected_price_rub=selected_price_rub,
            current_device_limit=current_device_limit,
            current_traffic_limit=current_traffic_limit,
        )
        add_result = session.add(new_key)
        if inspect.isawaitable(add_result):
            await add_result
        logger.info(f"[Store Key] Ключ создан: user_id={uid}, client_id={client_id}, server_id={server_id}")
        try:
            from database.subscription_events import record_subscription_event

            await record_subscription_event(
                session,
                event_type="created",
                user_id=uid,
                tg_id=u.tg_id,
                client_id=client_id,
                tariff_id=tariff_id,
                server_id=server_id,
                price_rub=float(selected_price_rub) if selected_price_rub is not None else None,
                expiry_time=expiry_time,
                source="bot",
            )
        except Exception:
            pass

    invalidate_user_snapshot(uid)
    if u.tg_id is not None:
        invalidate_user_snapshot(u.tg_id)
    await invalidate_keys_list(session, uid)
    await invalidate_key_details(email)


def _key_to_cache_dict(k: Key) -> dict:
    return {
        "email": k.email,
        "alias": k.alias,
        "client_id": k.client_id,
        "expiry_time": int(k.expiry_time) if k.expiry_time is not None else 0,
        "created_at": int(k.created_at) if k.created_at is not None else 0,
        "tariff_id": k.tariff_id,
        "server_id": k.server_id,
        "key": k.key,
        "remnawave_link": k.remnawave_link,
        "is_frozen": bool(k.is_frozen) if k.is_frozen is not None else False,
    }


async def get_keys(session: AsyncSession, legacy_user_ref: int):
    uid = await resolve_uid_cached(session, legacy_user_ref)
    if uid is None:
        return []
    ckey = cache_key("keys_list", uid)
    cached = await cache_get(ckey)
    if isinstance(cached, list):
        return [SimpleNamespace(**d) for d in cached]
    result = await session.execute(select(Key).where(Key.user_id == uid))
    rows = result.scalars().all()
    serialized = [_key_to_cache_dict(k) for k in rows]
    await cache_set(ckey, serialized, KEYS_LIST_CACHE_TTL_SEC)
    return rows


async def get_all_keys(session: AsyncSession):
    result = await session.execute(select(Key))
    return result.scalars().all()


async def get_key_by_server(session: AsyncSession, legacy_user_ref: int, client_id: str):
    uid = await resolve_uid_cached(session, legacy_user_ref)
    if uid is None:
        return None
    stmt = select(Key).where(Key.user_id == uid, Key.client_id == client_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_key_by_email(session: AsyncSession, email: str, legacy_user_ref: int | None = None) -> Key | None:
    stmt = select(Key).where(Key.email == email)
    if legacy_user_ref is not None:
        uid = await resolve_uid_cached(session, legacy_user_ref)
        if uid is None:
            return None
        stmt = stmt.where(Key.user_id == uid)
    result = await session.execute(stmt.limit(1))
    return result.scalar_one_or_none()


async def get_key_by_client_id(session: AsyncSession, client_id: str, legacy_user_ref: int | None = None) -> Key | None:
    stmt = select(Key).where(Key.client_id == client_id)
    if legacy_user_ref is not None:
        uid = await resolve_uid_cached(session, legacy_user_ref)
        if uid is None:
            return None
        stmt = stmt.where(Key.user_id == uid)
    result = await session.execute(stmt.limit(1))
    return result.scalar_one_or_none()


async def get_key_expiry_presets(session: AsyncSession, email: str) -> tuple[str | None, list[int]]:
    key_obj = await get_key_by_email(session, email)
    if not key_obj:
        return None, []

    if not key_obj.tariff_id:
        return key_obj.client_id, []

    tariff = await session.execute(select(Tariff.group_code).where(Tariff.id == key_obj.tariff_id))
    group_code = tariff.scalar_one_or_none()
    if not group_code:
        return key_obj.client_id, []

    result = await session.execute(
        select(Tariff.duration_days)
        .where(Tariff.group_code == group_code, Tariff.is_active.is_(True))
        .order_by(Tariff.duration_days)
    )
    unique_durations: list[int] = []
    seen: set[int] = set()
    for (days,) in result.all():
        if days is None or days < 1 or days in seen:
            continue
        seen.add(int(days))
        unique_durations.append(int(days))

    return key_obj.client_id, unique_durations


async def get_key_details(session: AsyncSession, email: str) -> dict | None:
    """Возвращает подробную информацию о ключе по email. Горячие данные кэшируются в Redis."""
    ckey = cache_key("key_details", email)
    cached = await cache_get(ckey)
    if isinstance(cached, dict):
        return cached

    stmt = select(Key, User).join(User, Key.user_id == User.id).where(Key.email == email)
    result = await session.execute(stmt)
    row = result.first()
    if not row:
        return None

    key, user = row
    expiry_date = datetime.fromtimestamp(key.expiry_time / 1000, UTC)
    current_date = datetime.now(UTC)
    time_left = expiry_date - current_date

    if time_left.total_seconds() <= 0:
        days_left_message = "<b>Ключ истек.</b>"
    elif time_left.days > 0:
        days_left_message = f"Осталось дней: <b>{time_left.days}</b>"
    else:
        hours_left = time_left.seconds // 3600
        days_left_message = f"Осталось часов: <b>{hours_left}</b>"

    out = {
        "key": key.key,
        "remnawave_link": key.remnawave_link,
        "server_id": key.server_id,
        "created_at": key.created_at,
        "expiry_time": key.expiry_time,
        "client_id": key.client_id,
        "tg_id": user.tg_id,
        "email": key.email,
        "is_frozen": key.is_frozen,
        "balance": user.balance,
        "alias": key.alias,
        "expiry_date": expiry_date.strftime("%d %B %Y года %H:%M"),
        "days_left_message": days_left_message,
        "link": key.key or key.remnawave_link,
        "cluster_name": key.server_id,
        "location_name": key.server_id,
        "tariff_id": key.tariff_id,
        "selected_device_limit": key.selected_device_limit,
        "selected_traffic_limit": key.selected_traffic_limit,
        "selected_price_rub": key.selected_price_rub,
        "current_device_limit": key.current_device_limit,
        "current_traffic_limit": key.current_traffic_limit,
    }
    await cache_set(ckey, out, KEY_DETAILS_CACHE_TTL_SEC)
    if key.client_id:
        await cache_set(cache_key("key_email", key.client_id), email, KEY_DETAILS_CACHE_TTL_SEC)
    return out


async def get_key_count(session: AsyncSession, legacy_user_ref: int) -> int:
    uid = await resolve_uid_cached(session, legacy_user_ref)
    if uid is None:
        return 0
    cached = await cache_get(cache_key("key_count", uid))
    if cached is not None:
        try:
            return int(cached)
        except (TypeError, ValueError):
            pass
    result = await session.execute(select(func.count()).select_from(Key).where(Key.user_id == uid))
    count = result.scalar() or 0
    await cache_set(cache_key("key_count", uid), count, KEY_COUNT_CACHE_TTL_SEC)
    return count


async def get_key_by_user_and_email(session: AsyncSession, user_id: int, email: str) -> Key | None:
    """Возвращает ORM-объект Key по паре (users.id, email) или None."""
    result = await session.execute(select(Key).where(Key.user_id == int(user_id), Key.email == email))
    return result.scalar_one_or_none()


async def delete_key_by_user_and_email(session: AsyncSession, user_id: int, email: str) -> None:
    """Удаляет ключ по паре (users.id, email). Commit — ответственность caller'а."""
    await session.execute(delete(Key).where(Key.user_id == int(user_id), Key.email == email))


async def get_user_keys_with_servers_by_email(
    session: AsyncSession, user_id: int, email: str
) -> list[tuple[str, str, dict]]:
    """Возвращает ключи пользователя + инфо о серверах (join Key × Server).

    Каждый элемент — ``(client_id, server_id, server_info_dict)``. Join
    делается по (Key.server_id == Server.server_name OR Server.cluster_name),
    чтобы поддержать и country-mode (server_id = cluster), и cluster-mode
    (server_id = server_name).

    Используется в ``services.operations.traffic.get_user_traffic``.
    """
    from sqlalchemy import or_

    from database.models import Server

    join_cond = or_(
        Key.server_id == Server.server_name,
        Key.server_id == Server.cluster_name,
    )
    result = await session.execute(
        select(Key.client_id, Key.server_id, Server)
        .select_from(Key)
        .join(Server, join_cond)
        .where(Server.enabled.is_(True), Key.user_id == int(user_id), Key.email == email)
    )
    rows = []
    for client_id, server_id, server in result.all():
        rows.append((
            client_id,
            server_id,
            {
                "server_name": server.server_name,
                "cluster_name": server.cluster_name,
                "api_url": server.api_url,
                "panel_type": server.panel_type,
            },
        ))
    return rows


async def get_key_client_id_by_email_and_server(session: AsyncSession, email: str, server_id: str) -> str | None:
    """Возвращает ``client_id`` первого ключа для пары (email, server_id).

    Используется для remnawave traffic reset, где нам нужен только client_id,
    без остальных полей ключа.
    """
    result = await session.execute(select(Key.client_id).where(Key.email == email, Key.server_id == server_id).limit(1))
    return result.scalar()


async def count_keys_by_server_id(session: AsyncSession, server_id: str) -> int:
    """Сколько всего ключей привязано к указанному server_id (кластеру или серверу).

    Используется для проверки max_keys лимита. ``server_id`` — строка
    (у ``keys.server_id`` колонка типа String, содержит либо cluster_name,
    либо server_name в зависимости от страны/кластера).
    """
    result = await session.execute(select(func.count()).select_from(Key).where(Key.server_id == server_id))
    return int(result.scalar() or 0)


async def get_all_key_server_ids(session: AsyncSession) -> list[str]:
    """Список всех ``server_id`` из таблицы keys (с повторениями).

    Используется в ``services.clusters.select_cluster`` для подсчёта загрузки
    кластеров. Возвращаем только server_id строки без подгрузки остальных
    полей, чтобы не тянуть сотни мегабайт для огромных deployments.
    """
    result = await session.execute(select(Key.server_id))
    return [row[0] for row in result.all() if row[0] is not None]


async def count_active_keys_for_user(session: AsyncSession, user_id: int) -> int:
    """Количество незамороженных ключей у пользователя (по internal users.id).

    Отличается от `get_key_count`: не кэшируется и явно исключает замороженные.
    Используется в проверке "новый пользователь" для купонных правил.
    """
    result = await session.execute(
        select(func.count()).select_from(Key).where(Key.user_id == int(user_id), Key.is_frozen.is_(False))
    )
    return int(result.scalar() or 0)


async def _log_key_deletions(session: AsyncSession, rows, client_ids) -> None:
    """Пишет событие expired/deleted в журнал ДО удаления ключа (иначе данные теряются)."""
    try:
        from database.subscription_events import record_subscription_event

        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        for r, cid in zip(rows, client_ids, strict=False):
            exp = getattr(r, "expiry_time", None)
            was_expired = bool(exp is not None and exp < now_ms)
            await record_subscription_event(
                session,
                event_type="expired" if was_expired else "deleted",
                user_id=getattr(r, "user_id", None),
                tg_id=getattr(r, "tg_id", None),
                client_id=cid,
                tariff_id=getattr(r, "tariff_id", None),
                server_id=getattr(r, "server_id", None),
                expiry_time=exp,
                was_expired=was_expired,
                source="cron",
            )
    except Exception:
        pass


async def delete_key(session: AsyncSession, identifier: int | str):
    legacy_for_cache = None
    email_for_cache = None
    if isinstance(identifier, str):
        res = await session.execute(
            select(Key.user_id, Key.tg_id, Key.email, Key.tariff_id, Key.server_id, Key.expiry_time).where(
                Key.client_id == identifier
            )
        )
        deleted_rows = res.all()
        if deleted_rows:
            legacy_for_cache, email_for_cache = deleted_rows[0].user_id, deleted_rows[0].email
        await _log_key_deletions(session, deleted_rows, [identifier] * len(deleted_rows))
        await cache_delete(cache_key("key_email", identifier))
        await session.execute(delete(Key).where(Key.client_id == identifier))
    else:
        u = await resolve_user_optional(session, identifier)
        if u is None:
            logger.info(f"Ключ не удалён: пользователь {identifier} не найден")
            return
        legacy_for_cache = u.id
        res = await session.execute(
            select(
                Key.user_id, Key.tg_id, Key.email, Key.tariff_id, Key.server_id, Key.expiry_time, Key.client_id
            ).where(Key.user_id == u.id)
        )
        deleted_rows = res.all()
        await _log_key_deletions(session, deleted_rows, [getattr(r, "client_id", None) for r in deleted_rows])
        await session.execute(delete(Key).where(Key.user_id == u.id))
    if legacy_for_cache is not None:
        invalidate_user_snapshot(legacy_for_cache)
        await invalidate_keys_list(session, legacy_for_cache)
    if email_for_cache is not None:
        await invalidate_key_details(str(email_for_cache))
    logger.info(f"Ключ с идентификатором {identifier} удалён")


async def update_key_expiry(
    session: AsyncSession,
    client_id: str,
    new_expiry_time: int,
    record_event: bool = True,
    price_rub: float | None = None,
):
    try:
        ctx = (
            await session.execute(
                select(Key.user_id, Key.tg_id, Key.tariff_id, Key.server_id).where(Key.client_id == client_id).limit(1)
            )
        ).first()
    except Exception:
        ctx = None
    await session.execute(update(Key).where(Key.client_id == client_id).values(expiry_time=new_expiry_time))
    await invalidate_key_details_by_client_id(session, client_id)
    logger.info(f"Срок действия ключа {client_id} обновлён до {new_expiry_time}")
    if not record_event:
        return
    try:
        from database.subscription_events import record_subscription_event

        await record_subscription_event(
            session,
            event_type="renewed",
            user_id=ctx.user_id if ctx else None,
            tg_id=ctx.tg_id if ctx else None,
            client_id=client_id,
            tariff_id=ctx.tariff_id if ctx else None,
            server_id=ctx.server_id if ctx else None,
            price_rub=price_rub,
            expiry_time=new_expiry_time,
            source="bot",
        )
    except Exception:
        pass


async def get_client_id_by_email(session: AsyncSession, email: str):
    result = await session.execute(select(Key.client_id).where(Key.email == email))
    return result.scalar_one_or_none()


async def update_key_notified(session: AsyncSession, legacy_user_ref: int, client_id: str):
    u = await resolve_user_optional(session, legacy_user_ref)
    if u is None:
        return
    await session.execute(update(Key).where(Key.user_id == u.id, Key.client_id == client_id).values(notified=True))
    await invalidate_keys_list(session, u.id)
    await invalidate_key_details_by_client_id(session, client_id)


async def mark_key_as_frozen(session: AsyncSession, legacy_user_ref: int, client_id: str, time_left: int):
    u = await resolve_user_optional(session, legacy_user_ref)
    if u is None:
        return
    await session.execute(
        text(
            """
            UPDATE keys
            SET expiry_time = :expiry,
                is_frozen = TRUE
            WHERE user_id = :user_id
              AND client_id = :client_id
            """
        ),
        {"expiry": time_left, "user_id": u.id, "client_id": client_id},
    )
    await invalidate_keys_list(session, u.id)
    await invalidate_key_details_by_client_id(session, client_id)


async def mark_key_as_unfrozen(
    session: AsyncSession,
    legacy_user_ref: int,
    client_id: str,
    new_expiry_time: int,
):
    u = await resolve_user_optional(session, legacy_user_ref)
    if u is None:
        return
    await session.execute(
        text(
            """
            UPDATE keys
            SET expiry_time = :expiry,
                is_frozen = FALSE
            WHERE user_id = :user_id
              AND client_id = :client_id
            """
        ),
        {"expiry": new_expiry_time, "user_id": u.id, "client_id": client_id},
    )
    await invalidate_keys_list(session, u.id)
    await invalidate_key_details_by_client_id(session, client_id)


async def update_key_tariff(session: AsyncSession, client_id: str, tariff_id: int):
    await session.execute(update(Key).where(Key.client_id == client_id).values(tariff_id=tariff_id))
    await invalidate_key_details_by_client_id(session, client_id)
    logger.info(f"Тариф ключа {client_id} обновлён на {tariff_id}")


async def update_key_renewal_snapshot(
    session: AsyncSession,
    email: str,
    *,
    tariff_id: int,
    selected_device_limit: int | None = None,
    current_device_limit: int | None = None,
    selected_traffic_limit: int | None = None,
    current_traffic_limit: int | None = None,
    apply_limits: bool = True,
) -> None:
    """Обновляет tariff_id и (опционально) лимиты ключа после продления.

    ``apply_limits=True`` — выставить все четыре лимита (для non-configurable
    тарифов). ``apply_limits=False`` — обновить только ``tariff_id``, лимиты
    не трогать (configurable-тарифы обновляют их через `save_key_config_with_mode`).
    """
    values: dict = {"tariff_id": tariff_id}
    if apply_limits:
        values["selected_device_limit"] = selected_device_limit
        values["current_device_limit"] = current_device_limit
        values["selected_traffic_limit"] = selected_traffic_limit
        values["current_traffic_limit"] = current_traffic_limit
    await session.execute(update(Key).where(Key.email == email).values(**values))
    await invalidate_key_details(email)


async def update_key_post_creation_snapshot(
    session: AsyncSession,
    *,
    user_id: int,
    email: str,
    selected_device_limit: int | None,
    selected_traffic_limit: int | None,
    selected_price_rub: int | None,
) -> None:
    """Дозаписывает выбранные пользователем параметры ключа сразу после создания.

    Используется из `services.keys.create_vpn_key_headless` — тариф/лимиты не
    всегда известны на момент `create_key_on_cluster`, поэтому после него
    идёт snapshot-апдейт для полей, которые нужны для отображения в UI.
    """
    await session.execute(
        update(Key)
        .where(Key.user_id == int(user_id), Key.email == email)
        .values(
            selected_device_limit=selected_device_limit,
            selected_traffic_limit=selected_traffic_limit,
            selected_price_rub=selected_price_rub,
        )
    )
    await invalidate_key_details(email)


async def get_subscription_link(session: AsyncSession, email: str) -> str | None:
    result = await session.execute(select(func.coalesce(Key.key, Key.remnawave_link)).where(Key.email == email))
    return result.scalar_one_or_none()


async def update_key_client_id(session: AsyncSession, email: str, new_client_id: str):
    await session.execute(update(Key).where(Key.email == email).values(client_id=new_client_id))
    await invalidate_key_details(email)
    logger.info(f"client_id обновлён для {email} -> {new_client_id}")


async def update_key_link(session: AsyncSession, email: str, link: str) -> bool:
    q = update(Key).where(Key.email == email).values(key=link).returning(Key.client_id)
    res = await session.execute(q)
    ok = res.scalar_one_or_none() is not None
    if ok:
        await invalidate_key_details(email)
    return ok


async def update_key_subscription_links(session: AsyncSession, email: str, link: str) -> bool:
    stmt = (
        update(Key)
        .where(Key.email == email)
        .values(
            key=link,
            remnawave_link=link,
        )
        .returning(Key.client_id)
    )
    res = await session.execute(stmt)
    ok = res.scalar_one_or_none() is not None
    if ok:
        await invalidate_key_details(email)
    return ok


async def update_key_email_and_link(
    session: AsyncSession, old_email: str, new_email: str, link: str, client_id: str
) -> bool:
    stmt = update(Key).where(Key.email == old_email).values(email=new_email, key=link).returning(Key.client_id)
    res = await session.execute(stmt)
    ok = res.scalar_one_or_none() is not None
    if ok:
        await invalidate_key_details(old_email)
        await invalidate_key_details(new_email)
        await invalidate_key_email(client_id)
    return ok


async def save_key_config_with_mode(
    session: AsyncSession,
    email: str,
    selected_devices: int | None,
    selected_traffic_gb: int | None,
    total_price: int,
    has_device_choice: bool,
    has_traffic_choice: bool,
    config_mode: str,
) -> None:
    values: dict = {}

    if config_mode == "pack":
        if has_device_choice and selected_devices is not None:
            values["current_device_limit"] = int(selected_devices)
        if has_traffic_choice and selected_traffic_gb is not None:
            values["current_traffic_limit"] = int(selected_traffic_gb)
    else:
        device_val = int(selected_devices) if selected_devices is not None and has_device_choice else None
        traffic_val = int(selected_traffic_gb) if selected_traffic_gb is not None and has_traffic_choice else None

        values["selected_device_limit"] = device_val
        values["selected_traffic_limit"] = traffic_val
        values["selected_price_rub"] = int(total_price)
        values["current_device_limit"] = device_val
        values["current_traffic_limit"] = traffic_val

    if not values:
        return

    await session.execute(update(Key).where(Key.email == email).values(**values))
    await invalidate_key_details(email)


async def reset_key_tariff_state(session: AsyncSession, legacy_user_ref: int, email: str, tariff_id: int) -> None:
    u = await resolve_user_optional(session, legacy_user_ref)
    if u is None:
        return
    await session.execute(
        update(Key)
        .where(Key.user_id == u.id, Key.email == email)
        .values(
            tariff_id=tariff_id,
            selected_device_limit=None,
            current_device_limit=None,
            selected_traffic_limit=None,
            current_traffic_limit=None,
            selected_price_rub=None,
        )
    )
    await invalidate_keys_list(session, u.id)
    await invalidate_key_details(email)


async def save_key_tariff_selection(
    session: AsyncSession,
    legacy_user_ref: int,
    email: str,
    tariff_id: int,
    selected_devices: int | None,
    selected_traffic_gb: int | None,
) -> None:
    u = await resolve_user_optional(session, legacy_user_ref)
    if u is None:
        return
    selected_devices_val = int(selected_devices) if selected_devices is not None else None
    selected_traffic_val = (
        int(selected_traffic_gb) if selected_traffic_gb is not None and int(selected_traffic_gb) > 0 else None
    )

    await session.execute(
        update(Key)
        .where(Key.user_id == u.id, Key.email == email)
        .values(
            tariff_id=tariff_id,
            selected_device_limit=selected_devices_val,
            current_device_limit=selected_devices_val,
            selected_traffic_limit=selected_traffic_val,
            current_traffic_limit=selected_traffic_val,
            selected_price_rub=None,
        )
    )
    await invalidate_keys_list(session, u.id)
    await invalidate_key_details(email)


async def save_admin_key_config(
    session: AsyncSession,
    email: str,
    base_devices: int,
    total_devices: int,
    base_traffic: int | None,
    total_traffic: int | None,
    selected_price: int | None,
) -> None:
    await session.execute(
        update(Key)
        .where(Key.email == email)
        .values(
            selected_device_limit=base_devices,
            current_device_limit=total_devices,
            selected_traffic_limit=base_traffic,
            current_traffic_limit=total_traffic,
            selected_price_rub=selected_price,
        )
    )
    await invalidate_key_details(email)


async def reset_key_current_limits_to_selected(session: AsyncSession, client_id: str):
    """Сбрасывает текущие лимиты к выбранным для ключа."""
    await session.execute(
        text(
            """
            UPDATE keys
            SET current_device_limit = selected_device_limit,
                current_traffic_limit = selected_traffic_limit
            WHERE client_id = :client_id
            """
        ),
        {"client_id": client_id},
    )
    await invalidate_key_details_by_client_id(session, client_id)
    logger.info(f"Текущие лимиты ключа {client_id} сброшены к выбранным")

import asyncio

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from database import async_session_maker
from database.models import Key, KeyPanelCleanup, Server, Tariff, User
from logger import logger
from panels._3xui import delete_client, get_xui_instance
from panels.remnawave_runtime import remnawave_api
from settings.config import REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD, SUPERNODE


SETTLE_SECONDS = 60


def panel_server_snapshot(server) -> dict:
    """Сохраняет адрес и привязку панели для повторной очистки."""
    return {field: getattr(server, field, None) for field in ("server_name", "api_url", "panel_type", "inbound_id")}


def _binding(server: dict, email: str) -> tuple:
    """Определяет отдельную запись или подключение панели."""
    panel = str(server.get("panel_type") or "").lower()
    api_url = str(server.get("api_url") or "").rstrip("/")
    if panel == "remnawave":
        return panel, api_url
    panel_email = f"{email}_{str(server.get('server_name') or '').lower()}" if SUPERNODE else email
    return panel, api_url, str(server.get("inbound_id") or ""), panel_email


def _utc(value: datetime) -> datetime:
    """Приводит сохранённое время к UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def key_placement_configuration(key) -> dict:
    """Отмечает тарифную конфигурацию, для которой сохранено размещение."""
    changed_at = key.last_tariff_change_at
    return {
        "tariff_id": key.tariff_id,
        "last_tariff_change_at": _utc(changed_at).isoformat() if changed_at is not None else None,
        "selected_device_limit": key.selected_device_limit,
        "selected_traffic_limit": key.selected_traffic_limit,
    }


async def prepare_key_panel_cleanup(
    *,
    user_id: int,
    client_id: str,
    email: str,
    old_server: dict,
    target_server: dict,
    previous_revision: str | None = None,
    sessionmaker=None,
) -> str:
    """Фиксирует обе привязки до изменения внешних панелей."""
    maker = sessionmaker or async_session_maker
    settle_after = datetime.now(UTC) + timedelta(seconds=SETTLE_SECONDS)
    async with maker() as session:
        intent = KeyPanelCleanup(
            user_id=int(user_id),
            client_id=client_id,
            target_client_id=client_id,
            email=email,
            previous_revision=previous_revision,
            old_server=old_server,
            target_server=target_server,
            settle_after=settle_after,
            next_attempt_at=settle_after,
        )
        session.add(intent)
        await session.flush()
        intent_id = intent.id
        await session.commit()
    return intent_id


async def record_key_panel_target(
    intent_id: str, client_id: str, *, creation_finished: bool = False, sessionmaker=None
) -> None:
    """Сохраняет фактический идентификатор созданной записи панели."""
    maker = sessionmaker or async_session_maker
    async with maker() as session:
        result = await session.execute(
            update(KeyPanelCleanup)
            .where(KeyPanelCleanup.id == intent_id)
            .values(target_client_id=client_id, creation_finished=creation_finished)
        )
        if not result.rowcount:
            raise ValueError("Операция очистки панели не найдена")
        await session.commit()


async def _delete_binding(
    server: dict,
    client_id: str,
    email: str,
    *,
    intent_id: str,
    lookup_new: bool = False,
    protected_client_id: str | None = None,
) -> bool:
    """Удаляет только указанную запись с подтверждением панели."""
    api_url = str(server.get("api_url") or "").rstrip("/")
    if not api_url:
        raise ValueError("Не задан адрес панели для очистки")
    panel = str(server.get("panel_type") or "").lower()
    if panel == "3x-ui":
        panel_email = f"{email}_{str(server.get('server_name') or '').lower()}" if SUPERNODE else email
        return (
            await delete_client(
                await get_xui_instance(api_url), int(server.get("inbound_id") or 0), panel_email, client_id
            )
            is True
        )
    if panel == "remnawave":
        async with remnawave_api(api_url) as api:
            if not await api.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
                return False
            if lookup_new:
                profile = await api.get_user_by_username(email)
                if isinstance(profile, dict) and profile.get("description") == f"location-operation:{intent_id}":
                    actual_id = profile.get("vlessUuid") or profile.get("uuid")
                    if not isinstance(actual_id, str) or not actual_id:
                        return False
                    if actual_id == protected_client_id:
                        return True
                    return await api.delete_user(actual_id, username=email) is True
            return await api.delete_user(client_id, username=email) is True
    raise ValueError("Неизвестная панель для очистки")


async def _restore_remnawave_placement(session, key: Key, server: dict) -> bool:
    """Восстанавливает размещение общей записи после отката базы."""
    tariff = await session.get(Tariff, key.tariff_id) if key.tariff_id else None
    traffic = key.current_traffic_limit
    if traffic is None:
        traffic = key.selected_traffic_limit
    if traffic is None:
        traffic = getattr(tariff, "traffic_limit", 0)
    devices = key.current_device_limit
    if devices is None:
        devices = key.selected_device_limit
    if devices is None:
        devices = getattr(tariff, "device_limit", 0)
    expiry = int(key.expiry_time or 0)
    if key.is_frozen and expiry <= int(datetime.now(UTC).timestamp() * 1000):
        expiry += int(datetime.now(UTC).timestamp() * 1000)
    placement = server.get("remnawave_placement")
    if not isinstance(placement, dict) or not isinstance(placement.get("active_user_inbounds"), list):
        raise ValueError("Не сохранены исходные сквады подписки")
    async with remnawave_api(str(server.get("api_url") or "")) as api:
        if not await api.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
            return False
        return (
            await api.update_user(
                uuid=key.client_id,
                lookup_username=key.email,
                status="DISABLED" if key.is_frozen else "ACTIVE",
                expire_at=datetime.fromtimestamp(expiry / 1000, UTC).isoformat(),
                active_user_inbounds=placement["active_user_inbounds"],
                traffic_limit_bytes=int(traffic or 0) * 1024**3,
                hwid_device_limit=int(devices or 0),
                external_squad_uuid=placement.get("external_squad_uuid") or "",
            )
            is True
        )


async def reconcile_key_panel_cleanup(intent_id: str, *, sessionmaker=None, allow_early: bool = False) -> bool:
    """Очищает незакреплённые привязки по фактически сохранённому ключу."""
    maker = sessionmaker or async_session_maker
    async with maker() as session:
        intent = await session.get(KeyPanelCleanup, intent_id)
        if intent is None:
            return True
        now = datetime.now(UTC)
        if not allow_early and _utc(intent.next_attempt_at) > now:
            return False
        current = (
            await session.execute(select(Key.user_id, Key.client_id).where(Key.email == intent.email))
        ).one_or_none()
        user_id = int(current.user_id) if current is not None else intent.user_id
        owner_exists = await session.scalar(select(User.id).where(User.id == user_id))
        if owner_exists is not None:
            locked_owner = await session.scalar(
                select(User.id).where(User.id == user_id).with_for_update(skip_locked=True)
            )
            if locked_owner is None:
                return False
        key = None
        if current is not None:
            key = await session.scalar(
                select(Key)
                .where(Key.user_id == user_id, Key.email == intent.email)
                .with_for_update(skip_locked=True)
                .execution_options(populate_existing=True)
            )
            if key is None:
                return False
        intent = await session.scalar(
            select(KeyPanelCleanup)
            .where(KeyPanelCleanup.id == intent_id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        if intent is None:
            return False
        try:
            protected = []
            if key is not None:
                protected = list(
                    (await session.scalars(select(Server).where(Server.server_name == key.server_id))).all()
                )
                if not protected:
                    protected = list(
                        (await session.scalars(select(Server).where(Server.cluster_name == key.server_id))).all()
                    )
                if not protected:
                    raise ValueError("Не удалось определить текущую панель подписки")
            protected_bindings = {_binding(panel_server_snapshot(server), intent.email) for server in protected}
            same_binding = _binding(intent.old_server, intent.email) == _binding(intent.target_server, intent.email)
            restored = False
            if (
                key is not None
                and key.client_id == intent.client_id
                and same_binding
                and str(intent.old_server.get("panel_type") or "").lower() == "remnawave"
                and key.server_id == intent.old_server.get("server_name")
                and key.location_revision == intent.previous_revision
                and intent.old_server.get("key_configuration") == key_placement_configuration(key)
            ):
                restored = await asyncio.wait_for(
                    _restore_remnawave_placement(session, key, intent.old_server), timeout=15
                )
                if not restored:
                    raise ValueError("Не удалось восстановить размещение подписки")
            seen = set()
            for server, resource_client_id, lookup_new in (
                (intent.old_server, intent.client_id, False),
                (intent.target_server, intent.target_client_id, True),
            ):
                binding = _binding(server, intent.email)
                resource = binding, resource_client_id
                if resource in seen:
                    continue
                seen.add(resource)
                if key is not None and key.client_id == resource_client_id and binding in protected_bindings:
                    continue
                if not await asyncio.wait_for(
                    _delete_binding(
                        server,
                        resource_client_id,
                        intent.email,
                        intent_id=intent.id,
                        lookup_new=lookup_new,
                        protected_client_id=key.client_id
                        if key is not None and binding in protected_bindings
                        else None,
                    ),
                    timeout=15,
                ):
                    raise ValueError("Панель не подтвердила очистку прежней привязки")
            if now >= _utc(intent.settle_after) and intent.creation_finished:
                await session.delete(intent)
            elif not intent.creation_finished:
                intent.attempts = int(intent.attempts or 0) + 1
                intent.last_error = "Ответ на изменение панели не подтверждён; привязки будут проверены повторно"
                intent.next_attempt_at = max(
                    _utc(intent.settle_after), now + timedelta(seconds=min(3600, 15 * 2 ** min(intent.attempts, 8)))
                )
            else:
                intent.last_error = None
                intent.next_attempt_at = intent.settle_after
            await session.commit()
            return True
        except Exception as error:
            intent.attempts = int(intent.attempts or 0) + 1
            intent.last_error = str(error)
            intent.next_attempt_at = max(
                _utc(intent.settle_after), now + timedelta(seconds=min(3600, 15 * 2 ** min(intent.attempts, 8)))
            )
            await session.commit()
            logger.warning("[KeyCleanup] Очистка {} будет повторена: {}", intent_id, error)
            return False


async def process_key_panel_cleanups(*, sessionmaker=None, limit: int = 10) -> int:
    """Повторяет только явно записанные операции смены локации."""
    maker = sessionmaker or async_session_maker
    async with maker() as session:
        ids = list(
            (
                await session.scalars(
                    select(KeyPanelCleanup.id)
                    .where(KeyPanelCleanup.next_attempt_at <= datetime.now(UTC))
                    .order_by(KeyPanelCleanup.next_attempt_at, KeyPanelCleanup.id)
                    .limit(max(1, min(50, limit)))
                )
            ).all()
        )
    for intent_id in ids:
        await reconcile_key_panel_cleanup(intent_id, sessionmaker=maker)
    return len(ids)


async def key_panel_cleanup_loop(_bot, sessionmaker) -> None:
    """Продолжает сохранённую очистку панелей после перезапуска."""
    while True:
        try:
            await process_key_panel_cleanups(sessionmaker=sessionmaker)
        except Exception as error:
            logger.error("[KeyCleanup] Ошибка обработки очереди: {}", error)
        await asyncio.sleep(30)

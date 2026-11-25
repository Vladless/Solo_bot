from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import SubscriptionEvent
from database.servers import get_servers
from database.tariffs import get_tariff_by_id
from services.cluster_access import _tariff_id, is_server_configured, resolve_tariff_servers
from services.errors import ValidationError
from services.tariffs.renewal_groups import DEFAULT_RENEWAL_FORBIDDEN_GROUPS, is_same_renewal_family
from settings.texts import RENEWAL_TARIFF_UNAVAILABLE


@dataclass
class RenewalScope:
    """Серверы текущей подписки для продления."""

    cluster_name: str
    selected_server: str | None
    servers: list[dict]
    cluster_servers: list[dict]
    binding_scope: list[dict]
    preserve_placement: bool = False


async def resolve_key_renewal_tariff(session: AsyncSession, key) -> dict | None:
    """Восстанавливает прежний тариф подписки из каталога или снимка удаления."""
    raw_id = key.get("tariff_id") if isinstance(key, Mapping) else getattr(key, "tariff_id", None)
    tariff_id = _tariff_id(raw_id)
    if raw_id is not None and tariff_id is None:
        return None
    if tariff_id is not None:
        tariff = await get_tariff_by_id(session, tariff_id)
        if tariff:
            return deepcopy(tariff)
    user_id = key.get("user_id") if isinstance(key, Mapping) else getattr(key, "user_id", None)
    client_id = key.get("client_id") if isinstance(key, Mapping) else getattr(key, "client_id", None)
    user_id = _tariff_id(user_id)
    if user_id is None or not isinstance(client_id, str) or not client_id:
        return None
    statement = select(SubscriptionEvent).where(
        SubscriptionEvent.user_id == user_id,
        SubscriptionEvent.client_id == client_id,
        SubscriptionEvent.event_type == "tariff_deleted",
    )
    if tariff_id is not None:
        statement = statement.where(SubscriptionEvent.tariff_id == tariff_id)
    result = await session.execute(
        statement.order_by(SubscriptionEvent.created_at.desc(), SubscriptionEvent.id.desc()).limit(1)
    )
    event = result.scalar_one_or_none()
    metadata = event.metadata_ if event is not None and isinstance(event.metadata_, dict) else {}
    snapshot = metadata.get("tariff_snapshot")
    if not isinstance(snapshot, dict):
        return None
    snapshot_id = _tariff_id(snapshot.get("id"))
    duration_days = _tariff_id(snapshot.get("duration_days"))
    if snapshot_id is None or duration_days is None or snapshot_id != _tariff_id(event.tariff_id):
        return None
    if not isinstance(event.created_at, datetime):
        return None
    archived_at = event.created_at
    if archived_at.tzinfo is not None:
        archived_at = archived_at.astimezone(UTC).replace(tzinfo=None)
    raw_created = key.get("created_at") if isinstance(key, Mapping) else getattr(key, "created_at", None)
    if raw_created is not None:
        try:
            created_ms = int(raw_created)
        except (TypeError, ValueError, OverflowError):
            return None
        if 0 < created_ms < 10**10:
            created_ms *= 1000
        elif created_ms > 10**13:
            created_ms //= 1000
        if created_ms > int(archived_at.replace(tzinfo=UTC).timestamp() * 1000):
            return None
    newer = await session.execute(
        select(SubscriptionEvent.id)
        .where(
            SubscriptionEvent.user_id == user_id,
            SubscriptionEvent.client_id == client_id,
            SubscriptionEvent.event_type.in_(("created", "renewed")),
            or_(
                SubscriptionEvent.created_at > archived_at,
                and_(SubscriptionEvent.created_at == archived_at, SubscriptionEvent.id > event.id),
            ),
            or_(SubscriptionEvent.event_type == "created", SubscriptionEvent.tariff_id.is_distinct_from(snapshot_id)),
        )
        .limit(1)
    )
    if newer.scalar_one_or_none() is not None:
        return None
    tariff = deepcopy(snapshot)
    tariff["id"] = snapshot_id
    tariff["duration_days"] = duration_days
    return tariff


async def resolve_key_server_scope(
    session: AsyncSession,
    key,
    *,
    servers_map: dict[str, list[dict]] | None = None,
) -> RenewalScope:
    """Находит исходные серверы подписки по сохранённой ссылке."""
    raw_ref = key.get("server_id") if isinstance(key, Mapping) else getattr(key, "server_id", None)
    ref = str(raw_ref or "").strip()
    if not ref or isinstance(raw_ref, bool):
        raise ValidationError(RENEWAL_TARIFF_UNAVAILABLE)
    if servers_map is None:
        servers_map = await get_servers(session, include_enabled=True)
    binding_scope = [server for servers in servers_map.values() for server in servers]
    cluster = servers_map.get(ref)
    if cluster:
        return RenewalScope(ref, None, cluster, cluster, binding_scope)

    selected = next((server for server in binding_scope if server.get("server_name") == ref), None)
    if selected is None and ref.isascii() and ref.isdecimal():
        selected = next((server for server in binding_scope if str(server.get("server_id")) == ref), None)
    if selected is None:
        raise ValidationError(RENEWAL_TARIFF_UNAVAILABLE)
    cluster_name = next(name for name, servers in servers_map.items() if any(server is selected for server in servers))
    return RenewalScope(cluster_name, selected.get("server_name"), [selected], [selected], binding_scope)


async def resolve_key_renewal_scope(
    session: AsyncSession,
    key,
    tariff: dict | None,
    *,
    servers_map: dict[str, list[dict]] | None = None,
) -> RenewalScope:
    """Проверяет доступность тарифа в исходном кластере подписки."""
    if not tariff or not tariff.get("is_active", True) or _tariff_id(tariff.get("id")) is None:
        raise ValidationError(RENEWAL_TARIFF_UNAVAILABLE)
    scope = await resolve_key_server_scope(session, key, servers_map=servers_map)
    candidates = [{**server, "enabled": True} for server in scope.cluster_servers]
    allowed = resolve_tariff_servers(candidates, tariff, binding_scope=scope.binding_scope)
    group_code = str(tariff.get("group_code") or "").strip().lower()
    if not allowed and group_code and group_code not in DEFAULT_RENEWAL_FORBIDDEN_GROUPS:
        global_candidates = [{**server, "enabled": True} for server in scope.binding_scope]
        if not resolve_tariff_servers(global_candidates, tariff, binding_scope=global_candidates):
            current_tariff = await resolve_key_renewal_tariff(session, key)
            own_groups = {str(server.get("tariff_group") or "").strip() for server in scope.cluster_servers}
            legacy_group_match = current_tariff is None and own_groups == {str(tariff.get("group_code") or "").strip()}
            if is_same_renewal_family(current_tariff, tariff) or legacy_group_match:
                allowed = candidates
                scope.preserve_placement = True
    if not allowed or not all(is_server_configured(server) for server in allowed):
        raise ValidationError(RENEWAL_TARIFF_UNAVAILABLE)
    names = {server.get("server_name") for server in allowed}
    scope.servers = [server for server in scope.cluster_servers if server.get("server_name") in names]
    return scope

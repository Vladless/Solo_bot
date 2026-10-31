from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

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
            current_id = key.get("tariff_id") if isinstance(key, Mapping) else getattr(key, "tariff_id", None)
            current_tariff = None
            if current_id:
                current_tariff = (
                    tariff if current_id == tariff.get("id") else await get_tariff_by_id(session, current_id)
                )
            if is_same_renewal_family(current_tariff, tariff):
                allowed = candidates
                scope.preserve_placement = True
    if not allowed or not all(is_server_configured(server) for server in allowed):
        raise ValidationError(RENEWAL_TARIFF_UNAVAILABLE)
    names = {server.get("server_name") for server in allowed}
    scope.servers = [server for server in scope.cluster_servers if server.get("server_name") in names]
    return scope

from __future__ import annotations

import asyncio

from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from database.keys import count_keys_by_server_id, get_all_key_server_ids
from database.servers import (
    filter_cluster_by_subgroup,
    filter_cluster_by_tariff,
    get_panel_type_for_server,
    get_panel_types_for_cluster,
    get_servers,
)
from hooks.processors import process_cluster_balancer, process_cluster_override
from logger import logger
from settings.config import ADMIN_PASSWORD, ADMIN_USERNAME, REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD

from .errors import NotFoundError, ValidationError


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

ALLOWED_GROUP_CODES = ["trial", "discounts", "discounts_max", "cold_discounts", "cold_discounts_max", "gifts"]


@dataclass
class ClusterSelection:
    """Результат выбора кластера."""

    cluster_name: str
    load: int
    available_servers: list[dict[str, Any]]


@dataclass
class ServerAvailability:
    """Результат проверки доступности сервера."""

    server_name: str
    available: bool
    panel_type: str


async def check_server_key_limit(
    server_info: dict[str, Any],
    session: AsyncSession,
    on_capacity_warning: Callable[..., Coroutine] | None = None,
) -> bool:
    """Проверяет, не превышен ли лимит ключей на сервере.

    on_capacity_warning — опциональный callback при >=90% заполненности
    (бот передаёт функцию уведомления админа, API может логировать).
    """
    server_name = server_info.get("server_name")
    cluster_name = server_info.get("cluster_name")
    max_keys = server_info.get("max_keys")

    if not max_keys:
        return True

    identifier = cluster_name if cluster_name else server_name
    total_keys = await count_keys_by_server_id(session, identifier)

    if total_keys >= max_keys:
        logger.warning(f"[Key Limit] Сервер {server_name} достиг лимита: {total_keys}/{max_keys}")
        return False

    usage_percent = total_keys / max_keys
    if usage_percent >= 0.9 and on_capacity_warning:
        try:
            await on_capacity_warning(server_name, total_keys, max_keys)
        except Exception:
            pass

    return True


async def check_server_availability(server_info: dict[str, Any], session: AsyncSession) -> ServerAvailability:
    """Проверяет доступность сервера (enabled + лимит + API ping)."""
    from panels.remnawave_runtime import remnawave_api
    server_name = server_info.get("server_name", "unknown")
    panel_type = (server_info.get("panel_type") or "3x-ui").lower()
    enabled = server_info.get("enabled", True)

    if not enabled:
        return ServerAvailability(server_name=server_name, available=False, panel_type=panel_type)

    max_keys = server_info.get("max_keys")
    if max_keys is not None:
        try:
            total = await count_keys_by_server_id(session, server_name)
            if total >= max_keys:
                return ServerAvailability(server_name=server_name, available=False, panel_type=panel_type)
        except Exception:
            return ServerAvailability(server_name=server_name, available=False, panel_type=panel_type)

    try:
        if panel_type == "remnawave":
            from panels.remnawave import RemnawaveAPI

            async with remnawave_api(server_info["api_url"]) as remna:
                await asyncio.wait_for(remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD), timeout=5.0)
        else:
            from panels._3xui import AsyncApi

            xui = AsyncApi(
                server_info["api_url"],
                username=ADMIN_USERNAME,
                password=ADMIN_PASSWORD,
                logger=logger,
            )
            await asyncio.wait_for(xui.login(), timeout=5.0)
        return ServerAvailability(server_name=server_name, available=True, panel_type=panel_type)
    except Exception:
        logger.warning(f"[Ping] Сервер {server_name} недоступен")
        return ServerAvailability(server_name=server_name, available=False, panel_type=panel_type)


async def select_cluster(
    session: AsyncSession,
    on_capacity_warning: Callable[..., Coroutine] | None = None,
) -> ClusterSelection:
    """Выбирает наименее нагруженный кластер.

    Raises: ValidationError если нет доступных кластеров.
    """
    forced = await process_cluster_override(session=session)
    if isinstance(forced, str) and forced.strip():
        servers = await get_servers(session)
        cluster_servers = servers.get(forced.strip(), [])
        enabled = [s for s in cluster_servers if s.get("enabled", True)]
        if enabled:
            return ClusterSelection(cluster_name=forced.strip(), load=0, available_servers=enabled)

    servers = await get_servers(session)
    server_to_cluster: dict[str, str] = {}
    cluster_loads: dict[str, int] = {}

    for cluster_name, cluster_servers in servers.items():
        cluster_loads[cluster_name] = 0
        for server in cluster_servers:
            server_to_cluster[server["server_name"]] = cluster_name

    key_server_ids = await get_all_key_server_ids(session)
    for sid in key_server_ids:
        cid = server_to_cluster.get(sid, sid)
        if cid in cluster_loads:
            cluster_loads[cid] += 1

    available: dict[str, int] = {}
    cluster_available_servers: dict[str, list] = {}

    for cluster_name, cluster_servers in servers.items():
        enabled = [s for s in cluster_servers if s.get("enabled", True)]
        if not enabled:
            continue
        ok_servers = []
        for s in enabled:
            if await check_server_key_limit(s, session, on_capacity_warning):
                ok_servers.append(s)
        if ok_servers:
            available[cluster_name] = cluster_loads[cluster_name]
            cluster_available_servers[cluster_name] = ok_servers

    filtered = await process_cluster_balancer(available_clusters=available, session=session)
    if filtered:
        available = {k: v for k, v in available.items() if k in filtered}

    if not available:
        raise ValidationError("Сервисы временно недоступны. Попробуйте позже.")

    best = min(available, key=lambda k: (available[k], k))
    logger.info(f"Выбран кластер: {best} (загрузка: {available[best]})")

    return ClusterSelection(
        cluster_name=best,
        load=available[best],
        available_servers=cluster_available_servers.get(best, []),
    )


async def filter_servers_for_key(
    session: AsyncSession,
    cluster_servers: list[dict[str, Any]],
    cluster_id: str,
    tariff_id: int | None = None,
    subgroup_title: str | None = None,
    special_group: str | None = None,
) -> list[dict[str, Any]]:
    """Фильтрует серверы кластера по тарифу, подгруппе и special group.

    Возвращает отфильтрованный список серверов.
    """
    enabled = [s for s in cluster_servers if s.get("enabled", True)]

    if tariff_id:
        filtered = await filter_cluster_by_tariff(session, enabled, tariff_id, cluster_id)
        if filtered:
            enabled = filtered

    if subgroup_title:
        filtered = await filter_cluster_by_subgroup(
            session,
            enabled,
            subgroup_title,
            cluster_id,
            tariff_id=tariff_id,
        )
        if filtered:
            enabled = filtered

    if special_group and special_group in ALLOWED_GROUP_CODES:
        bound = [s for s in enabled if special_group in (s.get("special_groups") or [])]
        if bound:
            enabled = bound

    return enabled


async def is_full_remnawave_cluster(cluster_id: str, session: AsyncSession) -> bool:
    """Проверяет, состоит ли кластер полностью из Remnawave-серверов."""
    panel_types = await get_panel_types_for_cluster(session, cluster_id)
    if panel_types:
        return all(pt.lower() == "remnawave" for pt in panel_types)
    pt = await get_panel_type_for_server(session, cluster_id)
    return bool(pt and pt.lower() == "remnawave")


def resolve_special_group(tariff: dict[str, Any] | None, is_trial: bool = False) -> str | None:
    """Определяет special group для фильтрации серверов."""
    if is_trial:
        return "trial"
    if tariff:
        gc = (tariff.get("group_code") or "").lower()
        if gc in ALLOWED_GROUP_CODES:
            return gc
    return None

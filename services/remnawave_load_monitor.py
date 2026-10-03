from __future__ import annotations

import asyncio

from html import escape as html_escape
from typing import Any

from core.settings.remnawave_config import REMNAWAVE_CONFIG, get_load_monitor_groups, update_remnawave_config
from database import async_session_maker, get_servers
from logger import logger
from panels.remnawave import RemnawaveAPI
from settings.config import ADMIN_ID, REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD, REMNAWAVE_TOKEN_LOGIN_ENABLED


_cycle_lock = asyncio.Lock()


def make_node_key(api_url: str, node_uuid: str) -> str:
    return f"{api_url.rstrip('/')}::{node_uuid}"


def split_node_key(value: str) -> tuple[str, str] | None:
    if "::" not in value:
        return None
    api_url, node_uuid = value.rsplit("::", 1)
    if not api_url or not node_uuid:
        return None
    return api_url.rstrip("/"), node_uuid


async def _configured_panel_urls() -> set[str]:
    async with async_session_maker() as session:
        servers = await get_servers(session, include_enabled=True)
    urls: set[str] = set()
    for cluster in servers.values():
        for server in cluster:
            if server.get("panel_type") != "remnawave":
                continue
            api_url = str(server.get("api_url") or "").strip().rstrip("/")
            if api_url:
                urls.add(api_url)
    return urls


async def _fetch_panel(api_url: str) -> dict[str, Any] | None:
    api = RemnawaveAPI(api_url)
    try:
        if not REMNAWAVE_TOKEN_LOGIN_ENABLED:
            if not await api.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
                logger.warning("[Remnawave-Load] Не удалось авторизоваться на панели {}", api_url)
                return None
        nodes = await api.get_all_nodes()
        hosts = await api.get_hosts()
        if not isinstance(nodes, list) or not isinstance(hosts, list):
            logger.warning("[Remnawave-Load] Не удалось полностью получить ноды и хосты панели {}", api_url)
            return None
        node_map = {str(node.get("uuid")): node for node in nodes if isinstance(node, dict) and node.get("uuid")}
        host_map = {str(host.get("uuid")): host for host in hosts if isinstance(host, dict) and host.get("uuid")}
        inbound_nodes: dict[str, set[str]] = {}
        for node_uuid, node in node_map.items():
            inbounds = (node.get("configProfile") or {}).get("activeInbounds") or []
            for inbound in inbounds:
                if isinstance(inbound, dict) and inbound.get("uuid"):
                    inbound_nodes.setdefault(str(inbound["uuid"]), set()).add(node_uuid)
        return {
            "nodes": node_map,
            "hosts": host_map,
            "inbound_nodes": inbound_nodes,
        }
    except Exception as exc:
        logger.warning("[Remnawave-Load] Ошибка чтения панели {}: {}", api_url, exc)
        return None
    finally:
        try:
            await api.aclose()
        except Exception:
            pass


async def _collect_snapshots(groups: list[dict[str, Any]], managed: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    urls: set[str] = set()
    for group in groups:
        for key in group.get("node_keys") or []:
            parsed = split_node_key(str(key))
            if parsed:
                urls.add(parsed[0])
    for item in managed:
        if isinstance(item, dict) and item.get("api_url"):
            urls.add(str(item["api_url"]).rstrip("/"))

    configured_urls = await _configured_panel_urls()
    urls.intersection_update(configured_urls)
    if not urls:
        return {}

    snapshots: dict[str, dict[str, Any]] = {}
    for api_url in sorted(urls):
        panel = await _fetch_panel(api_url)
        if panel is not None:
            snapshots[api_url] = panel
    return snapshots


def _inbound_uuid(host: dict[str, Any]) -> str | None:
    inbound = host.get("inbound") or {}
    value = inbound.get("configProfileInboundUuid")
    return str(value) if value else None


def _node_is_usable(node: dict[str, Any]) -> bool:
    return bool(node.get("isConnected")) and not bool(node.get("isDisabled"))


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _node_system_metrics(node: dict[str, Any]) -> dict[str, Any]:
    """Собирает системные показатели из GET /nodes; online не используется как нагрузка."""
    system = node.get("system") or {}
    info = system.get("info") or {}
    stats = system.get("stats") or {}
    raw_load = stats.get("loadAvg") or []
    load_avg: list[float | None] = []
    if isinstance(raw_load, list | tuple):
        load_avg = [_number(raw) for raw in raw_load[:3]]

    cores_value = _number(info.get("cpus"))
    cores = int(cores_value) if cores_value else 0
    load_percent = load_avg[0] / cores * 100 if load_avg and load_avg[0] is not None and cores else None
    memory_total = _number(info.get("memoryTotal"))
    memory_used = _number(stats.get("memoryUsed"))
    memory_percent = memory_used / memory_total * 100 if memory_total and memory_used is not None else None

    interface = stats.get("interface") or {}
    if isinstance(interface, list):
        interface = interface[0] if interface and isinstance(interface[0], dict) else {}
    if not isinstance(interface, dict):
        interface = {}
    rx_bytes = _number(interface.get("rxBytesPerSec"))
    tx_bytes = _number(interface.get("txBytesPerSec"))

    online = _number(node.get("usersOnline"))
    return {
        "cores": cores or None,
        "load_avg": load_avg,
        "load_percent": load_percent,
        "memory_percent": memory_percent,
        "rx_mbps": rx_bytes * 8 / 1_000_000 if rx_bytes is not None else None,
        "tx_mbps": tx_bytes * 8 / 1_000_000 if tx_bytes is not None else None,
        "online": int(online) if online is not None else None,
    }


def _group_limit(group: dict[str, Any]) -> int:
    try:
        return max(0, int(group.get("max_cpu_percent") or 0))
    except (TypeError, ValueError):
        return 0


def _tags(host: dict[str, Any]) -> list[str]:
    raw = host.get("tags") or []
    if not isinstance(raw, list):
        return []
    return list(dict.fromkeys(str(tag) for tag in raw if tag))


def _host_matches_node(
    host: dict[str, Any], api_url: str, node_uuid: str, snapshots: dict[str, dict[str, Any]]
) -> bool:
    panel = snapshots.get(api_url)
    inbound_uuid = _inbound_uuid(host)
    if not panel or not inbound_uuid:
        return False
    return node_uuid in panel["inbound_nodes"].get(inbound_uuid, set())


async def get_load_monitor_snapshot() -> list[dict[str, Any]]:
    groups = get_load_monitor_groups()
    managed = REMNAWAVE_CONFIG.get("LOAD_MONITOR_MANAGED_TAGS") or []
    snapshots = await _collect_snapshots(groups, managed if isinstance(managed, list) else [])
    output: list[dict[str, Any]] = []
    for group in groups:
        nodes_out: list[dict[str, Any]] = []
        for raw_key in group.get("node_keys") or []:
            parsed = split_node_key(str(raw_key))
            if not parsed:
                continue
            api_url, node_uuid = parsed
            node = (snapshots.get(api_url) or {}).get("nodes", {}).get(node_uuid)
            if not node:
                nodes_out.append({"key": str(raw_key), "name": node_uuid, "available": False})
                continue
            metrics = _node_system_metrics(node)
            nodes_out.append({
                "key": str(raw_key),
                "name": str(node.get("name") or node.get("address") or node_uuid),
                "available": True,
                "connected": _node_is_usable(node),
                **metrics,
                "max_cpu_percent": _group_limit(group),
            })
        output.append({
            "id": str(group.get("id")),
            "name": str(group.get("name") or "Без названия"),
            "max_cpu_percent": _group_limit(group),
            "routing_tag": str(group.get("routing_tag") or ""),
            "nodes": nodes_out,
        })
    return output


async def _persist_load_state(managed: list[dict[str, Any]], states: dict[str, Any]) -> None:
    current_managed = REMNAWAVE_CONFIG.get("LOAD_MONITOR_MANAGED_TAGS") or []
    current_states = REMNAWAVE_CONFIG.get("LOAD_MONITOR_LAST_STATES") or {}
    if managed == current_managed and states == current_states:
        return
    config = dict(REMNAWAVE_CONFIG)
    config["LOAD_MONITOR_MANAGED_TAGS"] = managed
    config["LOAD_MONITOR_LAST_STATES"] = states
    async with async_session_maker() as session:
        await update_remnawave_config(session, config)


async def restore_managed_load_tags(group_id: str | None = None) -> dict[str, Any]:
    """Возвращает только те routing-теги, которые ранее снял этот монитор."""
    managed = REMNAWAVE_CONFIG.get("LOAD_MONITOR_MANAGED_TAGS") or []
    if not isinstance(managed, list) or not managed:
        return {"restored": 0, "remaining": 0, "errors": []}
    selected = [
        item
        for item in managed
        if isinstance(item, dict) and (group_id is None or str(item.get("group_id")) == str(group_id))
    ]
    if not selected:
        return {"restored": 0, "remaining": len(managed), "errors": []}

    panels = await _collect_snapshots([], selected)
    api_objects: dict[str, RemnawaveAPI] = {}
    remaining: list[dict[str, Any]] = [
        item
        for item in managed
        if not isinstance(item, dict) or (group_id is not None and str(item.get("group_id")) != str(group_id))
    ]
    restored = 0
    errors: list[str] = []
    try:
        for item in selected:
            api_url = str(item.get("api_url") or "").rstrip("/")
            host_uuid = str(item.get("host_uuid") or "")
            tag = str(item.get("tag") or "")
            panel = panels.get(api_url)
            host = (panel or {}).get("hosts", {}).get(host_uuid)
            if not panel or not host or not tag:
                remaining.append(item)
                errors.append(str(host_uuid or api_url or "хост недоступен"))
                continue
            tags = _tags(host)
            if tag not in tags:
                api = api_objects.get(api_url)
                if api is None:
                    api = RemnawaveAPI(api_url)
                    if not REMNAWAVE_TOKEN_LOGIN_ENABLED and not await api.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
                        await api.aclose()
                        remaining.append(item)
                        errors.append(str(host.get("remark") or host_uuid))
                        continue
                    api_objects[api_url] = api
                if not await api.set_host_tags(host_uuid, [*tags, tag]):
                    remaining.append(item)
                    errors.append(str(host.get("remark") or host_uuid))
                    continue
                host["tags"] = [*tags, tag]
            restored += 1
    finally:
        for api in api_objects.values():
            try:
                await api.aclose()
            except Exception:
                pass

    config = dict(REMNAWAVE_CONFIG)
    config["LOAD_MONITOR_MANAGED_TAGS"] = remaining
    async with async_session_maker() as session:
        await update_remnawave_config(session, config)
    return {"restored": restored, "remaining": len(remaining), "errors": errors}


async def run_load_monitor_cycle(bot=None) -> dict[str, Any]:
    """Снимает live-срез и, при включённом пороге, убирает routing-тег с перегруженной ноды."""
    async with _cycle_lock:
        groups = get_load_monitor_groups()
        managed_raw = REMNAWAVE_CONFIG.get("LOAD_MONITOR_MANAGED_TAGS") or []
        managed = (
            [dict(item) for item in managed_raw if isinstance(item, dict)] if isinstance(managed_raw, list) else []
        )
        snapshots = await _collect_snapshots(groups, managed)
        previous_states = REMNAWAVE_CONFIG.get("LOAD_MONITOR_LAST_STATES") or {}
        states = dict(previous_states) if isinstance(previous_states, dict) else {}
        events: list[str] = []
        active_group_ids = {str(group.get("id")) for group in groups}
        group_by_id = {str(group.get("id")): group for group in groups}
        managed_keys = {
            (str(item.get("api_url") or "").rstrip("/"), str(item.get("host_uuid") or ""), str(item.get("tag") or ""))
            for item in managed
        }
        api_objects: dict[str, RemnawaveAPI] = {}
        restored_orphans = 0
        changed_managed = False

        async def get_api(api_url: str) -> RemnawaveAPI | None:
            api_url = api_url.rstrip("/")
            if api_url in api_objects:
                return api_objects[api_url]
            api = RemnawaveAPI(api_url)
            if not REMNAWAVE_TOKEN_LOGIN_ENABLED and not await api.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
                await api.aclose()
                return None
            api_objects[api_url] = api
            return api

        try:
            # First restore tags whose group, node membership, threshold, or tag was removed/changed.
            kept_managed: list[dict[str, Any]] = []
            for item in managed:
                api_url = str(item.get("api_url") or "").rstrip("/")
                host_uuid = str(item.get("host_uuid") or "")
                tag = str(item.get("tag") or "")
                group_id = str(item.get("group_id") or "")
                node_key = str(item.get("node_key") or "")
                group = group_by_id.get(group_id)
                panel = snapshots.get(api_url)
                host = (panel or {}).get("hosts", {}).get(host_uuid)
                if not panel or not host:
                    kept_managed.append(item)
                    continue
                tags = _tags(host)
                if tag in tags:
                    changed_managed = True
                    continue
                selected_keys = {str(key) for key in (group or {}).get("node_keys") or []}
                group_tag = str((group or {}).get("routing_tag") or "")
                limit = _group_limit(group or {})
                orphaned = (
                    group_id not in active_group_ids or node_key not in selected_keys or tag != group_tag or limit <= 0
                )
                parsed = split_node_key(node_key)
                node = None
                if parsed and parsed[0] == api_url:
                    node = panel["nodes"].get(parsed[1])
                if orphaned:
                    api = await get_api(api_url)
                    if api and await api.set_host_tags(host_uuid, [*tags, tag]):
                        host["tags"] = [*tags, tag]
                        restored_orphans += 1
                        changed_managed = True
                    else:
                        kept_managed.append(item)
                    continue
                if not node or not _node_is_usable(node):
                    kept_managed.append(item)
                    continue
                metrics = _node_system_metrics(node)
                load_percent = metrics.get("load_percent")
                if load_percent is None:
                    kept_managed.append(item)
                    continue
                recovery_ratio = float(REMNAWAVE_CONFIG.get("LOAD_MONITOR_RECOVERY_RATIO") or 0.75)
                recovery_limit = limit * min(max(recovery_ratio, 0.1), 0.95)
                if load_percent <= recovery_limit:
                    api = await get_api(api_url)
                    if api and await api.set_host_tags(host_uuid, [*tags, tag]):
                        host["tags"] = [*tags, tag]
                        changed_managed = True
                        states[f"{group_id}::{node_key}"] = {
                            "overloaded": False,
                            "name": str(node.get("name") or node.get("address") or (parsed[1] if parsed else "")),
                            "load_percent": load_percent,
                        }
                        events.append(
                            f"✅ <b>{html_escape(str(node.get('name') or host.get('remark') or 'Нода'))}</b> "
                            f"вернулась в пул (нагрузка {load_percent:.0f}%, порог {limit}%)"
                        )
                    else:
                        kept_managed.append(item)
                else:
                    kept_managed.append(item)
            managed = kept_managed
            managed_keys = {
                (
                    str(item.get("api_url") or "").rstrip("/"),
                    str(item.get("host_uuid") or ""),
                    str(item.get("tag") or ""),
                )
                for item in managed
            }

            for group in groups:
                group_id = str(group.get("id") or "")
                group_name = str(group.get("name") or group_id or "Группа")
                limit = _group_limit(group)
                routing_tag = str(group.get("routing_tag") or "").strip()
                selected_nodes: list[tuple[str, str, dict[str, Any]]] = []
                for raw_key in group.get("node_keys") or []:
                    parsed = split_node_key(str(raw_key))
                    if not parsed:
                        continue
                    api_url, node_uuid = parsed
                    node = (snapshots.get(api_url) or {}).get("nodes", {}).get(node_uuid)
                    if node:
                        selected_nodes.append((api_url, node_uuid, node))
                    state_key = f"{group_id}::{str(raw_key)}"
                    if not node or not _node_is_usable(node):
                        continue
                    old = states.get(state_key) or {}
                    old_overloaded = bool(old.get("overloaded", False))
                    metrics = _node_system_metrics(node)
                    load_percent = metrics.get("load_percent")
                    if load_percent is None:
                        states[state_key] = {
                            **old,
                            "metrics_available": False,
                            "name": str(node.get("name") or node.get("address") or node_uuid),
                        }
                        continue
                    recovery_ratio = float(REMNAWAVE_CONFIG.get("LOAD_MONITOR_RECOVERY_RATIO") or 0.75)
                    recovery_limit = limit * min(max(recovery_ratio, 0.1), 0.95)
                    overloaded = bool(
                        limit and (load_percent >= limit if not old_overloaded else load_percent > recovery_limit)
                    )
                    if limit and overloaded != old_overloaded:
                        node_name = html_escape(str(node.get("name") or node.get("address") or node_uuid))
                        if overloaded:
                            events.append(
                                f"⚠️ <b>{node_name}</b> [{html_escape(group_name)}] выше порога: "
                                f"нагрузка {load_percent:.0f}% при пороге {limit}%"
                            )
                        elif not any(
                            str(item.get("group_id")) == group_id and str(item.get("node_key")) == str(raw_key)
                            for item in managed
                        ):
                            events.append(
                                f"✅ <b>{node_name}</b> [{html_escape(group_name)}] нагрузка снизилась "
                                f"до {load_percent:.0f}% (порог восстановления {recovery_limit:.0f}%)"
                            )
                    states[state_key] = {
                        "overloaded": overloaded,
                        "metrics_available": True,
                        "load_percent": round(load_percent, 2),
                        "online": metrics.get("online"),
                        "memory_percent": round(metrics["memory_percent"], 2)
                        if metrics.get("memory_percent") is not None
                        else None,
                        "name": str(node.get("name") or node.get("address") or node_uuid),
                    }

                if not limit or not routing_tag:
                    continue

                candidate_hosts: list[tuple[str, str, dict[str, Any]]] = []
                for api_url, node_uuid, _node in selected_nodes:
                    panel = snapshots.get(api_url) or {}
                    for host_uuid, host in panel.get("hosts", {}).items():
                        if host.get("isDisabled") or routing_tag not in _tags(host):
                            continue
                        if _host_matches_node(host, api_url, node_uuid, snapshots):
                            candidate_hosts.append((api_url, host_uuid, host))
                active_candidates = len(candidate_hosts)

                for api_url, node_uuid, node in selected_nodes:
                    if not _node_is_usable(node):
                        continue
                    load_percent = _node_system_metrics(node).get("load_percent")
                    if load_percent is None or load_percent < limit:
                        continue
                    node_hosts = [
                        (host_api, host_uuid, host)
                        for host_api, host_uuid, host in candidate_hosts
                        if host_api == api_url and _host_matches_node(host, api_url, node_uuid, snapshots)
                    ]
                    # Keep at least one host in the group's auto-selection pool.
                    if not node_hosts or active_candidates - len(node_hosts) < 1:
                        continue
                    api = await get_api(api_url)
                    if not api:
                        continue
                    for host_api, host_uuid, host in node_hosts:
                        managed_key = (host_api, host_uuid, routing_tag)
                        if managed_key in managed_keys:
                            continue
                        tags = _tags(host)
                        next_tags = [existing for existing in tags if existing != routing_tag]
                        if await api.set_host_tags(host_uuid, next_tags):
                            host["tags"] = next_tags
                            candidate_hosts = [entry for entry in candidate_hosts if entry[1] != host_uuid]
                            active_candidates -= 1
                            managed.append({
                                "group_id": group_id,
                                "node_key": make_node_key(api_url, node_uuid),
                                "api_url": api_url,
                                "host_uuid": host_uuid,
                                "tag": routing_tag,
                            })
                            managed_keys.add(managed_key)
                            changed_managed = True

            await _persist_load_state(managed, states)
        finally:
            for api in api_objects.values():
                try:
                    await api.aclose()
                except Exception:
                    pass

        if bot is not None and events and ADMIN_ID:
            message = "<b>📈 Мониторинг нагрузки Remnawave</b>\n" + "\n".join(events[:30])
            for admin_id in ADMIN_ID:
                try:
                    await bot.send_message(admin_id, message)
                except Exception as exc:
                    logger.warning("[Remnawave-Load] Не удалось отправить уведомление {}: {}", admin_id, exc)

        return {
            "groups": len(groups),
            "panels": len(snapshots),
            "managed_tags": len(managed),
            "restored_orphans": restored_orphans,
            "events": events,
            "state_changed": changed_managed,
        }

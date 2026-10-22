import asyncio

from collections import defaultdict

from database import filter_cluster_by_subgroup, filter_cluster_by_tariff, get_servers, get_tariff_by_id
from database.traffic_notifications import TrafficMeter
from logger import logger
from middlewares.session import operation_session
from panels._3xui import get_client_traffic, get_xui_instance
from panels.remnawave_runtime import remnawave_api
from services.clusters import ALLOWED_GROUP_CODES
from settings.config import REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD, SUPERNODE
from utils.traffic_resources import traffic_resource_id


def _nonnegative_integer(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return None


async def _notification_scope(session, key, servers: dict, tariffs: dict) -> list[dict]:
    scope = servers.get(key.server_id)
    tariff = tariffs.get(key.tariff_id)
    if tariff is None and key.tariff_id:
        tariff = await get_tariff_by_id(session, key.tariff_id)
        if tariff:
            tariffs[key.tariff_id] = tariff
    if scope is None:
        return [s for cluster in servers.values() for s in cluster if s.get("server_name") == key.server_id]
    if tariff is None:
        return scope
    filtered = await filter_cluster_by_tariff(session, scope, key.tariff_id, key.server_id)
    subgroup = tariff.get("subgroup_title")
    if filtered is scope and subgroup:
        filtered = await filter_cluster_by_subgroup(session, scope, subgroup, key.server_id, tariff_id=key.tariff_id)
    group = str(tariff.get("group_code") or "").lower()
    if group in ALLOWED_GROUP_CODES:
        bound = [server for server in filtered if group in (server.get("special_groups") or [])]
        if bound:
            filtered = bound
    return filtered


def _configured_limit(key, tariff: dict | None) -> int | None:
    for value in (
        key.current_traffic_limit,
        key.selected_traffic_limit,
        (tariff or {}).get("traffic_limit"),
    ):
        if value is not None:
            limit = _nonnegative_integer(value)
            return limit * 1024**3 if limit is not None else None
    return None


async def _fetch_remnawave_meters(api_url: str, entries: list[tuple]) -> dict[tuple[int, str], list[TrafficMeter]]:
    result = defaultdict(list)
    async with remnawave_api(api_url) as api:
        users = await asyncio.wait_for(
            api.get_all_users_time(username=REMNAWAVE_LOGIN, password=REMNAWAVE_PASSWORD), timeout=60
        )
    if not isinstance(users, list):
        return result
    profiles = defaultdict(list)
    for user in users:
        if not isinstance(user, dict):
            continue
        for identifier in {user.get("uuid"), user.get("vlessUuid")} - {None, ""}:
            profiles[str(identifier)].append(user)
    for key, server, _fallback_limit in entries:
        matches = profiles.get(str(key.client_id), [])
        if len(matches) != 1:
            continue
        profile = matches[0]
        if profile.get("username") and profile["username"] != key.email:
            continue
        traffic = profile.get("userTraffic")
        if not isinstance(traffic, dict):
            continue
        used = _nonnegative_integer(traffic.get("usedTrafficBytes"))
        limit = _nonnegative_integer(profile.get("trafficLimitBytes"))
        if used is None or limit is None or limit <= 0:
            continue
        result[(int(key.user_id), key.client_id)].append(
            TrafficMeter(
                traffic_resource_id("remnawave", api_url),
                str(server.get("cluster_name") or server.get("server_name") or key.server_id),
                used,
                limit,
            )
        )
    return result


async def _fetch_xui_meter(key, server: dict, fallback_limit: int | None) -> TrafficMeter | None:
    api_url = server["api_url"]
    server_name = str(server.get("server_name") or key.server_id)
    email = f"{key.email}_{server_name.lower()}" if SUPERNODE else key.email
    api = await get_xui_instance(api_url)
    data = await asyncio.wait_for(get_client_traffic(api, key.client_id, email), timeout=30)
    if not isinstance(data, dict) or data.get("status") != "success":
        return None
    rows = data.get("traffic")
    if not isinstance(rows, list) or not rows:
        return None
    inbound_id = server.get("inbound_id")
    rows = [
        row
        for row in rows
        if isinstance(row, dict) and (row.get("inboundId") is None or str(row["inboundId"]) == str(inbound_id))
    ]
    if len(rows) != 1:
        return None
    row = rows[0]
    if row.get("uuid") and row["uuid"] != key.client_id:
        return None
    if row.get("email") and row["email"] != email:
        return None
    up = _nonnegative_integer(row.get("up"))
    down = _nonnegative_integer(row.get("down"))
    if up is None or down is None:
        return None
    limit = None
    for field in ("total", "totalGB"):
        if field in row:
            limit = _nonnegative_integer(row[field])
            break
    else:
        limit = fallback_limit
    if limit is None or limit <= 0:
        return None
    return TrafficMeter(traffic_resource_id("3x-ui", api_url, inbound_id, email), server_name, up + down, limit)


async def collect_traffic_notification_meters(session, keys: list, preload_data: dict | None = None) -> dict:
    """Собирает независимые счётчики только панелей нужных ключей."""
    remnawave = defaultdict(list)
    xui = []
    tariffs = dict((preload_data or {}).get("tariffs_cache", {}))
    async with operation_session(session) as current:
        servers = await get_servers(current)
        for key in keys:
            if _configured_limit(key, tariffs.get(key.tariff_id)) == 0:
                continue
            scope = await _notification_scope(current, key, servers, tariffs)
            fallback_limit = _configured_limit(key, tariffs.get(key.tariff_id))
            if fallback_limit == 0:
                continue
            seen = set()
            for server in scope:
                api_url = str(server.get("api_url") or "")
                panel = str(server.get("panel_type") or "3x-ui").lower()
                identity = (panel, api_url, server.get("inbound_id") if panel == "3x-ui" else None)
                if not api_url or identity in seen:
                    continue
                seen.add(identity)
                if panel == "remnawave":
                    remnawave[api_url].append((key, server, fallback_limit))
                elif panel == "3x-ui":
                    xui.append((key, server, fallback_limit))

    result = defaultdict(list)
    semaphore = asyncio.Semaphore(8)

    async def remna_one(api_url, entries):
        async with semaphore:
            try:
                values = await _fetch_remnawave_meters(api_url, entries)
                for owner, meters in values.items():
                    result[owner].extend(meters)
            except Exception as error:
                logger.warning("[TrafficNotify] Ошибка Remnawave: {}", type(error).__name__)

    async def xui_one(key, server, limit):
        async with semaphore:
            try:
                meter = await _fetch_xui_meter(key, server, limit)
                if meter:
                    result[(int(key.user_id), key.client_id)].append(meter)
            except Exception as error:
                logger.warning("[TrafficNotify] Ошибка 3x-ui: {}", type(error).__name__)

    await asyncio.gather(
        *(remna_one(api_url, entries) for api_url, entries in remnawave.items()),
        *(xui_one(key, server, limit) for key, server, limit in xui),
    )
    return dict(result)

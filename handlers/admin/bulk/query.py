from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_servers
from database.models import Key


DAY_MS = 86400 * 1000


async def _cluster_server_names(session: AsyncSession, cluster_name: str) -> list[str]:
    names = {cluster_name}
    servers = await get_servers(session)
    for cluster_id, server_list in servers.items():
        if cluster_id == cluster_name:
            for s in server_list:
                sn = s.get("server_name")
                if sn:
                    names.add(sn)
    return list(names)


async def fetch_matching_keys(session: AsyncSession, data: dict) -> list[Key]:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    conds = []
    ftype = data.get("filter_type")

    if ftype == "tariff":
        conds.append(Key.tariff_id == int(data["tariff_id"]))
    elif ftype == "cluster":
        names = await _cluster_server_names(session, data["cluster_name"])
        conds.append(Key.server_id.in_(names))
    elif ftype == "created":
        threshold = now_ms - int(data["created_days"]) * DAY_MS
        if data.get("created_dir") == "older":
            conds.append(Key.created_at <= threshold)
        else:
            conds.append(Key.created_at >= threshold)
    elif ftype == "expiry":
        kind = data.get("expiry_kind")
        if kind == "expired":
            conds.append(Key.expiry_time <= now_ms)
        elif kind == "active":
            conds.append(Key.expiry_time > now_ms)
        elif kind == "soon":
            conds.append(Key.expiry_time > now_ms)
            conds.append(Key.expiry_time <= now_ms + int(data["expiry_days"]) * DAY_MS)

    if not conds:
        return []

    result = await session.execute(select(Key).where(*conds))
    return list(result.scalars().all())

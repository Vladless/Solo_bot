from urllib.parse import unquote, urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from core.redis_cache import cache_get, cache_key, cache_set
from database import get_servers
from logger import logger
from panels.remnawave_runtime import with_remnawave_api


HOSTS_PER_PAGE = 20
LINKS_CACHE_TTL_SEC = 150
API_TIMEOUT_SEC = 8.0


async def resolve_remnawave_server_ref(session: AsyncSession, server_id: str | None) -> str | None:
    if not server_id:
        return None

    servers = await get_servers(session)
    cluster = servers.get(server_id)

    if not cluster:
        for cluster_list in servers.values():
            for srv in cluster_list:
                if srv.get("server_name", "").lower() == server_id.lower():
                    cluster = cluster_list
                    break
            if cluster:
                break

    if not cluster:
        return None

    has_remnawave = any(s.get("panel_type", "3x-ui").lower() == "remnawave" for s in cluster)
    return server_id if has_remnawave else None


async def fetch_user_links(
    session: AsyncSession,
    server_id: str,
    username: str,
) -> list[str] | None:
    ckey = cache_key("keys_user_links", str(server_id or ""), username)
    cached = await cache_get(ckey)
    if isinstance(cached, list) and cached:
        return cached

    server_ref = await resolve_remnawave_server_ref(session, server_id)
    if not server_ref:
        logger.warning(
            f"[subscription_keys] Не нашли remnawave-сервер для server_id='{server_id}' (username='{username}')"
        )
        return None

    async def op(api):
        return await api.get_subscription_by_username(username)

    data = await with_remnawave_api(
        session,
        server_ref,
        op,
        fallback_any=True,
        timeout_sec=API_TIMEOUT_SEC,
    )
    if data is None:
        logger.warning(
            f"[subscription_keys] with_remnawave_api вернул None для username='{username}' "
            f"(server_id='{server_id}', server_ref='{server_ref}')"
        )
        return None

    raw_links = data.get("links") or []
    links = [link for link in raw_links if isinstance(link, str) and link.strip()]
    logger.info(
        f"[subscription_keys] API: username='{username}' raw={len(raw_links)} valid={len(links)} "
        f"(server_id='{server_id}')"
    )

    if links:
        await cache_set(ckey, links, LINKS_CACHE_TTL_SEC)

    return links


def host_label(link: str, fallback_idx: int) -> str:
    try:
        parsed = urlparse(link)
        fragment = unquote(parsed.fragment) if parsed.fragment else ""
        if fragment:
            return fragment
        if parsed.hostname:
            return parsed.hostname
    except Exception:
        pass
    return f"Хост #{fallback_idx + 1}"

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from config import HAPP_CRYPTOLINK, LEGACY_LINKS, PUBLIC_LINK, REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD, SUPERNODE
from database import filter_cluster_by_subgroup, get_key_details, get_tariff_by_id
from logger import logger
from panels._3xui import get_vless_link_for_client, get_xui_instance
from panels.remnawave import RemnawaveAPI
from servers import extract_host

from .utils import is_plan_vless, score_vless_url, split_by_panel


async def _is_vless_tariff(session: AsyncSession, email: str) -> bool:
    kd = await get_key_details(session, email)
    if not kd or not kd.get("tariff_id"):
        return False
    tariff = await get_tariff_by_id(session, int(kd["tariff_id"]))
    if not tariff:
        return False
    return is_plan_vless(tariff)


async def _try_build_remna_vless(servers: list, email: str) -> tuple[str | None, str | None]:
    si = servers[0]
    remna = RemnawaveAPI(si["api_url"])
    ok = await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD)
    if not ok:
        logger.warning("[Remnawave] login failed")
        return None, None

    data = await remna.get_subscription_by_username(email)
    if not data:
        logger.warning("[Remnawave] by-username empty")
        return None, None

    links = data.get("links") or []
    best = None
    if links:
        best = max(links, key=score_vless_url)
        if score_vless_url(best) < 0:
            best = None

    happ_link = None
    try:
        happ = data.get("happ") or {}
        if isinstance(happ, dict):
            happ_link = happ.get("cryptoLink") or happ.get("link")
    except Exception:
        pass

    if HAPP_CRYPTOLINK and happ_link:
        return best, happ_link

    sub_url = data.get("subscriptionUrl")
    return best, sub_url


async def _try_build_3xui_vless(servers: list, email: str) -> str | None:
    async def one(si: dict) -> str | None:
        name = si.get("server_name", "unknown")
        inbound_id = si.get("inbound_id")
        if not inbound_id:
            return None
        login_email = f"{email}_{name.lower()}" if SUPERNODE else email
        try:
            xui = await get_xui_instance(si["api_url"])
        except Exception as e:
            logger.warning(f"[{name}] 3x-ui недоступен для VLESS: {e}")
            return None
        try:
            inbound = await xui.inbound.get_by_id(int(inbound_id))
            if not inbound:
                return None
            port = getattr(inbound, "port", None)
            host = extract_host(si.get("subscription_url") or si.get("api_url"))
            return await get_vless_link_for_client(
                xui=xui,
                inbound_id=int(inbound_id),
                email=login_email,
                external_host=host,
                port=int(port) if port else None,
                remark=email,
            )
        except Exception as e:
            logger.warning(f"[{name}] ошибка VLESS: {e}")
            return None

    results = await asyncio.gather(*[one(s) for s in servers], return_exceptions=True)
    return next((r for r in results if isinstance(r, str) and r), None)


async def make_aggregated_link(
    session: AsyncSession,
    cluster_all: list,
    cluster_id: str,
    email: str,
    client_id: str,
    tg_id: int,
    subgroup_code: str | None = None,
    remna_link_override: str | None = None,
    plan=None,
) -> str | None:
    servers = (
        await filter_cluster_by_subgroup(session, cluster_all, subgroup_code, cluster_id)
        if subgroup_code
        else cluster_all
    )
    if not servers:
        logger.info("[agg_link] servers=0 after DB filter")
        return None

    xui, remna = split_by_panel(servers)
    logger.debug(f"[agg_link] subgroup='{subgroup_code}' xui={len(xui)} remna={len(remna)}")

    if plan is None:
        vless_needed = await _is_vless_tariff(session, email)
    elif isinstance(plan, int):
        tr = await get_tariff_by_id(session, plan)
        vless_needed = is_plan_vless(tr)
    else:
        vless_needed = is_plan_vless(plan)

    base = PUBLIC_LINK.rstrip("/")

    if vless_needed:
        if LEGACY_LINKS:
            if xui:
                xui_link = await _try_build_3xui_vless(xui, email)
                if xui_link:
                    logger.info("[agg_link] LEGACY choose 3x-ui VLESS")
                    return xui_link
            logger.info("[agg_link] LEGACY fallback base")
            return f"{base}/{email}/{tg_id}"
        if xui:
            xui_link = await _try_build_3xui_vless(xui, email)
            if xui_link:
                logger.info("[agg_link] choose 3x-ui VLESS")
                return xui_link
        if remna:
            best_vless, sub_url = await _try_build_remna_vless(remna, email)
            if best_vless:
                logger.info("[agg_link] choose Remnawave VLESS")
                return best_vless
            if remna_link_override and remna_link_override.lower().startswith("vless://"):
                logger.info("[agg_link] choose override Remnawave VLESS")
                return remna_link_override
            kd = await get_key_details(session, email)
            stored = kd.get("remnawave_link") if kd else None
            if stored and str(stored).lower().startswith("vless://"):
                logger.info("[agg_link] choose stored Remnawave VLESS")
                return stored
            if sub_url:
                logger.info("[agg_link] choose Remnawave subscriptionUrl")
                return sub_url
        logger.info("[agg_link] fallback base link")
        return f"{base}/{email}/{tg_id}"

    if remna and not xui:
        if LEGACY_LINKS:
            logger.info("[agg_link] LEGACY non-vless -> base link")
            return f"{base}/{email}/{tg_id}"
        best_vless, sub_url = await _try_build_remna_vless(remna, email)
        if remna_link_override and (
            remna_link_override.lower().startswith("vless://") or 
            remna_link_override.startswith("http") or 
            remna_link_override.startswith("happ://")
        ):
            logger.info("[agg_link] choose override Remnawave (non-vless)")
            return remna_link_override
        kd = await get_key_details(session, email)
        stored = kd.get("remnawave_link") if kd else None
        if stored:
            logger.info("[agg_link] choose stored Remnawave (non-vless)")
            return stored
        if sub_url:
            logger.info("[agg_link] choose Remnawave subscriptionUrl (non-vless)")
            return sub_url
        if best_vless:
            logger.info("[agg_link] fallback Remnawave VLESS (non-vless)")
            return best_vless

    return f"{base}/{email}/{tg_id}"

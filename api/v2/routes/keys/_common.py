import asyncio
import re

from base64 import b64encode
from datetime import datetime, timedelta, timezone
from io import BytesIO
from math import ceil
from typing import Any
from urllib.parse import urlsplit

import qrcode

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import (
    get_request_actor,
    get_session,
    validate_redirect_url,
    verify_identity_admin,
    verify_identity_token,
)
from api.v2.base_crud import generate_crud_router
from api.v2.routes.coupon_pricing import resolve_percent_coupon_pricing
from api.v2.schemas import KeyBase, KeyCreateRequest, KeyResponse, KeyUpdate
from api.v2.schemas.web_public import (
    AccountKeyActionResponse,
    AccountKeyActionsAvailability,
    AccountKeyActionsConfigResponse,
    AccountKeyAddonOptionResponse,
    AccountKeyAddonsPreviewRequest,
    AccountKeyAddonsPreviewResponse,
    AccountKeyAliasUpdateRequest,
    AccountKeyApplyAddonsResponse,
    AccountKeyChangeLocationRequest,
    AccountKeyChangeLocationResponse,
    AccountKeyConnectionResponse,
    AccountKeyDetailsResponse,
    AccountKeyLocationOptionResponse,
    AccountKeyLocationsResponse,
    AccountKeyQrResponse,
    AccountKeyRenewRequest,
    AccountKeyRenewResponse,
    AccountKeyResetHwidResponse,
    AccountKeyResponse,
)
from config import (
    ENABLE_DELETE_KEY_BUTTON,
    HWID_RESET_BUTTON,
    INSTRUCTIONS_BUTTON,
    QRCODE,
    REMNAWAVE_LOGIN,
    REMNAWAVE_PASSWORD,
    RENEW_BUTTON_BEFORE_DAYS,
    USE_COUNTRY_SELECTION,
)
from core.bootstrap import BUTTONS_CONFIG, MODES_CONFIG, NOTIFICATIONS_CONFIG, PAYMENTS_CONFIG, TARIFFS_CONFIG
from core.settings.tariffs_config import normalize_tariff_config
from database import (
    check_server_name_by_cluster,
    filter_cluster_by_subgroup,
    get_balance,
    get_key_details,
    get_keys,
    get_tariff_by_id,
    identities as idb,
    save_key_config_with_mode,
    update_balance,
)
from database.access.resolution import resolve_user_optional
from database.coupons import mark_coupon_used
from database.models import Key, Server, ServerSpecialgroup, Tariff
from database.servers import cluster_name_exists, get_cluster_name_for_server_name
from database.temporary_data import create_temporary_data
from handlers.buttons import CONNECT_DEVICE, ROUTER_BUTTON, TV_BUTTON
from handlers.keys.key_view import build_key_view_payload
from handlers.tariffs.addons.key_addons_pack import calc_pack_full_price_rub, get_pack_flags
from handlers.tariffs.addons.utils import calc_remaining_ratio_seconds, is_not_downgrade
from handlers.utils import ALLOWED_GROUP_CODES, is_full_remnawave_cluster
from logger import logger
from panels._3xui import delete_client, get_xui_instance
from panels.remnawave import RemnawaveAPI, get_vless_link_for_remnawave_by_username
from panels.remnawave_runtime import get_remnawave_profile, invalidate_remnawave_profile, with_remnawave_api
from services.operations import (
    create_client_on_server,
    create_key_on_cluster,
    delete_key_from_cluster,
    renew_key_in_cluster,
)
from services.operations.aggregated_links import make_aggregated_link
from services.payments.payment_links import PaymentLinkRequest, create_payment_link
from services.payments.providers import get_web_link_provider_ids
from services.tariffs import calculate_config_price
from services.tariffs.tariff_display import GB, get_effective_limits_for_key, get_key_tariff_addons_state


router = generate_crud_router(
    model=Key,
    schema_response=KeyResponse,
    schema_create=KeyBase,
    schema_update=KeyUpdate,
    identifier_field="tg_id",
    extra_get_by_email=True,
    enabled_methods=["get_all", "get_one", "get_by_email", "get_all_by_field"],
)
user_router = APIRouter()

stats_router = APIRouter()


@stats_router.get("/stats")
async def key_stats(
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Метрики ключей: активные/заморожённые, доля заморозки, корзины истечения."""
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    total = int((await session.execute(select(func.count()).select_from(Key))).scalar() or 0)
    active = int((await session.execute(select(func.count()).select_from(Key).where(Key.expiry_time > now_ms, Key.is_frozen.is_(False)))).scalar() or 0)
    frozen = int((await session.execute(select(func.count()).select_from(Key).where(Key.is_frozen.is_(True)))).scalar() or 0)
    expiring = {}
    for d in (1, 3, 7, 14, 30):
        cnt = (await session.execute(select(func.count()).select_from(Key).where(Key.expiry_time > now_ms, Key.expiry_time <= now_ms + d * 86_400_000, Key.is_frozen.is_(False)))).scalar() or 0
        expiring[f"{d}d"] = int(cnt)
    denom = active + frozen
    return {
        "total_keys": total,
        "active": active,
        "frozen": frozen,
        "frozen_ratio_pct": round(100.0 * frozen / denom, 1) if denom else 0.0,
        "expiring": expiring,
    }



def _renew_available_from_ms(expiry_time_ms: int) -> int:
    renew_before_days = int(NOTIFICATIONS_CONFIG.get("RENEW_BUTTON_BEFORE_DAYS", RENEW_BUTTON_BEFORE_DAYS))
    return int(expiry_time_ms) - renew_before_days * 86_400_000


def _is_renew_available(expiry_time_ms: int) -> bool:
    if not expiry_time_ms:
        return True
    now_ms = int(datetime.utcnow().timestamp() * 1000)
    return now_ms >= _renew_available_from_ms(expiry_time_ms)


def _key_actions_config() -> AccountKeyActionsConfigResponse:
    addons_mode = str(TARIFFS_CONFIG.get("KEY_ADDONS_PACK_MODE", "") or "").strip().lower()
    if addons_mode not in {"", "traffic", "devices", "all"}:
        addons_mode = ""
    addons_enabled_default = addons_mode in {"", "traffic", "devices", "all"}
    return AccountKeyActionsConfigResponse(
        renew_enabled=True,
        delete_enabled=bool(BUTTONS_CONFIG.get("DELETE_KEY_BUTTON_ENABLE", ENABLE_DELETE_KEY_BUTTON)),
        qr_enabled=bool(BUTTONS_CONFIG.get("QRCODE_BUTTON_ENABLE", QRCODE)),
        hwid_reset_enabled=bool(BUTTONS_CONFIG.get("HWID_RESET_BUTTON_ENABLE", HWID_RESET_BUTTON)),
        country_change_enabled=bool(MODES_CONFIG.get("COUNTRY_SELECTION_ENABLED", USE_COUNTRY_SELECTION)),
        instructions_enabled=bool(BUTTONS_CONFIG.get("INSTRUCTIONS_BUTTON_ENABLE", INSTRUCTIONS_BUTTON)),
        addons_enabled=addons_enabled_default,
        addons_mode=addons_mode,
        tv_connect_enabled=bool(BUTTONS_CONFIG.get("ANDROID_TV_BUTTON_ENABLE")),
    )


def _extract_key_actions_from_markup(markup) -> AccountKeyActionsAvailability:
    actions = AccountKeyActionsAvailability()
    rows = getattr(markup, "inline_keyboard", None) or []
    for row in rows:
        for button in row:
            callback_data = str(getattr(button, "callback_data", "") or "")
            text = str(getattr(button, "text", "") or "")
            has_url = bool(getattr(button, "url", None))
            has_web_app = bool(getattr(button, "web_app", None))
            if callback_data.startswith("connect_router|") or text == ROUTER_BUTTON:
                actions.can_connect_router = True
            if callback_data.startswith("connect_tv|") or text == TV_BUTTON:
                actions.can_connect_tv = True
            if callback_data.startswith("connect_device|") or (text == CONNECT_DEVICE and (has_url or has_web_app)):
                actions.can_connect_device = True
            if callback_data.startswith("renew_key|"):
                actions.can_renew = True
            if callback_data.startswith("key_addons|"):
                actions.can_addons = True
            if callback_data.startswith("reset_hwid|"):
                actions.can_reset_hwid = True
            if callback_data.startswith("show_qr|"):
                actions.can_qr = True
            if callback_data.startswith("delete_key|"):
                actions.can_delete = True
            if callback_data.startswith("change_location|"):
                actions.can_change_location = True
    return actions


async def _resolve_available_location_servers(session: AsyncSession, db_key: Key) -> list[str]:
    current_server = str(getattr(db_key, "server_id", "") or "")
    if not current_server:
        return []
    cluster_info = await check_server_name_by_cluster(session, current_server)
    if not cluster_info:
        return []
    cluster_name = str(cluster_info.get("cluster_name") or "")
    if not cluster_name:
        return []
    q = (
        select(
            Server.id,
            Server.server_name,
            Server.api_url,
            Server.panel_type,
            Server.enabled,
            Server.max_keys,
        )
        .where(Server.cluster_name == cluster_name)
        .where(Server.server_name != current_server)
    )
    servers = [dict(m) for m in (await session.execute(q)).mappings().all()]
    if not servers:
        return []
    server_ids = [s["id"] for s in servers if s.get("id") is not None]
    groups_map: dict[int, list[str]] = {}
    if server_ids:
        r = await session.execute(
            select(ServerSpecialgroup.server_id, ServerSpecialgroup.group_code).where(
                ServerSpecialgroup.server_id.in_(server_ids)
            )
        )
        for sid, gc in r.all():
            groups_map.setdefault(int(sid), []).append(gc)
    for server in servers:
        sid_raw = server.get("id")
        sid = int(sid_raw) if sid_raw is not None else -1
        server["special_groups"] = [g for g in groups_map.get(sid, []) if g in ALLOWED_GROUP_CODES]
    key_tariff_id = getattr(db_key, "tariff_id", None)
    subgroup_title = None
    tariff_dict = None
    if key_tariff_id:
        tariff_dict = await get_tariff_by_id(session, int(key_tariff_id))
        if tariff_dict:
            subgroup_title = tariff_dict.get("subgroup_title")
    available_servers = [s for s in servers if bool(s.get("enabled", True))]
    if subgroup_title and available_servers:
        filtered_servers = await filter_cluster_by_subgroup(
            session=session,
            cluster=available_servers,
            target_subgroup=str(subgroup_title).strip(),
            cluster_id=cluster_name,
            tariff_id=int(key_tariff_id) if key_tariff_id else None,
        )
        if filtered_servers:
            available_servers = filtered_servers
        else:
            available_servers = []
    if available_servers and tariff_dict:
        special = None
        gc = str(tariff_dict.get("group_code") or "").lower()
        if gc and gc in ALLOWED_GROUP_CODES:
            special = gc
        if special:
            bound_servers = [s for s in available_servers if special in (s.get("special_groups") or [])]
            if bound_servers:
                available_servers = bound_servers
    names = sorted({
        str(s.get("server_name") or "").strip() for s in available_servers if str(s.get("server_name") or "").strip()
    })
    return names


async def resolve_user_squad_uuids(session: AsyncSession, billing_user_id: int) -> set[str]:
    """Сквады Remnawave, доступные юзеру: кластеры его ключей → все remnawave-серверы
    этих кластеров → Server.inbound_id (= UUID внутреннего сквада). Учитывает, что
    Key.server_id может быть как именем сервера, так и именем кластера.
    Используется блоком «Статус серверов», чтобы показывать только серверы тарифа юзера.
    """
    keys = (await session.execute(select(Key).where(Key.user_id == billing_user_id))).scalars().all()
    if not keys:
        return set()
    cluster_names: set[str] = set()
    for db_key in keys:
        sid = str(getattr(db_key, "server_id", "") or "").strip()
        if not sid:
            continue
        cluster = await get_cluster_name_for_server_name(session, sid)
        if cluster:
            cluster_names.add(str(cluster))
        elif await cluster_name_exists(session, sid):
            cluster_names.add(sid)
    if not cluster_names:
        return set()
    rows = (
        await session.execute(
            select(Server.inbound_id, Server.panel_type, Server.enabled).where(Server.cluster_name.in_(cluster_names))
        )
    ).all()
    squads: set[str] = set()
    for inbound_id, panel_type, enabled in rows:
        if enabled is False:
            continue
        if str(panel_type or "").lower() == "remnawave" and inbound_id:
            squads.add(str(inbound_id))
    return squads


async def _resolve_billing_user_id(request: Request, identity, session: AsyncSession) -> int:
    actor = get_request_actor(request)
    billing_user_id = actor.billing_user_id if actor and actor.billing_user_id is not None else None
    if billing_user_id is None:
        billing_user_id = await idb.ensure_billing_user_for_identity(session, identity)
    return int(billing_user_id)


def _resolve_public_base_url(request: Request) -> str:
    origin = str(request.headers.get("origin") or "").strip()
    if origin.startswith(("http://", "https://")):
        return origin.rstrip("/")
    referer = str(request.headers.get("referer") or request.headers.get("referrer") or "").strip()
    if referer.startswith(("http://", "https://")):
        parsed = urlsplit(referer)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    forwarded_host = str(request.headers.get("x-forwarded-host") or "").strip()
    host = forwarded_host or str(request.headers.get("host") or "").strip()
    forwarded_proto = str(request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip().lower()
    scheme = forwarded_proto if forwarded_proto in {"http", "https"} else request.url.scheme
    if host:
        return f"{scheme}://{host}".rstrip("/")
    return str(request.base_url).rstrip("/")


def _resolve_default_web_payment_provider() -> str | None:
    ids = get_web_link_provider_ids()
    for provider_id in ids:
        if bool(PAYMENTS_CONFIG.get(provider_id)):
            return provider_id
    return ids[0] if ids else None


def _normalize_expiry_ms(raw_value: int | float | None) -> int:
    if not raw_value:
        return 0
    value = int(raw_value)
    if value > 10**13:
        value //= 1000
    elif value < 10**10:
        value *= 1000
    return value

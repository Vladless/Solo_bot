import asyncio
import os
import re
import time

import aiohttp

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_session
from config import (
    BALANCE_BUTTON,
    CAPTCHA_ENABLE,
    CHANNEL_EXISTS,
    CHANNEL_REQUIRED,
    CONNECT_ANDROID,
    CONNECT_IOS,
    CONNECT_MACOS,
    CONNECT_WINDOWS,
    DONATIONS_ENABLE,
    DOWNLOAD_ANDROID,
    DOWNLOAD_IOS,
    DOWNLOAD_MACOS,
    DOWNLOAD_PC,
    GIFT_BUTTON,
    HAPP_CRYPTOLINK,
    HWID_RESET_BUTTON,
    INSTRUCTIONS_BUTTON,
    PROJECT_NAME,
    REFERRAL_BUTTON,
    REFERRAL_QR,
    REMNAWAVE_WEBAPP,
    REMNAWAVE_WEBAPP_OPEN_IN_BROWSER,
    TELEGRAM_WEBAPP_DIRECT_LINK,
    TELEGRAM_WEBAPP_SHORT_NAME,
    TOP_REFERRAL_BUTTON,
    TRIAL_TIME_DISABLE,
    USERNAME_BOT,
    USE_COUNTRY_SELECTION,
)
from core.bootstrap import BUTTONS_CONFIG, MODES_CONFIG, MONEY_CONFIG, PAYMENTS_CONFIG
from core.settings.money_config import get_currency_mode
from core.settings.web_config import WEB_CONFIG
from services.payments.providers import (
    TELEGRAM_ONLY_PROVIDER_IDS,
    get_providers_with_hooks,
    get_web_link_provider_ids,
)


router = APIRouter(tags=["Root"])


def _telegram_web_app_return_base() -> str | None:
    direct = str(TELEGRAM_WEBAPP_DIRECT_LINK or "").strip().rstrip("/")
    if direct:
        if direct.lower().startswith("http://"):
            direct = "https://" + direct[7:]
        if direct.lower().startswith(("https://t.me/", "https://telegram.me/")):
            return direct
    bot = USERNAME_BOT.replace("@", "").strip()
    sn = str(TELEGRAM_WEBAPP_SHORT_NAME or "").strip()
    if bot and sn:
        return f"https://telegram.me/{bot}/{sn}"
    if bot:
        return f"https://telegram.me/{bot}"
    return None


def _partner_feature_enabled() -> bool:
    try:
        from modules.partner_program import settings as partner_settings
    except Exception:
        return False
    for key in ("PARTNER_PROGRAM_ENABLED", "PARTNER_BUTTON_ENABLED", "PARTNER_ENABLED"):
        value = getattr(partner_settings, key, None)
        if isinstance(value, bool):
            return value
    return True


@router.get("/api", include_in_schema=False)
async def root():
    return {"message": "SoloBot API v2", "docs": "/api/docs"}


@router.get("/api/version", include_in_schema=True)
async def version():
    bot_version = ""
    try:
        from utils.versioning import get_version

        bot_version = get_version(include_git_info=False)
    except Exception:
        bot_version = ""
    return {"version": 2, "api": "v2", "bot_version": bot_version}


@router.get("/api/telegram-widget-bot", include_in_schema=True)
async def telegram_widget_bot():
    """Имя бота и имя проекта для веб-клиента."""
    bot_username = str(USERNAME_BOT or "").replace("@", "").strip()
    project_name = (PROJECT_NAME or "Solo").strip() if isinstance(PROJECT_NAME, str) else "Solo"
    telegram_client_id = ""
    try:
        from config import TELEGRAM_CLIENT_ID

        telegram_client_id = str(TELEGRAM_CLIENT_ID).strip()
    except ImportError:
        pass
    return {
        "bot_username": bot_username,
        "project_name": project_name,
        "telegram_client_id": telegram_client_id,
    }


@router.get("/api/site/init-state", include_in_schema=True)
async def site_init_state(session: AsyncSession = Depends(get_session)):
    """Прошёл ли сайт первую настройку админом. Используется middleware веб-клиента."""
    from database.site_state import is_site_initialized

    initialized = await is_site_initialized(session)
    return {"initialized": bool(initialized)}


@router.get("/api/site/revision", include_in_schema=True)
async def site_revision(session: AsyncSession = Depends(get_session)):
    """Глобальный счётчик ревизии контента. Фронт опрашивает его в фоне и при
    изменении инвалидирует свои SWR-кэши, подтягивая свежие правки админа."""
    from database.site_revision import get_site_revision

    revision = await get_site_revision(session)
    return {"revision": int(revision)}


@router.get("/api/site-config", include_in_schema=True)
async def site_config(session: AsyncSession = Depends(get_session)):
    """Настройки витрины и кабинета для веб-клиента (флаги из runtime-конфигов бота)."""
    bot_username = USERNAME_BOT.replace("@", "").strip()
    providers_map = await get_providers_with_hooks(dict(PAYMENTS_CONFIG or {}))
    pay_flags = {name: bool(cfg.get("enabled")) for name, cfg in providers_map.items()}
    any_pay = any(pay_flags.values())
    web_link_set = set(get_web_link_provider_ids())
    tg_only_set = set(TELEGRAM_ONLY_PROVIDER_IDS)
    web_link_provider_ids = [name for name in providers_map if name in web_link_set and pay_flags.get(name)]
    telegram_only_provider_ids = [name for name in providers_map if name in tg_only_set and pay_flags.get(name)]
    currency_mode, currency_one_screen = get_currency_mode()
    try:
        cb_raw = MONEY_CONFIG.get("CASHBACK", 0)
        cashback_percent = float(cb_raw) if cb_raw not in (None, False) else 0.0
    except (TypeError, ValueError):
        cashback_percent = 0.0

    webapp_short = str(TELEGRAM_WEBAPP_SHORT_NAME or "").strip() or None
    webapp_return_base = _telegram_web_app_return_base()

    from api.v2.routes.partners import partners_table_exists

    partner_enabled = bool(_partner_feature_enabled()) and await partners_table_exists(session)

    from sqlalchemy import select

    from database.models import WebFlow

    trial_flow_ids: list[str] = []
    flows_rows = await session.execute(select(WebFlow.id, WebFlow.nodes))
    for flow_id, nodes in flows_rows.all():
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            action_cfg = node.get("action_config")
            if isinstance(action_cfg, dict) and action_cfg.get("action_type") == "activate-trial":
                trial_flow_ids.append(flow_id)
                break

    return {
        "bot_username": bot_username or None,
        "telegram_web_app_short_name": webapp_short,
        "telegram_web_app_return_base": webapp_return_base,
        "project_name": (PROJECT_NAME or "Solo").strip() if isinstance(PROJECT_NAME, str) else "Solo",
        "site_mode": str(WEB_CONFIG.get("SITE_MODE", "full")).strip() or "full",
        "auth": {
            "telegram_login_enabled": bool(bot_username),
            "email_code_login_enabled": bool(MODES_CONFIG.get("WEB_EMAIL_CODE_LOGIN_ENABLED", True)),
        },
        "mobile": {
            "prefer_mini_app_on_telegram_mobile": bool(MODES_CONFIG.get("PREFER_MINI_APP_ON_TELEGRAM_MOBILE", False)),
        },
        "features": {
            "channel_enabled": bool(BUTTONS_CONFIG.get("CHANNEL_BUTTON_ENABLE", CHANNEL_EXISTS)),
            "donations_enabled": bool(BUTTONS_CONFIG.get("DONATIONS_BUTTON_ENABLE", DONATIONS_ENABLE)),
            "balance_enabled": bool(BUTTONS_CONFIG.get("BALANCE_BUTTON_ENABLE", BALANCE_BUTTON)),
            "referral_qr_enabled": bool(BUTTONS_CONFIG.get("REFERRAL_QR_BUTTON_ENABLE", REFERRAL_QR)),
            "instructions_enabled": bool(BUTTONS_CONFIG.get("INSTRUCTIONS_BUTTON_ENABLE", INSTRUCTIONS_BUTTON)),
            "gift_enabled": bool(BUTTONS_CONFIG.get("GIFT_BUTTON_ENABLE", GIFT_BUTTON)),
            "referral_enabled": bool(BUTTONS_CONFIG.get("REFERRAL_BUTTON_ENABLED", REFERRAL_BUTTON)),
            "top_referral_enabled": bool(BUTTONS_CONFIG.get("TOP_REFERRAL_BUTTON_ENABLE", TOP_REFERRAL_BUTTON)),
            "coupon_enabled": bool(BUTTONS_CONFIG.get("COUPON_BUTTON_ENABLE", True)),
            "qr_subscription_enabled": bool(MODES_CONFIG.get("HAPP_CRYPTOLINK_ENABLED", HAPP_CRYPTOLINK)),
            "hwid_reset_enabled": bool(BUTTONS_CONFIG.get("HWID_RESET_BUTTON_ENABLE", HWID_RESET_BUTTON)),
            "country_selection_enabled": bool(MODES_CONFIG.get("COUNTRY_SELECTION_ENABLED", USE_COUNTRY_SELECTION)),
            "captcha_enabled": bool(MODES_CONFIG.get("CAPTCHA_ENABLED", CAPTCHA_ENABLE)),
            "channel_check_enabled": bool(MODES_CONFIG.get("CHANNEL_CHECK_ENABLED", CHANNEL_REQUIRED)),
            "trial_enabled": not bool(MODES_CONFIG.get("TRIAL_TIME_DISABLED", TRIAL_TIME_DISABLE))
            and not bool(MODES_CONFIG.get("WEB_TRIAL_DISABLED", False)),
            "trial_flow_ids": trial_flow_ids,
            "mini_app_enabled": bool(MODES_CONFIG.get("REMNAWAVE_WEBAPP_ENABLED", REMNAWAVE_WEBAPP)),
            "mini_app_open_in_browser": bool(
                MODES_CONFIG.get("REMNAWAVE_WEBAPP_OPEN_IN_BROWSER", REMNAWAVE_WEBAPP_OPEN_IN_BROWSER)
            ),
            "partner_enabled": partner_enabled,
            "support_tickets_enabled": bool(MODES_CONFIG.get("SUPPORT_TICKETS_ENABLED", False)),
        },
        "payments": {
            "any_enabled": any_pay,
            "any_web_link_enabled": bool(web_link_provider_ids),
            "any_telegram_only_enabled": bool(telegram_only_provider_ids),
            "web_link_provider_ids": web_link_provider_ids,
            "telegram_only_provider_ids": telegram_only_provider_ids,
            "yookassa_enabled": pay_flags.get("YOOKASSA", False),
            "yoomoney_enabled": pay_flags.get("YOOMONEY", False),
            "robokassa_enabled": pay_flags.get("ROBOKASSA", False),
            "kassai_cards_enabled": pay_flags.get("KASSAI_CARDS", False),
            "kassai_sbp_enabled": pay_flags.get("KASSAI_SBP", False),
            "tribute_enabled": pay_flags.get("TRIBUTE", False),
            "heleket_enabled": pay_flags.get("HELEKET", False),
            "cryptobot_enabled": pay_flags.get("CRYPTOBOT", False),
            "freekassa_enabled": pay_flags.get("FREEKASSA", False),
            "stars_enabled": pay_flags.get("STARS", False),
        },
        "money": {
            "currency_mode": currency_mode,
            "currency_one_screen": currency_one_screen,
            "cashback_enabled": cashback_percent > 0,
            "cashback_percent": cashback_percent,
        },
        "connect": {
            "ios": str(CONNECT_IOS or "").strip() or None,
            "android": str(CONNECT_ANDROID or "").strip() or None,
            "macos": str(CONNECT_MACOS or "").strip() or None,
            "windows": str(CONNECT_WINDOWS or "").strip() or None,
            "download_ios": str(DOWNLOAD_IOS or "").strip() or None,
            "download_android": str(DOWNLOAD_ANDROID or "").strip() or None,
            "download_macos": str(DOWNLOAD_MACOS or "").strip() or None,
            "download_windows": str(DOWNLOAD_PC or "").strip() or None,
        },
    }


_UPDATE_CHECK_CACHE: dict[str, object] = {"fetched_at": 0.0, "data": None}
_UPDATE_CHECK_LOCK = asyncio.Lock()
_UPDATE_CHECK_TTL_SEC = 600
_SEMVER_RE = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?:-(?P<pre>[0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)


def _parse_semver(tag: str) -> tuple[int, int, int, int, tuple[tuple[int, int | str], ...]] | None:
    """Returns a tuple comparable per semver spec.

    Release > prerelease (second-to-last slot: 1 for release, 0 for prerelease).
    Last slot — tuple of prerelease identifiers; numeric ids compare numerically,
    alphanumeric ids compare lexically, numeric < alphanumeric.
    """
    match = _SEMVER_RE.match(tag.strip())
    if not match:
        return None
    major = int(match.group("major"))
    minor = int(match.group("minor"))
    patch = int(match.group("patch"))
    pre_raw = match.group("pre")
    if not pre_raw:
        return (major, minor, patch, 1, ())
    identifiers: list[tuple[int, int | str]] = []
    for part in pre_raw.split("."):
        if part.isdigit():
            identifiers.append((0, int(part)))
        else:
            identifiers.append((1, part))
    return (major, minor, patch, 0, tuple(identifiers))


async def _fetch_ghcr_tags(image: str) -> list[str]:
    """Возвращает все теги образа в GHCR. Поддерживает paginate через Link header."""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        token_url = f"https://ghcr.io/token?scope=repository:{image}:pull"
        async with session.get(token_url) as token_resp:
            if token_resp.status != 200:
                return []
            token_data = await token_resp.json()
            token = str(token_data.get("token") or "").strip()
            if not token:
                return []
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        all_tags: list[str] = []
        next_url: str | None = f"https://ghcr.io/v2/{image}/tags/list?n=1000"
        guard = 0
        while next_url and guard < 20:
            guard += 1
            async with session.get(next_url, headers=headers) as tags_resp:
                if tags_resp.status != 200:
                    break
                payload = await tags_resp.json()
                page_tags = payload.get("tags") or []
                if isinstance(page_tags, list):
                    all_tags.extend(str(t) for t in page_tags)
                link_header = tags_resp.headers.get("Link") or ""
            next_url = None
            for part in link_header.split(","):
                part = part.strip()
                if not part or 'rel="next"' not in part:
                    continue
                inner = part.split(";", 1)[0].strip()
                if inner.startswith("<") and inner.endswith(">"):
                    inner = inner[1:-1]
                if inner.startswith("/"):
                    next_url = f"https://ghcr.io{inner}"
                else:
                    next_url = inner
                break
        return all_tags


def _is_dev_version(v: str) -> bool:
    return "-dev" in v or "dev." in v


def _latest_for_channel(tags: list[str], dev: bool) -> str | None:
    versions = []
    for raw in tags:
        parsed = _parse_semver(str(raw))
        if parsed is None:
            continue
        is_dev = _is_dev_version(str(raw))
        if is_dev == dev:
            versions.append((parsed, str(raw)))
    if not versions:
        return None
    versions.sort(key=lambda item: item[0], reverse=True)
    return versions[0][1]


@router.get("/api/meta/update-check", include_in_schema=True)
async def update_check(current: str | None = Query(default=None)):
    """Сравнивает переданную версию с последним тегом в GHCR. Канал (dev/release) определяется по current."""
    current_v = (current or "").strip()
    image = (os.environ.get("GHCR_IMAGE") or "vladless/solo-brick").strip()
    if not image:
        return {"current": current_v or None, "latest": None, "hasUpdate": False}

    now = time.time()
    is_dev = _is_dev_version(current_v) if current_v else True
    cache_key = f"data-{'dev' if is_dev else 'release'}"

    cached = _UPDATE_CHECK_CACHE.get(cache_key)
    fetched_at = float(_UPDATE_CHECK_CACHE.get("fetched_at") or 0.0)
    if cached is not None and now - fetched_at < _UPDATE_CHECK_TTL_SEC:
        cached_resp = dict(cached)
        cached_resp["current"] = current_v or None
        if current_v:
            cur = _parse_semver(current_v)
            nxt = _parse_semver(str(cached_resp.get("latest") or ""))
            cached_resp["hasUpdate"] = bool(cur and nxt and nxt > cur)
        return cached_resp

    latest: str | None = None
    async with _UPDATE_CHECK_LOCK:
        fetched_at = float(_UPDATE_CHECK_CACHE.get("fetched_at") or 0.0)
        if now - fetched_at < _UPDATE_CHECK_TTL_SEC:
            cached = _UPDATE_CHECK_CACHE.get(cache_key)
            if cached is not None:
                cached_resp = dict(cached)
                cached_resp["current"] = current_v or None
                if current_v:
                    cur = _parse_semver(current_v)
                    nxt = _parse_semver(str(cached_resp.get("latest") or ""))
                    cached_resp["hasUpdate"] = bool(cur and nxt and nxt > cur)
                return cached_resp
        try:
            tags = await _fetch_ghcr_tags(image)
            latest_dev = _latest_for_channel(tags, dev=True)
            latest_rel = _latest_for_channel(tags, dev=False)
            _UPDATE_CHECK_CACHE["data-dev"] = {"latest": latest_dev, "hasUpdate": False, "image": image}
            _UPDATE_CHECK_CACHE["data-release"] = {"latest": latest_rel, "hasUpdate": False, "image": image}
            _UPDATE_CHECK_CACHE["fetched_at"] = now
            latest = latest_dev if is_dev else latest_rel
        except Exception:
            latest = None

    has_update = False
    if current_v and latest:
        cur = _parse_semver(current_v)
        nxt = _parse_semver(latest)
        if cur and nxt:
            has_update = nxt > cur

    return {
        "current": current_v or None,
        "latest": latest,
        "hasUpdate": has_update,
        "image": image or None,
        "channel": "dev" if is_dev else "release",
        "checkedAt": int(now),
    }

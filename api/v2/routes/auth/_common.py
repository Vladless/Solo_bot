from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from api.v2.schemas.identities import IdentityResponse, LoginResponse
from database import partners as pdb
from logger import logger
from settings.config import API_TOKEN_TTL_DAYS


TOKEN_TTL_HINT = "бессрочно" if API_TOKEN_TTL_DAYS is None else f"{API_TOKEN_TTL_DAYS} дн."
TELEGRAM_LOGIN_MAX_AGE = 3600


def build_login_response(identity) -> LoginResponse:
    return LoginResponse(
        identity_id=identity.id,
        identity=IdentityResponse.model_validate(identity),
    )


def safe_return_path(return_to: str | None, default: str) -> str:
    path = str(return_to or "").strip()
    if not path.startswith("/") or path.startswith(("//", "/\\")):
        return default
    return path


LOGIN_MARKER_PARAM = "auth"


def with_login_marker(path: str) -> str:
    """Помечает возврат после входа: клиент забирает личность из cookie-сессии."""
    if f"{LOGIN_MARKER_PARAM}=1" in path:
        return path
    separator = "&" if "?" in path else "?"
    return f"{path}{separator}{LOGIN_MARKER_PARAM}=1"


_TRUSTED_PROXY_CIDRS: list[str] = []


def _client_ip(request: Request) -> str:
    client_host = (request.client.host if request.client else "") or ""
    forwarded = request.headers.get("x-forwarded-for") or request.headers.get("X-Forwarded-For")
    if not forwarded:
        return client_host
    if not _TRUSTED_PROXY_CIDRS and client_host not in ("127.0.0.1", "::1"):
        return client_host
    return forwarded.split(",")[0].strip() or client_host


async def _resolve_partner_snapshot(session: AsyncSession, billing_user_id: int) -> dict[str, object]:
    partner_feature_enabled = False
    default_percent = 0.0
    try:
        from modules.partner_program import settings as partner_settings

        partner_feature_enabled = True
        raw_percent = getattr(partner_settings, "PARTNER_BONUS_PERCENTAGES", {}).get(1, 0.0)
        default_percent = float(raw_percent) * 100.0
    except Exception:
        partner_feature_enabled = False
        default_percent = 0.0
    partner_table_ok = await pdb.partner_schema_available(session)
    if not partner_table_ok:
        partner_feature_enabled = False
    payload: dict[str, object] = {
        "partner_enabled": partner_feature_enabled,
        "partner_code": "",
        "partner_balance": 0.0,
        "partner_percent": default_percent,
        "partner_percent_custom": False,
        "partner_referred_total": 0,
        "partner_referred_paid": 0,
        "partner_payout_method": None,
    }
    if not partner_table_ok:
        return payload
    try:
        async with session.begin_nested():
            partner_row = await pdb.get_partner_profile(session, billing_user_id)
    except Exception as e:
        logger.warning("[Site:Partner] Не удалось прочитать партнёрские поля клиента: {}", e)
        partner_row = None
    if partner_row is None:
        return payload
    balance = float(partner_row["partner_balance"] or 0.0)
    percent_raw = partner_row["partner_percent"]
    percent_custom = bool(partner_row["partner_percent_custom"])
    percent_value = float(percent_raw) if (percent_custom and percent_raw is not None) else float(default_percent)
    code = str(partner_row["partner_code"] or "").strip()
    try:
        async with session.begin_nested():
            code = await pdb.ensure_partner_code(session, billing_user_id, code)
    except Exception as e:
        logger.warning("[Site:Partner] Не удалось сохранить партнёрский код клиента {}: {}", billing_user_id, e)
    payout_method = str(partner_row["payout_method"] or "").strip() or None
    referred_total = 0
    referred_paid = 0
    try:
        async with session.begin_nested():
            summary = await pdb.get_partner_summary(session, billing_user_id)
            referred_total = summary["referred_count"]
            referred_paid = summary["paid_count"]
    except Exception as e:
        logger.warning("[Site:Partner] Не удалось посчитать приглашённых: {}", e)
    payload.update({
        "partner_enabled": bool(
            partner_table_ok and (partner_feature_enabled or code or referred_total > 0 or balance > 0)
        ),
        "partner_code": code,
        "partner_balance": balance,
        "partner_percent": percent_value,
        "partner_percent_custom": percent_custom,
        "partner_referred_total": referred_total,
        "partner_referred_paid": referred_paid,
        "partner_payout_method": payout_method,
    })
    return payload

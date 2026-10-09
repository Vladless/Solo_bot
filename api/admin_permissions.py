from fastapi import HTTPException

from core.settings.management_config import MANAGEMENT_SETTING_KEY
from database.admins import get_admin_by_tg_id
from database.models import Admin
from filters.admin_actions import AdminActionAccess, granular_permissions_enabled
from filters.permissions import (
    PERM_ADS,
    PERM_CLUSTERS,
    PERM_COUPONS,
    PERM_GIFTS,
    PERM_KEY_CREATE,
    PERM_KEY_DELETE,
    PERM_KEY_DEVICES,
    PERM_KEY_EXPIRY,
    PERM_KEY_FREEZE,
    PERM_KEY_LOCATION,
    PERM_KEY_REISSUE,
    PERM_KEY_TARIFF,
    PERM_KEY_TRAFFIC,
    PERM_KEY_VIEW,
    PERM_MANAGEMENT,
    PERM_MODULES,
    PERM_SETTINGS,
    PERM_STATS,
    PERM_TARIFFS,
    PERM_USERS,
    PERM_USER_BALANCE,
    PERM_USER_BAN,
    normalize_permissions,
)
from settings.config import ADMIN_ID
from settings.texts import ADMIN_ACTION_PERMISSION_DENIED


KEY_FIELD_PERMISSIONS = {
    "expiry_time": PERM_KEY_EXPIRY,
    "tariff_id": PERM_KEY_TARIFF,
    "server_id": PERM_KEY_LOCATION,
    "is_frozen": PERM_KEY_FREEZE,
    "selected_device_limit": PERM_KEY_DEVICES,
    "current_device_limit": PERM_KEY_DEVICES,
    "selected_traffic_limit": PERM_KEY_TRAFFIC,
    "current_traffic_limit": PERM_KEY_TRAFFIC,
    "key": PERM_KEY_REISSUE,
    "remnawave_link": PERM_KEY_REISSUE,
    "email": PERM_KEY_REISSUE,
    "alias": PERM_KEY_VIEW,
    "notified": PERM_KEY_EXPIRY,
    "notified_24h": PERM_KEY_EXPIRY,
    "selected_price_rub": PERM_KEY_TARIFF,
}
LEGACY_RESOURCE_PERMISSIONS = {
    "users": PERM_USERS,
    "keys": PERM_USERS,
    "coupons": PERM_COUPONS,
    "servers": PERM_CLUSTERS,
    "tariffs": PERM_TARIFFS,
    "gifts": PERM_GIFTS,
    "referrals": PERM_USERS,
    "partners": PERM_USERS,
    "payments": PERM_USER_BALANCE,
    "notifications": PERM_USERS,
    "manual-bans": PERM_USER_BAN,
    "blocked-users": PERM_USER_BAN,
    "temporary-data": PERM_USERS,
    "tracking-sources": PERM_ADS,
    "modules": PERM_MODULES,
    "management": PERM_MANAGEMENT,
    "settings": PERM_SETTINGS,
    "stats": PERM_STATS,
}


async def _admin_access(session, principal) -> tuple[AdminActionAccess, frozenset[str]]:
    """Читает актуальную роль и права по Telegram администратора."""
    tg_id = getattr(principal, "tg_id", None)
    configured = (ADMIN_ID,) if isinstance(ADMIN_ID, int) else ADMIN_ID
    if tg_id is not None and int(tg_id) in configured:
        return AdminActionAccess(True, frozenset()), frozenset()
    admin = None
    if tg_id is not None:
        admin = await get_admin_by_tg_id(session, int(tg_id))
    if admin is None:
        if tg_id is None and not isinstance(principal, Admin) and getattr(principal, "is_admin", False):
            return AdminActionAccess(True, frozenset()), frozenset()
        raise HTTPException(status_code=403, detail=ADMIN_ACTION_PERMISSION_DENIED)
    role = (admin.role or "admin").strip().lower()
    if role == "designer":
        raise HTTPException(status_code=403, detail=ADMIN_ACTION_PERMISSION_DENIED)
    permissions = frozenset(normalize_permissions(admin.permissions))
    return AdminActionAccess(role != "moderator", permissions), permissions


async def require_admin_action(session, principal, *required: str) -> None:
    """Запрещает действие без актуального разрешения."""
    if not granular_permissions_enabled():
        return
    access, permissions = await _admin_access(session, principal)
    coarse = (
        PERM_USERS
        if any(
            permission.startswith("key_") or permission in (PERM_USER_BALANCE, PERM_USER_BAN) for permission in required
        )
        else None
    )
    if not access.is_super and coarse and coarse not in permissions:
        raise HTTPException(status_code=403, detail=ADMIN_ACTION_PERMISSION_DENIED)
    if not access.allows(tuple(required)):
        raise HTTPException(status_code=403, detail=ADMIN_ACTION_PERMISSION_DENIED)


async def require_full_admin(session, principal) -> None:
    """Проверяет свежие полные права независимо от общего переключателя."""
    access, _ = await _admin_access(session, principal)
    if not access.is_super:
        raise HTTPException(status_code=403, detail=ADMIN_ACTION_PERMISSION_DENIED)


async def require_key_update_permissions(session, principal, values: dict) -> None:
    """Проверяет каждое изменяемое поле подписки."""
    required = {KEY_FIELD_PERMISSIONS.get(field, PERM_KEY_REISSUE) for field in values}
    await require_admin_action(session, principal, *sorted(required))


async def require_admin_crud_permissions(
    session, principal, model: type, operation: str, values: dict | None = None
) -> None:
    """Защищает универсальные маршруты пользователей и подписок."""
    name = model.__name__
    values = values or {}
    if name == "Key":
        if operation == "update":
            await require_key_update_permissions(session, principal, values)
        else:
            permission = (
                PERM_KEY_CREATE
                if operation == "create"
                else PERM_KEY_DELETE
                if operation == "delete"
                else PERM_KEY_VIEW
            )
            await require_admin_action(session, principal, permission)
    elif name == "User":
        required = []
        if operation == "create":
            required.append(PERM_KEY_CREATE)
        elif operation == "delete":
            required.append(PERM_KEY_DELETE)
        if "balance" in values:
            required.append(PERM_USER_BALANCE)
        if "trial" in values:
            required.append(PERM_KEY_CREATE)
        await require_admin_action(session, principal, *required)
    elif name in ("ManualBan", "BlockedUser"):
        await require_admin_action(session, principal, PERM_USER_BAN)


async def require_legacy_resource_permission(session, admin: Admin, request) -> None:
    """Закрывает обход прав через прежнее API с токеном."""
    if not granular_permissions_enabled():
        return
    access, permissions = await _admin_access(session, admin)
    if access.is_super:
        return
    path = request.url.path if request is not None else ""
    parts = path.strip("/").split("/")
    resource = parts[1] if len(parts) > 1 and parts[0] == "api" else ""
    required = LEGACY_RESOURCE_PERMISSIONS.get(resource)
    if not required:
        raise HTTPException(status_code=403, detail=ADMIN_ACTION_PERMISSION_DENIED)
    setting_key = parts[2] if len(parts) > 2 else ""
    if (
        resource == "settings"
        and request.method not in ("GET", "HEAD", "OPTIONS")
        and setting_key in (MANAGEMENT_SETTING_KEY, "ADMINS")
    ):
        raise HTTPException(status_code=403, detail=ADMIN_ACTION_PERMISSION_DENIED)
    if required.startswith("user_"):
        if PERM_USERS not in permissions or not access.allows((required,)):
            raise HTTPException(status_code=403, detail=ADMIN_ACTION_PERMISSION_DENIED)
    elif required not in permissions:
        raise HTTPException(status_code=403, detail=ADMIN_ACTION_PERMISSION_DENIED)
    if (
        resource == "management"
        and path.rstrip("/").endswith("/restore-trials")
        and not access.allows((PERM_KEY_CREATE,))
    ):
        raise HTTPException(status_code=403, detail=ADMIN_ACTION_PERMISSION_DENIED)
    if resource == "management" and path.rstrip("/").endswith("/backup"):
        raise HTTPException(status_code=403, detail=ADMIN_ACTION_PERMISSION_DENIED)
    if (
        resource == "management"
        and path.rstrip("/").endswith("/change-domain")
        and not access.allows((PERM_KEY_REISSUE,))
    ):
        raise HTTPException(status_code=403, detail=ADMIN_ACTION_PERMISSION_DENIED)

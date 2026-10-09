from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictStr
from sqlalchemy.ext.asyncio import AsyncSession

from api.admin_permissions import require_full_admin
from api.depends import get_session, verify_identity_admin
from database.admins import list_moderators_for_permissions, save_moderator_action_permissions
from database.models import Admin
from filters.admin import invalidate_admin_cache
from filters.permissions import (
    ACTION_PERMISSION_LABELS,
    ALL_ACTION_PERMISSIONS,
    GRANULAR_ACTIONS_CONFIGURED,
    action_permissions,
    normalize_permissions,
)
from settings.buttons import (
    ADMIN_ACTION_PERMISSIONS_SAVE,
    ADMIN_MODERATOR_PERMISSIONS_REFRESH,
    ADMIN_MODERATOR_PERMISSIONS_SAVING,
    CANCEL,
)
from settings.config import ADMIN_ID
from settings.texts import (
    ADMIN_ACCESS_SETTINGS_TITLE,
    ADMIN_ACTION_PERMISSIONS_SAVED,
    ADMIN_MODERATOR_PERMISSIONS_DESCRIPTION,
    ADMIN_MODERATOR_PERMISSIONS_DISABLED,
    ADMIN_MODERATOR_PERMISSIONS_EMPTY,
    ADMIN_MODERATOR_PERMISSIONS_EXPLICIT,
    ADMIN_MODERATOR_PERMISSIONS_INHERITED,
    ADMIN_MODERATOR_PERMISSIONS_INVALID,
    ADMIN_MODERATOR_PERMISSIONS_LOADING,
    ADMIN_MODERATOR_PERMISSIONS_LOAD_ERROR,
    ADMIN_MODERATOR_PERMISSIONS_NOT_FOUND,
    ADMIN_MODERATOR_PERMISSIONS_SAVE_ERROR,
    ADMIN_MODERATOR_PERMISSIONS_SELECT,
)


router = APIRouter()


class ModeratorPermissionsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    permissions: list[StrictStr] = Field(max_length=len(ALL_ACTION_PERMISSIONS))


def _protected_tg_ids() -> tuple[int, ...]:
    """Возвращает Telegram ID главных администраторов."""
    return (ADMIN_ID,) if isinstance(ADMIN_ID, int) else tuple(ADMIN_ID or ())


def _moderator_payload(admin: Admin) -> dict:
    """Возвращает права без токена администратора."""
    selected = action_permissions(admin.permissions)
    return {
        "tg_id": str(admin.tg_id),
        "description": admin.description or "",
        "permissions": [permission for permission in ALL_ACTION_PERMISSIONS if permission in selected],
        "granular_actions_configured": GRANULAR_ACTIONS_CONFIGURED in normalize_permissions(admin.permissions),
    }


@router.get("/moderators/permissions")
async def get_moderator_permissions(
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Показывает полный список настраиваемых прав модераторов."""
    await require_full_admin(session, identity)
    moderators = await list_moderators_for_permissions(session, _protected_tg_ids())
    return {
        "items": [_moderator_payload(admin) for admin in moderators],
        "actions": [{"key": key, "label": label} for key, label in ACTION_PERMISSION_LABELS.items()],
        "copy": {
            "title": ADMIN_ACCESS_SETTINGS_TITLE,
            "description": ADMIN_MODERATOR_PERMISSIONS_DESCRIPTION,
            "select": ADMIN_MODERATOR_PERMISSIONS_SELECT,
            "empty": ADMIN_MODERATOR_PERMISSIONS_EMPTY,
            "inherited": ADMIN_MODERATOR_PERMISSIONS_INHERITED,
            "explicit": ADMIN_MODERATOR_PERMISSIONS_EXPLICIT,
            "disabled": ADMIN_MODERATOR_PERMISSIONS_DISABLED,
            "loading": ADMIN_MODERATOR_PERMISSIONS_LOADING,
            "load_error": ADMIN_MODERATOR_PERMISSIONS_LOAD_ERROR,
            "save_error": ADMIN_MODERATOR_PERMISSIONS_SAVE_ERROR,
            "save": ADMIN_ACTION_PERMISSIONS_SAVE,
            "saved": ADMIN_ACTION_PERMISSIONS_SAVED,
            "saving": ADMIN_MODERATOR_PERMISSIONS_SAVING,
            "cancel": CANCEL,
            "refresh": ADMIN_MODERATOR_PERMISSIONS_REFRESH,
        },
    }


@router.put("/moderators/{tg_id}/permissions")
async def update_moderator_permissions(
    tg_id: int,
    payload: ModeratorPermissionsUpdate,
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Сохраняет только отдельные действия существующего модератора."""
    await require_full_admin(session, identity)
    selected = payload.permissions
    if len(set(selected)) != len(selected) or set(selected).difference(ALL_ACTION_PERMISSIONS):
        raise HTTPException(status_code=400, detail=ADMIN_MODERATOR_PERMISSIONS_INVALID)
    admin = await save_moderator_action_permissions(session, tg_id, selected, _protected_tg_ids())
    if admin is None:
        raise HTTPException(status_code=404, detail=ADMIN_MODERATOR_PERMISSIONS_NOT_FOUND)
    invalidate_admin_cache(tg_id)
    return {"item": _moderator_payload(admin), "message": ADMIN_ACTION_PERMISSIONS_SAVED}

from contextvars import ContextVar
from dataclasses import dataclass

from aiogram.types import InlineKeyboardMarkup

from core.settings.management_config import MANAGEMENT_CONFIG
from filters.permissions import (
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
    PERM_USER_BALANCE,
    PERM_USER_BAN,
    action_permissions,
)


ANY_KEY_LIMIT = "any_key_limit"
FULL_ADMIN = "full_admin"
ACTION_BY_CALLBACK = {
    "users_key_edit": (PERM_KEY_VIEW,),
    "view_key": (PERM_KEY_VIEW,),
    "users_keys_list": (PERM_KEY_VIEW,),
    "users_keys_show": (PERM_KEY_VIEW,),
    "users_create_key": (PERM_KEY_CREATE,),
    "users_reissue_menu": (PERM_KEY_REISSUE,),
    "users_update_key": (PERM_KEY_REISSUE, PERM_KEY_LOCATION),
    "users_recreate_key": (PERM_KEY_REISSUE,),
    "users_delete_key": (PERM_KEY_DELETE,),
    "users_delete_key_confirm": (PERM_KEY_DELETE,),
    "users_delete_user": (PERM_KEY_DELETE,),
    "users_delete_user_confirm": (PERM_KEY_DELETE,),
    "users_expiry_edit": (PERM_KEY_EXPIRY,),
    "users_renew": (PERM_KEY_TARIFF,),
    "users_edit_config": (ANY_KEY_LIMIT,),
    "users_traffic": (PERM_KEY_TRAFFIC,),
    "users_reset_traffic": (PERM_KEY_TRAFFIC,),
    "users_hwid_menu": (PERM_KEY_DEVICES,),
    "users_hwid_page": (PERM_KEY_DEVICES,),
    "users_hwid_unbind": (PERM_KEY_DEVICES,),
    "users_freeze": (PERM_KEY_FREEZE,),
    "users_unfreeze": (PERM_KEY_FREEZE,),
    "users_trial_restore": (PERM_KEY_CREATE,),
    "users_ban": (PERM_USER_BAN,),
    "users_ban_forever": (PERM_USER_BAN,),
    "users_ban_temporary": (PERM_USER_BAN,),
    "users_ban_shadow": (PERM_USER_BAN,),
    "users_unban": (PERM_USER_BAN,),
}
BULK_ACTION_PERMISSIONS = {
    "days": PERM_KEY_EXPIRY,
    "add_days": PERM_KEY_EXPIRY,
    "gb": PERM_KEY_TRAFFIC,
    "add_gb": PERM_KEY_TRAFFIC,
    "freeze": PERM_KEY_FREEZE,
    "unfreeze": PERM_KEY_FREEZE,
    "reissue": PERM_KEY_REISSUE,
    "reissue_link": PERM_KEY_REISSUE,
    "delete": PERM_KEY_DELETE,
}
LIMIT_CALLBACKS = {
    "cfg_base_devices": PERM_KEY_DEVICES,
    "cfg_addon_devices": PERM_KEY_DEVICES,
    "cfg_base_traffic": PERM_KEY_TRAFFIC,
    "cfg_addon_traffic": PERM_KEY_TRAFFIC,
}
LIMIT_PREFIXES = {
    "cfg_set_base_dev:": PERM_KEY_DEVICES,
    "cfg_set_base_traf:": PERM_KEY_TRAFFIC,
    "cfg_renew_devices|": PERM_KEY_DEVICES,
    "cfg_renew_traffic|": PERM_KEY_TRAFFIC,
}


@dataclass(frozen=True)
class AdminActionAccess:
    is_super: bool
    permissions: frozenset[str]

    def allows(self, required: tuple[str, ...]) -> bool:
        """Проверяет разрешения выбранного действия."""
        if self.is_super:
            return True
        allowed = action_permissions(self.permissions)
        return all(
            bool(allowed.intersection((PERM_KEY_DEVICES, PERM_KEY_TRAFFIC)))
            if permission == ANY_KEY_LIMIT
            else permission in allowed
            for permission in required
        )


ADMIN_ACTION_ACCESS: ContextVar[AdminActionAccess | None] = ContextVar("admin_action_access", default=None)


def granular_permissions_enabled() -> bool:
    """Проверяет включение детальных прав."""
    return bool(MANAGEMENT_CONFIG.get("ADMIN_GRANULAR_PERMISSIONS_ENABLED", False))


def admin_action_allowed(permission: str) -> bool:
    """Проверяет показ данных внутри уже разрешённого действия."""
    access = ADMIN_ACTION_ACCESS.get()
    return access is None or access.allows((permission,))


def callback_action_permissions(value: str | None, state_data: dict | None = None) -> tuple[str, ...]:
    """Определяет права кнопки, включая старые обратные вызовы."""
    value = value or ""
    state_data = state_data or {}
    parts = value.split(":")
    if parts[0] in ("admin_user", "admin_users") and len(parts) > 1:
        action = parts[1]
        if action.startswith("users_balance_"):
            return (PERM_USER_BALANCE,)
        return ACTION_BY_CALLBACK.get(action, ())
    if parts[0] in ("admin_user_key", "admin_users_key") and len(parts) > 1:
        return (PERM_KEY_EXPIRY,) if parts[1] in ("add", "take", "set") else ()
    if parts[0] == "abulk" and len(parts) > 1:
        action = parts[2] if parts[1] == "action" and len(parts) > 2 else state_data.get("action")
        permission = BULK_ACTION_PERMISSIONS.get(action)
        return (permission,) if permission and parts[1] != "back_actions" else ()
    if parts[0] == "admin_panel" and len(parts) > 1:
        action = parts[1]
        if action in (
            "admins",
            "add_admin",
            "upload_file",
            "restore_db",
            "restore_db_local",
            "backups",
        ) or action.startswith((
            "add_role|",
            "admin_menu|",
            "generate_token|",
            "edit_role|",
            "set_role|",
            "edit_perms|",
            "toggle_perm|",
            "action_perms|",
            "action_toggle|",
            "action_save|",
            "delete_admin|",
            "restore_local|",
        )):
            return (FULL_ADMIN,)
        if action in ("restore_trials", "confirm_restore_trials"):
            return (PERM_KEY_CREATE,)
        if action in ("request_3xui_file", "export_remnawave"):
            return (PERM_KEY_CREATE,)
        if action == "resync_after_import":
            return (PERM_KEY_REISSUE,)
        if action == "change_domain":
            return (PERM_KEY_REISSUE,)
        if action.startswith(("bans_", "shadow_bans_", "manual_bans_")) or action == "bans":
            return (PERM_USER_BAN, PERM_KEY_DELETE) if action == "bans_delete_banned" else (PERM_USER_BAN,)
    if parts[0] == "admin_cluster" and len(parts) > 1:
        if parts[1] == "add_time":
            return (PERM_KEY_EXPIRY,)
        if parts[1] in ("sync", "sync-server", "sync-cluster"):
            return (PERM_KEY_REISSUE,)
        if parts[1] == "backup":
            return (FULL_ADMIN,)
    if value.startswith(("transfer_to_server|", "transfer_to_cluster|")):
        return (PERM_KEY_LOCATION,)
    if value.startswith("upload_target:"):
        return (FULL_ADMIN,)
    if value.startswith((
        "confirm_admin_key_reissue|",
        "admin_key_reissue|",
        "admin_reissue_country|",
        "admin_reissue_server|",
    )):
        return (PERM_KEY_REISSUE, PERM_KEY_LOCATION)
    if value.startswith("confirm_recreate|"):
        return (PERM_KEY_REISSUE,)
    if value in LIMIT_CALLBACKS:
        return (LIMIT_CALLBACKS[value],)
    for prefix, permission in LIMIT_PREFIXES.items():
        if value.startswith(prefix):
            return (permission, PERM_KEY_TARIFF) if prefix.startswith("cfg_renew_") else (permission,)
    if value == "cfg_save":
        touched = state_data.get("admin_config_permissions")
        return tuple(touched) or (ANY_KEY_LIMIT,) if isinstance(touched, list) else (PERM_KEY_DEVICES, PERM_KEY_TRAFFIC)
    if value.startswith("cfg_renew_apply|"):
        touched = state_data.get("admin_config_permissions")
        return (PERM_KEY_TARIFF, *(touched if isinstance(touched, list) else (PERM_KEY_DEVICES, PERM_KEY_TRAFFIC)))
    if value in ("cfg_edit_base", "cfg_edit_addon", "cfg_back_menu", "cfg_cancel_input"):
        return (ANY_KEY_LIMIT,)
    if value.startswith(("group:", "confirm:")) or value in ("back:renew", "back:group"):
        return (PERM_KEY_TARIFF,)
    return ()


def state_action_permissions(state: str | None, data: dict) -> tuple[str, ...]:
    """Проверяет разрешения продолжения сохранённого сценария."""
    if state == "UserEditorState:waiting_for_balance":
        return (PERM_USER_BALANCE,)
    if state and state.startswith(("AdminState:", "DatabaseState:", "FileUploadState:")):
        return (FULL_ADMIN,)
    if state == "AdminClusterStates:waiting_for_days_input":
        return (PERM_KEY_EXPIRY,)
    if state == "AdminManagementStates:waiting_for_new_domain":
        return (PERM_KEY_REISSUE,)
    if state == "Import3xuiStates:waiting_for_file":
        return (PERM_KEY_CREATE,)
    if state == "UserEditorState:waiting_for_expiry_time":
        return (PERM_KEY_EXPIRY,)
    if state == "UserEditorState:confirm_delete_key":
        return (PERM_KEY_DELETE,)
    if state in (
        "UserEditorState:selecting_country",
        "UserEditorState:selecting_cluster",
        "UserEditorState:selecting_duration",
    ):
        return (PERM_KEY_CREATE,)
    if state == "UserEditorState:config_input_addon":
        return (PERM_KEY_DEVICES,) if data.get("cfg_param") == "devices" else (PERM_KEY_TRAFFIC,)
    if state and state.startswith("RenewTariffState:"):
        return (PERM_KEY_TARIFF,)
    if state and state.startswith(("BanUserStates:", "PreemptiveBanStates:")):
        return (PERM_USER_BAN,)
    if state and state.startswith("BulkStates:"):
        permission = BULK_ACTION_PERMISSIONS.get(data.get("action"))
        return (permission,) if permission else ()
    return ()


def filter_admin_markup(
    markup: InlineKeyboardMarkup, access: AdminActionAccess, data: dict | None = None
) -> InlineKeyboardMarkup:
    """Скрывает недоступные действия в любой клавиатуре админки."""
    rows = [
        [button for button in row if access.allows(callback_action_permissions(button.callback_data, data))]
        for row in markup.inline_keyboard
    ]
    return markup.model_copy(update={"inline_keyboard": [row for row in rows if row]})

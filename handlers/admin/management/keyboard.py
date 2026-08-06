from collections.abc import Iterable

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.bootstrap import MANAGEMENT_CONFIG
from filters.permissions import PERM_ADMINS, PERM_MANAGEMENT
from settings.buttons import BACK

from ..panel.keyboard import AdminPanelCallback, build_admin_back_btn


def build_management_kb(
    admin_role: str,
    permissions: Iterable[str] | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    is_super = admin_role == "superadmin"
    perm_set = frozenset(permissions or ())

    def can(perm: str) -> bool:
        return is_super or perm in perm_set

    if can(PERM_ADMINS):
        builder.button(
            text="👑 Админы",
            callback_data=AdminPanelCallback(action="admins").pack(),
        )

    if not can(PERM_MANAGEMENT):
        builder.adjust(2)
        builder.row(build_admin_back_btn())
        return builder.as_markup()

    builder.button(
        text="🗄 База данных",
        callback_data=AdminPanelCallback(action="database").pack(),
    )
    builder.button(
        text="📛 Баны",
        callback_data=AdminPanelCallback(action="bans").pack(),
    )
    builder.button(
        text="🔄 Перезапуск",
        callback_data=AdminPanelCallback(action="restart").pack(),
    )
    builder.button(
        text="🌐 Домен",
        callback_data=AdminPanelCallback(action="change_domain").pack(),
    )
    builder.button(
        text="🔑 Пробный период",
        callback_data=AdminPanelCallback(action="restore_trials").pack(),
    )
    builder.button(
        text="📤 Файлы",
        callback_data=AdminPanelCallback(action="upload_file").pack(),
    )

    maintenance_enabled = bool(MANAGEMENT_CONFIG.get("MAINTENANCE_ENABLED", False))
    maintenance_text = "🛠 Снять тех. работы" if maintenance_enabled else "🛠 Тех. работы"
    builder.button(
        text=maintenance_text,
        callback_data=AdminPanelCallback(action="toggle_maintenance").pack(),
    )

    builder.adjust(2)
    builder.row(build_admin_back_btn())
    return builder.as_markup()


def build_database_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text="💾 Создать копию",
        callback_data=AdminPanelCallback(action="backups").pack(),
    )
    builder.button(
        text="♻️ Из файла",
        callback_data=AdminPanelCallback(action="restore_db").pack(),
    )
    builder.button(
        text="🖥 С сервера",
        callback_data=AdminPanelCallback(action="restore_db_local").pack(),
    )
    builder.button(
        text="📤 Импорт с панели",
        callback_data=AdminPanelCallback(action="export_db").pack(),
    )
    builder.adjust(2)
    builder.row(build_admin_back_btn())
    return builder.as_markup()


def build_back_to_db_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=BACK, callback_data=AdminPanelCallback(action="back_to_db_menu").pack())
    builder.adjust(1)
    return builder.as_markup()


def build_export_db_sources_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(text="🌀 Remnawave", callback_data=AdminPanelCallback(action="export_remnawave").pack())
    builder.button(text="🧩 3x-ui", callback_data=AdminPanelCallback(action="request_3xui_file").pack())
    builder.adjust(2)
    builder.button(text=BACK, callback_data=AdminPanelCallback(action="back_to_db_menu").pack())
    builder.adjust(2, 1)
    return builder.as_markup()


def build_admins_kb(admins: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for tg_id, role in admins:
        builder.button(
            text=f"🧑 {tg_id} ({role})", callback_data=AdminPanelCallback(action=f"admin_menu|{tg_id}").pack()
        )

    builder.button(text="➕ Добавить админа", callback_data=AdminPanelCallback(action="add_admin").pack())
    builder.row(build_admin_back_btn())
    builder.adjust(1)
    return builder.as_markup()


def build_single_admin_menu(tg_id: int, role: str = "moderator") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(text="✏ Изменить роль", callback_data=AdminPanelCallback(action=f"edit_role|{tg_id}").pack())
    if role != "superadmin":
        builder.button(
            text="🔐 Настроить права",
            callback_data=AdminPanelCallback(action=f"edit_perms|{tg_id}").pack(),
        )
    builder.button(text="🗑 Удалить админа", callback_data=AdminPanelCallback(action=f"delete_admin|{tg_id}").pack())

    if role == "superadmin":
        builder.button(
            text="🎟 Выпустить токен", callback_data=AdminPanelCallback(action=f"generate_token|{tg_id}").pack()
        )

    builder.adjust(2)
    builder.row(InlineKeyboardButton(text=BACK, callback_data=AdminPanelCallback(action="admins").pack()))
    return builder.as_markup()


def build_admin_permissions_kb(tg_id: int, current: set[str]) -> InlineKeyboardMarkup:
    from filters.permissions import PERMISSION_LABELS

    builder = InlineKeyboardBuilder()
    for perm_id, label in PERMISSION_LABELS.items():
        mark = "✅" if perm_id in current else "⬜"
        builder.button(
            text=f"{mark} {label}",
            callback_data=AdminPanelCallback(action=f"toggle_perm|{tg_id}|{perm_id}").pack(),
        )
    builder.button(text=BACK, callback_data=AdminPanelCallback(action=f"admin_menu|{tg_id}").pack())
    builder.adjust(1)
    return builder.as_markup()


def build_new_admin_role_kb(tg_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🛡 moderator", callback_data=AdminPanelCallback(action=f"add_role|{tg_id}|moderator").pack())
    builder.button(text="🎨 designer", callback_data=AdminPanelCallback(action=f"add_role|{tg_id}|designer").pack())
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text=BACK, callback_data=AdminPanelCallback(action="admins").pack()))
    return builder.as_markup()


def build_role_selection_kb(tg_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👑 superadmin", callback_data=AdminPanelCallback(action=f"set_role|{tg_id}|superadmin").pack())
    builder.button(text="🛡 moderator", callback_data=AdminPanelCallback(action=f"set_role|{tg_id}|moderator").pack())
    builder.button(text="🎨 designer", callback_data=AdminPanelCallback(action=f"set_role|{tg_id}|designer").pack())
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text=BACK, callback_data=AdminPanelCallback(action=f"admin_menu|{tg_id}").pack()))
    return builder.as_markup()


def build_admin_back_kb_to_admins() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=BACK, callback_data=AdminPanelCallback(action="admins").pack())
    builder.adjust(1)
    return builder.as_markup()


def build_token_result_kb(token: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Скопировать токен", switch_inline_query_current_chat=token)
    builder.button(text=BACK, callback_data=AdminPanelCallback(action="admins").pack())
    builder.adjust(1)
    return builder.as_markup()


def build_post_import_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔁 Перевыпустить", callback_data=AdminPanelCallback(action="resync_after_import").pack())
    builder.button(text=BACK, callback_data=AdminPanelCallback(action="back_to_db_menu").pack())
    builder.adjust(1)
    return builder.as_markup()

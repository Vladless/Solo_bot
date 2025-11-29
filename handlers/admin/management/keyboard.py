from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.bootstrap import MANAGEMENT_CONFIG

from ..panel.keyboard import AdminPanelCallback, build_admin_back_btn


def build_management_kb(admin_role: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if admin_role == "superadmin":
        builder.button(
            text="👑 Управление админами",
            callback_data=AdminPanelCallback(action="admins").pack(),
        )

    builder.button(
        text="🗄 Управление БД",
        callback_data=AdminPanelCallback(action="database").pack(),
    )
    builder.button(
        text="📛 Управление банами",
        callback_data=AdminPanelCallback(action="bans").pack(),
    )
    builder.button(
        text="🔄 Перезагрузить бота",
        callback_data=AdminPanelCallback(action="restart").pack(),
    )
    builder.button(
        text="🌐 Сменить домен подписок",
        callback_data=AdminPanelCallback(action="change_domain").pack(),
    )
    builder.button(
        text="🔑 Восстановить пробники",
        callback_data=AdminPanelCallback(action="restore_trials").pack(),
    )
    builder.button(
        text="📤 Загрузить файл",
        callback_data=AdminPanelCallback(action="upload_file").pack(),
    )

    maintenance_enabled = bool(MANAGEMENT_CONFIG.get("MAINTENANCE_ENABLED", False))
    maintenance_text = "🛠️ Выключить тех. работы" if maintenance_enabled else "🛠️ Включить тех. работы"
    builder.button(
        text=maintenance_text,
        callback_data=AdminPanelCallback(action="toggle_maintenance").pack(),
    )

    builder.row(build_admin_back_btn())
    builder.adjust(1)
    return builder.as_markup()


def build_database_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text="💾 Создать резервную копию",
        callback_data=AdminPanelCallback(action="backups").pack(),
    )
    builder.button(
        text="♻️ Восстановить БД из бэкапа",
        callback_data=AdminPanelCallback(action="restore_db").pack(),
    )
    builder.button(
        text="📤 Получить данные БД из панели",
        callback_data=AdminPanelCallback(action="export_db").pack(),
    )
    builder.row(build_admin_back_btn())
    builder.adjust(1)
    return builder.as_markup()


def build_back_to_db_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Назад", callback_data=AdminPanelCallback(action="back_to_db_menu").pack())
    builder.adjust(1)
    return builder.as_markup()


def build_export_db_sources_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(text="🌀 Remnawave", callback_data=AdminPanelCallback(action="export_remnawave").pack())
    builder.button(text="🧩 3x-ui", callback_data=AdminPanelCallback(action="request_3xui_file").pack())
    builder.button(text="🔙 Назад", callback_data=AdminPanelCallback(action="back_to_db_menu").pack())

    builder.adjust(1)
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
    builder.button(text="🗑 Удалить админа", callback_data=AdminPanelCallback(action=f"delete_admin|{tg_id}").pack())

    if role == "superadmin":
        builder.button(
            text="🎟 Выпустить токен", callback_data=AdminPanelCallback(action=f"generate_token|{tg_id}").pack()
        )

    builder.button(text="🔙 Назад", callback_data=AdminPanelCallback(action="admins").pack())

    builder.adjust(1)
    return builder.as_markup()


def build_role_selection_kb(tg_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👑 superadmin", callback_data=AdminPanelCallback(action=f"set_role|{tg_id}|superadmin").pack())
    builder.button(text="🛡 moderator", callback_data=AdminPanelCallback(action=f"set_role|{tg_id}|moderator").pack())
    builder.button(text="🔙 Назад", callback_data=AdminPanelCallback(action=f"admin_menu|{tg_id}").pack())
    builder.adjust(1)
    return builder.as_markup()


def build_admin_back_kb_to_admins() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data=AdminPanelCallback(action="admins").pack())
    builder.adjust(1)
    return builder.as_markup()


def build_token_result_kb(token: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Скопировать токен", switch_inline_query_current_chat=token)
    builder.button(text="🔙 Назад", callback_data=AdminPanelCallback(action="admins").pack())
    builder.adjust(1)
    return builder.as_markup()


def build_post_import_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🔁 Перевыпустить подписки", callback_data=AdminPanelCallback(action="resync_after_import").pack()
    )
    builder.button(text="🔙 Назад", callback_data=AdminPanelCallback(action="back_to_db_menu").pack())
    builder.adjust(1)
    return builder.as_markup()

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from handlers.admin.panel.keyboard import AdminPanelCallback
from handlers.buttons import BACK
from utils.modules_manager import manager


def build_modules_kb(page: int, total_pages: int, items: list[tuple[str, str | None]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    row_buf = []
    for name, _ in items:
        label = name if manager.is_enabled(name) else f"{name} (off)"
        row_buf.append(
            InlineKeyboardButton(
                text=label,
                callback_data=AdminPanelCallback(action=f"module__{name}", page=page).pack(),
            )
        )
        if len(row_buf) == 2:
            builder.row(*row_buf)
            row_buf = []
    if row_buf:
        builder.row(*row_buf)

    if total_pages > 1:
        nav = []
        if page > 1:
            nav.append(
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=AdminPanelCallback(action="modules", page=page - 1).pack(),
                )
            )
        nav.append(
            InlineKeyboardButton(
                text=f"{page}/{total_pages}",
                callback_data=AdminPanelCallback(action="modules", page=page).pack(),
            )
        )
        if page < total_pages:
            nav.append(
                InlineKeyboardButton(
                    text="Вперед ➡️",
                    callback_data=AdminPanelCallback(action="modules", page=page + 1).pack(),
                )
            )
        builder.row(*nav)

    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminPanelCallback(action="admin", page=1).pack(),
        )
    )

    return builder.as_markup()


def build_module_menu_kb(name: str, page: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    enabled = manager.is_enabled(name)

    if enabled:
        builder.button(
            text="🔁 Перезапустить",
            callback_data=AdminPanelCallback(action=f"module_restart__{name}", page=page).pack(),
        )
        builder.button(
            text="🛑 Остановить",
            callback_data=AdminPanelCallback(action=f"module_stop__{name}", page=page).pack(),
        )
    else:
        builder.button(
            text="▶️ Запустить",
            callback_data=AdminPanelCallback(action=f"module_start__{name}", page=page).pack(),
        )

    builder.button(
        text="🔄 Обновить",
        callback_data=AdminPanelCallback(action=f"module_update__{name}", page=page).pack(),
    )

    builder.row(
        InlineKeyboardButton(
            text="⬅️ К списку",
            callback_data=AdminPanelCallback(action="modules", page=page).pack(),
        )
    )
    builder.adjust(1)
    return builder.as_markup()

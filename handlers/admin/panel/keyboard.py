from collections.abc import Iterable
from typing import Any

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from filters.permissions import (
    PERM_ADMINS,
    PERM_ADS,
    PERM_BROADCASTING,
    PERM_BULK,
    PERM_CLUSTERS,
    PERM_COUPONS,
    PERM_EMOJI,
    PERM_GIFTS,
    PERM_MANAGEMENT,
    PERM_MODULES,
    PERM_SETTINGS,
    PERM_STATS,
    PERM_TARIFFS,
    PERM_USERS,
)
from hooks.hook_buttons import insert_hook_buttons
from hooks.hooks import run_hooks
from settings.buttons import BACK, MAIN_MENU


class AdminPanelCallback(CallbackData, prefix="admin_panel"):
    action: str
    page: int

    def __init__(self, /, **data: Any) -> None:
        if "page" not in data or data["page"] is None:
            data["page"] = 1
        super().__init__(**data)


_ADMIN_MENU: tuple[tuple[tuple[str, str, tuple[str, ...]], ...], ...] = (
    (
        ("🔍 Поиск", "search_user", (PERM_USERS,)),
        ("📦 Массовые", "bulk", (PERM_BULK,)),
    ),
    (
        ("🖥️ Серверы", "clusters", (PERM_CLUSTERS,)),
        ("💸 Тарифы", "tariffs", (PERM_TARIFFS,)),
    ),
    (
        ("📢 Рассылка", "sender", (PERM_BROADCASTING,)),
        ("🎟️ Купоны", "coupons", (PERM_COUPONS,)),
    ),
    (
        ("🎁 Подарки", "gifts", (PERM_GIFTS,)),
        ("🧩 Модули", "modules", (PERM_MODULES,)),
    ),
    (
        ("📊 Статистика", "stats", (PERM_STATS,)),
        ("📈 Аналитика", "ads", (PERM_ADS,)),
    ),
    (
        ("🤖 Бот", "management", (PERM_MANAGEMENT, PERM_ADMINS)),
        ("😀 Эмоджи", "emoji", (PERM_EMOJI,)),
    ),
)


async def build_panel_kb(
    admin_role: str,
    permissions: Iterable[str] | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    is_super = admin_role == "superadmin"
    perm_set = frozenset(permissions or ())

    def can(perm: str) -> bool:
        return is_super or perm in perm_set

    for row_spec in _ADMIN_MENU:
        row = [
            InlineKeyboardButton(text=text, callback_data=AdminPanelCallback(action=action).pack())
            for text, action, perms in row_spec
            if any(can(p) for p in perms)
        ]
        if row:
            builder.row(*row)

    module_buttons = await run_hooks("admin_panel", admin_role=admin_role)
    builder = insert_hook_buttons(builder, module_buttons)

    if can(PERM_SETTINGS):
        builder.row(
            InlineKeyboardButton(
                text="⚙️ Настройки",
                callback_data=AdminPanelCallback(action="settings").pack(),
            )
        )

    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    return builder.as_markup()


def build_restart_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Да, перезагрузить",
        callback_data=AdminPanelCallback(action="restart_confirm").pack(),
    )
    builder.row(build_admin_back_btn())
    builder.adjust(1)
    return builder.as_markup()


def build_admin_back_kb(action: str = "admin") -> InlineKeyboardMarkup:
    return build_admin_singleton_kb(BACK, action)


def build_admin_singleton_kb(text: str, action: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(build_admin_btn(text, action))
    return builder.as_markup()


def build_admin_back_btn(action: str = "admin") -> InlineKeyboardButton:
    return build_admin_btn(BACK, action)


def build_admin_btn(text: str, action: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text,
        callback_data=AdminPanelCallback(action=action).pack(),
    )

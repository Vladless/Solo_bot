from typing import Any

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from handlers.buttons import BACK, MAIN_MENU
from hooks.hooks import run_hooks
from logger import logger


class AdminPanelCallback(CallbackData, prefix="admin_panel"):
    action: str
    page: int

    def __init__(self, /, **data: Any) -> None:
        if "page" not in data or data["page"] is None:
            data["page"] = 1
        super().__init__(**data)


async def build_panel_kb(admin_role: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="👤 Поиск пользователя",
        callback_data=AdminPanelCallback(action="search_user").pack(),
    )
    builder.button(
        text="🔑 Поиск по подписке",
        callback_data=AdminPanelCallback(action="search_key").pack(),
    )
    if admin_role == "superadmin":
        builder.button(
            text="🖥️ Управление серверами",
            callback_data=AdminPanelCallback(action="clusters").pack(),
        )
    builder.row(
        InlineKeyboardButton(text="📢 Рассылка", callback_data=AdminPanelCallback(action="sender").pack()),
        InlineKeyboardButton(text="🎟️ Купоны", callback_data=AdminPanelCallback(action="coupons").pack()),
    )
    if admin_role == "superadmin":
        builder.row(
            InlineKeyboardButton(text="💸 Тарифы", callback_data=AdminPanelCallback(action="tariffs").pack()),
            InlineKeyboardButton(text="🎁 Подарки", callback_data=AdminPanelCallback(action="gifts").pack()),
        )
        builder.button(
            text="🤖 Управление ботом",
            callback_data=AdminPanelCallback(action="management").pack(),
        )
        builder.row(
            InlineKeyboardButton(
                text="📊 Статистика",
                callback_data=AdminPanelCallback(action="stats").pack(),
            ),
            InlineKeyboardButton(
                text="📈 Аналитика",
                callback_data=AdminPanelCallback(action="ads").pack(),
            ),
        )
    else:
        builder.button(
            text="🎁 Подарки",
            callback_data=AdminPanelCallback(action="gifts").pack(),
        )

    module_buttons = await run_hooks("admin_panel", admin_role=admin_role)

    for module_btn in module_buttons:
        if isinstance(module_btn, dict) and "after" in module_btn:
            after_callback = module_btn["after"]
            insert_pos = -1

            current_markup = builder.as_markup()

            for i, row in enumerate(current_markup.inline_keyboard):
                for btn in row:
                    if btn.callback_data == after_callback:
                        insert_pos = i + 1
                        break
                if insert_pos > 0:
                    break
            
            if insert_pos > 0:
                new_buttons = []
                for i, row in enumerate(current_markup.inline_keyboard):
                    if i == insert_pos:
                        new_buttons.append([module_btn["button"]])
                    new_buttons.append(row)

                if insert_pos >= len(current_markup.inline_keyboard):
                    new_buttons.append([module_btn["button"]])

                builder = InlineKeyboardBuilder.from_markup(InlineKeyboardMarkup(inline_keyboard=new_buttons))
            else:
                builder.row(module_btn["button"])
        else:
            if isinstance(module_btn, dict):
                builder.row(module_btn["button"])
            else:
                builder.row(module_btn)

    builder.button(
        text=MAIN_MENU,
        callback_data="profile",
    )

    if admin_role == "superadmin":
        builder.adjust(1, 1, 1, 1, 2, 2, 1, 2, 1)
    else:
        builder.adjust(1, 1, 1, 2, 1, 1)
    
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
    return InlineKeyboardButton(text=text, callback_data=AdminPanelCallback(action=action).pack())

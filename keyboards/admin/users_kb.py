from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from keyboards.admin.panel_kb import build_admin_back_btn


class AdminUserEditorCallback(CallbackData, prefix="admin_users"):
    action: str
    tg_id: int
    data: str | None = None


def build_user_edit_kb(tg_id: int, key_records: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for record in key_records:
        email = record["email"]
        builder.button(
            text=f"🔑 {email}",
            callback_data=AdminUserEditorCallback(
                action="users_key_edit",
                tg_id=tg_id,
                data=str(email)
            ).pack()
        )

    builder.button(
        text="💸 Изменить баланс",
        callback_data=AdminUserEditorCallback(
            action="users_balance_change",
            tg_id=tg_id
        ).pack()
    )
    builder.button(
        text="🔄 Восстановить триал",
        callback_data=AdminUserEditorCallback(
            action="users_trial_restore",
            tg_id=tg_id
        ).pack()
    )
    builder.button(
        text="❌ Удалить клиента",
        callback_data=AdminUserEditorCallback(
            action="users_delete_user",
            tg_id=tg_id
        ).pack()
    )
    builder.row(
        build_admin_back_btn()
    )
    builder.adjust(1)
    return builder.as_markup()


def build_user_delete_kb(tg_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(
        text="❌ Да, удалить!",
        callback_data=AdminUserEditorCallback(
            action="users_delete_user_confirm",
            tg_id=tg_id
        ).pack()
    )
    builder.row(
        build_editor_back_btn(tg_id, True)
    )
    builder.adjust(1)
    return builder.as_markup()


def build_key_edit_kb(key_details: dict, email: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="⏳ Изменить время истечения",
        callback_data=AdminUserEditorCallback(
            action="users_change_expiry",
            data=email,
            tg_id=key_details["tg_id"]
        ).pack()
    )
    builder.button(
        text="❌ Удалить ключ",
        callback_data=AdminUserEditorCallback(
            action="users_delete_key",
            data=email,
            tg_id=key_details["tg_id"]
        ).pack()
    )
    builder.row(
        build_editor_back_btn(key_details["tg_id"], True)
    )
    builder.adjust(1)
    return builder.as_markup()


def build_key_delete_kb(tg_id: int, client_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Да, удалить",
            callback_data=AdminUserEditorCallback(
                action="users_delete_key_confirm",
                data=client_id,
                tg_id=tg_id
            ).pack()
        )
    )
    builder.row(
        build_editor_back_btn(tg_id)
    )
    builder.adjust(1)
    return builder.as_markup()


def build_editor_kb(tg_id: int, edit: bool = False) -> InlineKeyboardMarkup:
    return build_editor_singleton_kb("🔙 Назад", tg_id, edit)


def build_editor_singleton_kb(text: str, tg_id: int, edit: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        build_editor_btn(text, tg_id, edit)
    )
    return builder.as_markup()


def build_editor_back_btn(tg_id: int, edit: bool = False) -> InlineKeyboardButton:
    return build_editor_btn("🔙 Назад", tg_id, edit)


def build_editor_btn(text: str, tg_id: int, edit: bool = False) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text,
        callback_data=AdminUserEditorCallback(
            action="users_editor",
            data="" if edit else "edit",
            tg_id=tg_id
        ).pack()
    )

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import RENEWAL_PRICES
from keyboards.admin.panel_kb import build_admin_back_btn


class AdminUserEditorCallback(CallbackData, prefix="admin_users"):
    action: str
    tg_id: int
    data: str | int | None = None
    edit: bool = False


class AdminUserKeyEditorCallback(CallbackData, prefix="admin_users_key"):
    action: str
    tg_id: int
    data: str
    month: int | None = None


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
        text="✉️ Сообщение",
        callback_data=AdminUserEditorCallback(
            action="users_send_message",
            tg_id=tg_id
        ).pack()
    )
    builder.button(
        text="💸 Изменить баланс",
        callback_data=AdminUserEditorCallback(
            action="users_balance_edit",
            tg_id=tg_id
        ).pack()
    )
    builder.button(
        text="♻️ Восстановить триал",
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
        build_editor_btn("🔄 Обновить данные", tg_id, edit=True)
    )
    builder.row(
        build_admin_back_btn()
    )
    builder.adjust(1)
    return builder.as_markup()


def build_users_balance_change_kb(tg_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🔙 Назад",  # todo: fix magic text was set
        callback_data=AdminUserEditorCallback(
            action="users_balance_edit",
            tg_id=tg_id
        ).pack()
    )
    return builder.as_markup()


def build_users_balance_kb(tg_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for month, amount in RENEWAL_PRICES.items():
        builder.button(
            text=f"+ {amount}Р ({month} мес.)",
            callback_data=AdminUserEditorCallback(
                action="users_balance_add",
                tg_id=tg_id,
                data=amount
            ).pack()
        )
        builder.button(
            text=f"- {amount}Р ({month} мес.)",
            callback_data=AdminUserEditorCallback(
                action="users_balance_add",
                tg_id=tg_id,
                data=-amount
            ).pack()
        )
    builder.button(
        text="💵 Добавить",
        callback_data=AdminUserEditorCallback(
            action="users_balance_add",
            tg_id=tg_id
        ).pack()
    )
    builder.button(
        text="💵 Вычесть",
        callback_data=AdminUserEditorCallback(
            action="users_balance_take",
            tg_id=tg_id
        ).pack()
    )
    builder.button(
        text="💵 Установить баланс",
        callback_data=AdminUserEditorCallback(
            action="users_balance_set",
            tg_id=tg_id
        ).pack()
    )
    builder.row(
        build_editor_back_btn(tg_id, True)
    )
    builder.adjust(2, 2, 2, 2, 2, 1)
    return builder.as_markup()


def build_users_key_show_kb(tg_id: int, email: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🔙 Назад",  # todo: fix magic text was set
        callback_data=AdminUserEditorCallback(
            action="users_key_edit",
            tg_id=tg_id,
            data=email,
            edit=True
        ).pack()
    )
    return builder.as_markup()


def build_users_key_expiry_kb(tg_id: int, email: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for month in RENEWAL_PRICES.keys():
        month = int(month)
        builder.button(
            text=f"+ {month} мес.",
            callback_data=AdminUserKeyEditorCallback(
                action="add",
                tg_id=tg_id,
                data=email,
                month=month
            ).pack()
        )
        builder.button(
            text=f"- {month} мес.",
            callback_data=AdminUserKeyEditorCallback(
                action="add",
                tg_id=tg_id,
                data=email,
                month=-month
            ).pack()
        )
    builder.button(
        text="⏳ Добавить дни",
        callback_data=AdminUserKeyEditorCallback(
            action="add",
            tg_id=tg_id,
            data=email
        ).pack()
    )
    builder.button(
        text="⏳ Вычесть дни",
        callback_data=AdminUserKeyEditorCallback(
            action="take",
            tg_id=tg_id,
            data=email
        ).pack()
    )
    builder.button(
        text="⏳ Установить дату истечения",
        callback_data=AdminUserKeyEditorCallback(
            action="set",
            tg_id=tg_id,
            data=email
        ).pack()
    )
    builder.button(
        text="🔙 Назад",  # todo: fix magic text was set
        callback_data=AdminUserEditorCallback(
            action="users_key_edit",
            tg_id=tg_id,
            data=email
        ).pack()
    )
    builder.adjust(2, 2, 2, 2, 2, 1)
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


def build_user_key_kb(tg_id: int, email: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=f"🔙 Назад",
        callback_data=AdminUserEditorCallback(
            action="users_key_edit",
            tg_id=tg_id,
            data=email
        ).pack()
    )
    builder.adjust(1)
    return builder.as_markup()


def build_key_edit_kb(key_details: dict, email: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="⏳ Время истечения",
        callback_data=AdminUserEditorCallback(
            action="users_expiry_edit",
            data=email,
            tg_id=key_details["tg_id"]
        ).pack()
    )
    builder.button(
        text="🔄 Перевыпустить",
        callback_data=AdminUserEditorCallback(
            action="users_update_key",
            data=email,
            tg_id=key_details["tg_id"]
        ).pack()
    )
    builder.button(
        text="❌ Удалить",
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


def build_key_delete_kb(tg_id: int, email: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Да, удалить",
            callback_data=AdminUserEditorCallback(
                action="users_delete_key_confirm",
                data=email,
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
            tg_id=tg_id,
            edit=edit
        ).pack()
    )

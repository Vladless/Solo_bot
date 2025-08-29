from datetime import datetime, timezone

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import HWID_RESET_BUTTON
from database import get_clusters
from database.models import Key, Tariff
from handlers.buttons import BACK
from handlers.utils import format_days
from hooks.hook_buttons import insert_hook_buttons
from hooks.hooks import run_hooks

from ..panel.keyboard import build_admin_back_btn


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
    edit: bool = False


async def build_user_edit_kb(tg_id: int, key_records: list, is_banned: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    current_time = datetime.now(tz=timezone.utc)

    builder.row(
        InlineKeyboardButton(
            text="➕ Создать подписку",
            callback_data=AdminUserEditorCallback(action="users_create_key", tg_id=tg_id).pack(),
        )
    )

    for record in key_records:
        email = record.email
        expiry = datetime.fromtimestamp(record.expiry_time / 1000, tz=timezone.utc)
        days = (expiry - current_time).days
        builder.row(
            InlineKeyboardButton(
                text=f"🔑 {email} ({'<1' if days < 1 else days} дн.)",
                callback_data=AdminUserEditorCallback(action="users_key_edit", tg_id=tg_id, data=str(email)).pack(),
            )
        )

    builder.row(
        InlineKeyboardButton(
            text="✉️ Сообщение",
            callback_data=AdminUserEditorCallback(action="users_send_message", tg_id=tg_id).pack(),
        ),
        InlineKeyboardButton(
            text="💸 Баланс",
            callback_data=AdminUserEditorCallback(action="users_balance_edit", tg_id=tg_id).pack(),
        ),
    )

    builder.row(
        InlineKeyboardButton(
            text="🤝 Выгрузить рефералов",
            callback_data=AdminUserEditorCallback(action="users_export_referrals", tg_id=tg_id).pack(),
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="♻️ Восстановить триал",
            callback_data=AdminUserEditorCallback(action="users_trial_restore", tg_id=tg_id).pack(),
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="❌ Удалить",
            callback_data=AdminUserEditorCallback(action="users_delete_user", tg_id=tg_id).pack(),
        ),
        InlineKeyboardButton(
            text="✅ Разблокировать" if is_banned else "🚫 Заблокировать",
            callback_data=AdminUserEditorCallback(
                action="users_unban" if is_banned else "users_ban", tg_id=tg_id
            ).pack(),
        ),
    )

    hook_buttons = await run_hooks("admin_user_edit", tg_id=tg_id, is_banned=is_banned)
    builder = insert_hook_buttons(builder, hook_buttons)

    builder.row(build_editor_btn("🔄 Обновить данные", tg_id, edit=True))
    builder.row(build_admin_back_btn())

    return builder.as_markup()


def build_users_balance_change_kb(tg_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=BACK,
        callback_data=AdminUserEditorCallback(action="users_balance_edit", tg_id=tg_id).pack(),
    )
    return builder.as_markup()


async def build_users_balance_kb(session: AsyncSession, tg_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for amount in [100, 250, 500, 1000]:
        builder.row(
            InlineKeyboardButton(
                text=f"+ {amount}₽",
                callback_data=AdminUserEditorCallback(action="users_balance_add", tg_id=tg_id, data=amount).pack(),
            ),
            InlineKeyboardButton(
                text=f"- {amount}₽",
                callback_data=AdminUserEditorCallback(action="users_balance_add", tg_id=tg_id, data=-amount).pack(),
            ),
        )

    builder.row(
        InlineKeyboardButton(
            text="💵 Добавить",
            callback_data=AdminUserEditorCallback(action="users_balance_add", tg_id=tg_id).pack(),
        ),
        InlineKeyboardButton(
            text="💵 Вычесть",
            callback_data=AdminUserEditorCallback(action="users_balance_take", tg_id=tg_id).pack(),
        ),
    )

    builder.row(
        InlineKeyboardButton(
            text="💵 Установить баланс",
            callback_data=AdminUserEditorCallback(action="users_balance_set", tg_id=tg_id).pack(),
        )
    )

    builder.row(build_editor_back_btn(tg_id, True))

    return builder.as_markup()


def build_users_key_show_kb(tg_id: int, email: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=BACK,
        callback_data=AdminUserEditorCallback(action="users_key_edit", tg_id=tg_id, data=email, edit=True).pack(),
    )
    return builder.as_markup()


async def build_users_key_expiry_kb(session: AsyncSession, tg_id: int, email: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    result = await session.execute(select(Key.server_id, Key.tariff_id).where(Key.email == email))
    row = result.first()
    _server_id, tariff_id = row if row else (None, None)

    if tariff_id:
        result = await session.execute(select(Tariff.group_code).where(Tariff.id == tariff_id))
        row = result.first()
        if row and row[0]:
            group_code = row[0]
            result = await session.execute(
                select(Tariff).where(Tariff.group_code == group_code, Tariff.is_active.is_(True))
            )
            tariffs = result.scalars().all()
            unique_durations = set()
            for tariff in tariffs:
                days = tariff.duration_days
                if days < 1 or days in unique_durations:
                    continue
                unique_durations.add(days)
                label = format_days(days)
                builder.row(
                    InlineKeyboardButton(
                        text=f"+ {label}",
                        callback_data=AdminUserKeyEditorCallback(
                            action="add", tg_id=tg_id, data=email, month=days
                        ).pack(),
                    ),
                    InlineKeyboardButton(
                        text=f"- {label}",
                        callback_data=AdminUserKeyEditorCallback(
                            action="add", tg_id=tg_id, data=email, month=-days
                        ).pack(),
                    ),
                )

    builder.row(
        InlineKeyboardButton(
            text="⏳ Добавить дни",
            callback_data=AdminUserKeyEditorCallback(action="add", tg_id=tg_id, data=email).pack(),
        ),
        InlineKeyboardButton(
            text="⏳ Вычесть дни",
            callback_data=AdminUserKeyEditorCallback(action="take", tg_id=tg_id, data=email).pack(),
        ),
    )

    builder.row(
        InlineKeyboardButton(
            text="⏳ Установить дату истечения",
            callback_data=AdminUserKeyEditorCallback(action="set", tg_id=tg_id, data=email).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminUserEditorCallback(action="users_key_edit", tg_id=tg_id, data=email).pack(),
        )
    )

    return builder.as_markup()


def build_user_delete_kb(tg_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(
        text="❌ Да, удалить!",
        callback_data=AdminUserEditorCallback(action="users_delete_user_confirm", tg_id=tg_id).pack(),
    )
    builder.row(build_editor_back_btn(tg_id, True))
    builder.adjust(1)
    return builder.as_markup()


def build_user_key_kb(tg_id: int, email: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=BACK,
        callback_data=AdminUserEditorCallback(action="users_key_edit", tg_id=tg_id, data=email).pack(),
    )
    builder.adjust(1)
    return builder.as_markup()


def build_key_edit_kb(key_details: dict, email: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text="⏳ Время истечения",
        callback_data=AdminUserEditorCallback(
            action="users_expiry_edit", data=email, tg_id=key_details["tg_id"]
        ).pack(),
    )
    builder.button(
        text="🔄 Перевыпустить",
        callback_data=AdminUserEditorCallback(action="users_update_key", data=email, tg_id=key_details["tg_id"]).pack(),
    )
    builder.button(
        text="📦 Тариф",
        callback_data=AdminUserEditorCallback(action="users_renew", data=email, tg_id=key_details["tg_id"]).pack(),
    )
    builder.button(
        text="❌ Удалить",
        callback_data=AdminUserEditorCallback(action="users_delete_key", data=email, tg_id=key_details["tg_id"]).pack(),
    )
    builder.button(
        text="📊 Трафик",
        callback_data=AdminUserEditorCallback(action="users_traffic", data=email, tg_id=key_details["tg_id"]).pack(),
    )
    builder.button(
        text="♻️ Сбросить трафик",
        callback_data=AdminUserEditorCallback(
            action="users_reset_traffic", data=email, tg_id=key_details["tg_id"]
        ).pack(),
    )
    if HWID_RESET_BUTTON:
        builder.button(
            text="💻 HWID",
            callback_data=AdminUserEditorCallback(
                action="users_hwid_menu", data=email, tg_id=key_details["tg_id"]
            ).pack(),
        )

    builder.row(build_editor_back_btn(key_details["tg_id"], True))
    builder.adjust(1)
    return builder.as_markup()


def build_hwid_menu_kb(email: str, tg_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="♻️ Сбросить HWID",
        callback_data=AdminUserEditorCallback(action="users_hwid_reset", data=email, tg_id=tg_id).pack(),
    )
    builder.button(
        text="🔙 Назад",
        callback_data=AdminUserEditorCallback(action="users_key_edit", data=email, tg_id=tg_id).pack(),
    )
    builder.adjust(1)
    return builder.as_markup()


def build_key_delete_kb(tg_id: int, email: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Да, удалить",
            callback_data=AdminUserEditorCallback(action="users_delete_key_confirm", data=email, tg_id=tg_id).pack(),
        )
    )
    builder.row(build_editor_back_btn(tg_id))
    builder.adjust(1)
    return builder.as_markup()


def build_editor_kb(tg_id: int, edit: bool = False) -> InlineKeyboardMarkup:
    return build_editor_singleton_kb(BACK, tg_id, edit)


def build_editor_singleton_kb(text: str, tg_id: int, edit: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(build_editor_btn(text, tg_id, edit))
    return builder.as_markup()


def build_editor_back_btn(tg_id: int, edit: bool = False) -> InlineKeyboardButton:
    return build_editor_btn(BACK, tg_id, edit)


def build_editor_btn(text: str, tg_id: int, edit: bool = False) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text,
        callback_data=AdminUserEditorCallback(action="users_editor", tg_id=tg_id, edit=edit).pack(),
    )


async def build_cluster_selection_kb(session, tg_id: int, email: str, action: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    clusters = await get_clusters(session)

    for cluster_id in clusters:
        builder.button(text=cluster_id, callback_data=f"{action}|{tg_id}|{email}|{cluster_id}")

    builder.button(
        text=BACK, callback_data=AdminUserEditorCallback(action="users_key_edit", tg_id=tg_id, data=email).pack()
    )
    builder.adjust(1)
    return builder.as_markup()


def build_user_ban_type_kb(tg_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="⛔ Навсегда",
            callback_data=AdminUserEditorCallback(action="users_ban_forever", tg_id=tg_id).pack(),
        ),
        InlineKeyboardButton(
            text="⏳ По сроку",
            callback_data=AdminUserEditorCallback(action="users_ban_temporary", tg_id=tg_id).pack(),
        ),
    )

    builder.row(
        InlineKeyboardButton(
            text="👻 Теневой бан",
            callback_data=AdminUserEditorCallback(action="users_ban_shadow", tg_id=tg_id).pack(),
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=AdminUserEditorCallback(action="users_editor", tg_id=tg_id, edit=True).pack(),
        )
    )

    return builder.as_markup()

from datetime import datetime, timezone
from typing import Optional, Dict, List, Tuple
from datetime import datetime, timedelta

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from config import HWID_RESET_BUTTON
from database import get_clusters
from database.models import Key, Server, Tariff, Payment
from handlers.buttons import BACK
from handlers.utils import format_days

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


class BalanceActionCallback(CallbackData, prefix="balance_action"):
    """Callback data for balance management actions"""
    action: str  # 'topup', 'deduct', 'set'
    user_id: int
    amount: Optional[float] = None
    description: Optional[str] = None


def build_user_edit_kb(
    tg_id: int, key_records: list, is_banned: bool = False
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    current_time = datetime.now(tz=timezone.utc)

    builder.button(
        text="➕ Создать ключ",
        callback_data=AdminUserEditorCallback(
            action="users_create_key", tg_id=tg_id
        ).pack(),
    )

    for record in key_records:
        email = record.email
        expiry = datetime.fromtimestamp(record.expiry_time / 1000, tz=timezone.utc)
        days = (expiry - current_time).days
        builder.button(
            text=f"🔑 {email} ({'<1' if days < 1 else days} дн.)",
            callback_data=AdminUserEditorCallback(
                action="users_key_edit", tg_id=tg_id, data=str(email)
            ).pack(),
        )

    builder.button(
        text="✉️ Сообщение",
        callback_data=AdminUserEditorCallback(
            action="users_send_message", tg_id=tg_id
        ).pack(),
    )
    builder.button(
        text="💸 Изменить баланс",
        callback_data=AdminUserEditorCallback(
            action="users_balance_edit", tg_id=tg_id
        ).pack(),
    )
    builder.button(
        text="🤝 Выгрузить рефералов",
        callback_data=AdminUserEditorCallback(
            action="users_export_referrals", tg_id=tg_id
        ).pack(),
    )
    builder.button(
        text="♻️ Восстановить триал",
        callback_data=AdminUserEditorCallback(
            action="users_trial_restore", tg_id=tg_id
        ).pack(),
    )
    builder.button(
        text="❌ Удалить клиента",
        callback_data=AdminUserEditorCallback(
            action="users_delete_user", tg_id=tg_id
        ).pack(),
    )
    builder.button(
        text="✅ Разблокировать" if is_banned else "🚫 Заблокировать",
        callback_data=AdminUserEditorCallback(
            action="users_unban" if is_banned else "users_ban", tg_id=tg_id
        ).pack(),
    )
    builder.row(build_editor_btn("🔄 Обновить данные", tg_id, edit=True))
    builder.row(build_admin_back_btn())
    builder.adjust(1)
    return builder.as_markup()


def build_users_balance_change_kb(tg_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=BACK,
        callback_data=AdminUserEditorCallback(
            action="users_balance_edit", tg_id=tg_id
        ).pack(),
    )
    return builder.as_markup()


async def build_users_balance_kb(
    session: AsyncSession, tg_id: int
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for amount in [100, 250, 500, 1000]:
        builder.row(
            InlineKeyboardButton(
                text=f"+ {amount}₽",
                callback_data=AdminUserEditorCallback(
                    action="users_balance_add", tg_id=tg_id, data=amount
                ).pack(),
            ),
            InlineKeyboardButton(
                text=f"- {amount}₽",
                callback_data=AdminUserEditorCallback(
                    action="users_balance_add", tg_id=tg_id, data=-amount
                ).pack(),
            ),
        )

    builder.row(
        InlineKeyboardButton(
            text="💵 Добавить",
            callback_data=AdminUserEditorCallback(
                action="users_balance_add", tg_id=tg_id
            ).pack(),
        ),
        InlineKeyboardButton(
            text="💵 Вычесть",
            callback_data=AdminUserEditorCallback(
                action="users_balance_take", tg_id=tg_id
            ).pack(),
        ),
    )

    builder.row(
        InlineKeyboardButton(
            text="💵 Установить баланс",
            callback_data=AdminUserEditorCallback(
                action="users_balance_set", tg_id=tg_id
            ).pack(),
        )
    )

    builder.row(build_editor_back_btn(tg_id, True))

    return builder.as_markup()


def build_users_key_show_kb(tg_id: int, email: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=BACK,
        callback_data=AdminUserEditorCallback(
            action="users_key_edit", tg_id=tg_id, data=email, edit=True
        ).pack(),
    )
    return builder.as_markup()


async def build_users_key_expiry_kb(
    session: AsyncSession, tg_id: int, email: str
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    result = await session.execute(select(Key.server_id, Key.tariff_id).where(Key.email == email))
    row = result.first()
    server_id, tariff_id = (row if row else (None, None))

    if tariff_id:
        result = await session.execute(select(Tariff.group_code).where(Tariff.id == tariff_id))
        row = result.first()
        if row and row[0]:
            group_code = row[0]
            result = await session.execute(
                select(Tariff)
                .where(Tariff.group_code == group_code, Tariff.is_active.is_(True))
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
            callback_data=AdminUserKeyEditorCallback(
                action="add", tg_id=tg_id, data=email
            ).pack(),
        ),
        InlineKeyboardButton(
            text="⏳ Вычесть дни",
            callback_data=AdminUserKeyEditorCallback(
                action="take", tg_id=tg_id, data=email
            ).pack(),
        ),
    )

    builder.row(
        InlineKeyboardButton(
            text="⏳ Установить дату истечения",
            callback_data=AdminUserKeyEditorCallback(
                action="set", tg_id=tg_id, data=email
            ).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminUserEditorCallback(
                action="users_key_edit", tg_id=tg_id, data=email
            ).pack(),
        )
    )

    return builder.as_markup()


def build_user_delete_kb(tg_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(
        text="❌ Да, удалить!",
        callback_data=AdminUserEditorCallback(
            action="users_delete_user_confirm", tg_id=tg_id
        ).pack(),
    )
    builder.row(build_editor_back_btn(tg_id, True))
    builder.adjust(1)
    return builder.as_markup()


def build_user_key_kb(tg_id: int, email: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=BACK,
        callback_data=AdminUserEditorCallback(
            action="users_key_edit", tg_id=tg_id, data=email
        ).pack(),
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
        callback_data=AdminUserEditorCallback(
            action="users_update_key", data=email, tg_id=key_details["tg_id"]
        ).pack(),
    )
    builder.button(
        text="📦 Тариф",
        callback_data=AdminUserEditorCallback(
            action="users_renew", data=email, tg_id=key_details["tg_id"]
        ).pack(),
    )
    builder.button(
        text="❌ Удалить",
        callback_data=AdminUserEditorCallback(
            action="users_delete_key", data=email, tg_id=key_details["tg_id"]
        ).pack(),
    )
    builder.button(
        text="📊 Трафик",
        callback_data=AdminUserEditorCallback(
            action="users_traffic", data=email, tg_id=key_details["tg_id"]
        ).pack(),
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
        callback_data=AdminUserEditorCallback(
            action="users_hwid_reset", data=email, tg_id=tg_id
        ).pack(),
    )
    builder.button(
        text="🔙 Назад",
        callback_data=AdminUserEditorCallback(
            action="users_key_edit", data=email, tg_id=tg_id
        ).pack(),
    )
    builder.adjust(1)
    return builder.as_markup()


def build_key_delete_kb(tg_id: int, email: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Да, удалить",
            callback_data=AdminUserEditorCallback(
                action="users_delete_key_confirm", data=email, tg_id=tg_id
            ).pack(),
        )
    )
    builder.row(build_editor_back_btn(tg_id))
    builder.adjust(1)
    return builder.as_markup()


def build_editor_kb(tg_id: int, edit: bool = False) -> InlineKeyboardMarkup:
    return build_editor_singleton_kb(BACK, tg_id, edit)


def build_editor_singleton_kb(
    text: str, tg_id: int, edit: bool = False
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(build_editor_btn(text, tg_id, edit))
    return builder.as_markup()


def build_editor_back_btn(tg_id: int, edit: bool = False) -> InlineKeyboardButton:
    return build_editor_btn(BACK, tg_id, edit)


def build_editor_btn(text: str, tg_id: int, edit: bool = False) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text,
        callback_data=AdminUserEditorCallback(
            action="users_editor", tg_id=tg_id, edit=edit
        ).pack(),
    )


def format_balance_history(
    transactions: list[dict],
    current_page: int,
    total_transactions: int,
    user_id: int
) -> str:
    """
    Format balance history for display
    
    Args:
        transactions: List of transaction dictionaries
        current_page: Current page number
        total_transactions: Total number of transactions
        user_id: User ID for the history
        
    Returns:
        Formatted history string
    """
    # Format header
    text = (
        f"📊 <b>История операций</b>\n"
        f"ID пользователя: <code>{user_id}</code>\n"
        f"Страница: {current_page}\n"
        f"Всего операций: {total_transactions}\n\n"
    )
    
    if not transactions:
        return text + "Нет операций для отображения"
    
    # Add transactions
    for t in transactions:
        amount = f"+{t['amount']:.2f}₽" if t['amount'] >= 0 else f"{t['amount']:.2f}₽"
        date = datetime.fromisoformat(t['created_at']).strftime("%d.%m %H:%M")
        op_type = {
            'payment': '💳 Платеж',
            'manual_topup': '➕ Пополнение',
            'manual_deduct': '➖ Списание',
            'referral': '👥 Реферал',
            'referral_bonus': '🎁 Бонус'
        }.get(t['operation_type'], t['operation_type'])
        
        desc = f" - {t['description']}" if t['description'] else ""
        text += f"\n• {date} | {op_type} | {amount}{desc}"
    
    return text


async def build_cluster_selection_kb(
    session, tg_id: int, email: str, action: str
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    clusters = await get_clusters(session)

    for cluster_id in clusters:
        builder.button(
            text=cluster_id, callback_data=f"{action}|{tg_id}|{email}|{cluster_id}"
        )

    builder.button(
        text=BACK, 
        callback_data=AdminUserEditorCallback(
            action="users_key_edit", tg_id=tg_id, data=email
        ).pack()
    )
    builder.adjust(1)
    return builder.as_markup()

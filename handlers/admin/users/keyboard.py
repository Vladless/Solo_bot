from datetime import datetime, timezone

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import BUTTONS_CONFIG
from database import get_clusters, get_key_expiry_presets
from handlers.utils import format_days
from hooks.hook_buttons import insert_hook_buttons
from hooks.hooks import run_hooks
from services.users_utils import build_admin_key_ref
from settings.buttons import BACK
from settings.config import HWID_RESET_BUTTON

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


async def build_user_edit_kb(
    tg_id: int,
    key_records: list,
    is_banned: bool = False,
    admin_role: str | None = None,
    has_email: bool = False,
    has_tg: bool = False,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    current_time = datetime.now(tz=timezone.utc)

    def button(label: str, action: str, data: str = "") -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=label,
            callback_data=AdminUserEditorCallback(action=action, tg_id=tg_id, data=data).pack(),
        )

    for record in key_records:
        expiry = datetime.fromtimestamp(record.expiry_time / 1000, tz=timezone.utc)
        days = (expiry - current_time).days
        builder.row(
            button(
                f"🔑 {record.email} ({'<1' if days < 1 else days} дн.)",
                "users_key_edit",
                build_admin_key_ref(record.client_id, record.email),
            )
        )
    builder.row(button("➕ Новая подписка", "users_create_key"))

    builder.row(button("💸 Баланс", "users_balance_edit"), button("✉️ Написать", "users_send_message"))
    builder.row(button("🕘 Действия", "users_audit", "all|all|0"), button("🧾 Подписки", "users_sub_history"))
    builder.row(button("🤝 Рефералы", "users_export_referrals"), button("🎁 Подарки", "users_gifts"))
    builder.row(button("🌐 Кабинет", "users_site"), button("♻️ Вернуть пробный", "users_trial_restore"))

    if has_email and has_tg:
        builder.row(
            button("📧 Отвязать почту", "users_unlink_email"),
            button("✈️ Отвязать TG", "users_unlink_tg"),
        )

    builder.row(
        button("✅ Разблокировать" if is_banned else "🚫 Заблокировать", "users_unban" if is_banned else "users_ban"),
        button("❌ Удалить", "users_delete_user"),
    )

    hook_buttons = await run_hooks("admin_user_edit", tg_id=tg_id, is_banned=is_banned, admin_role=admin_role)
    builder = insert_hook_buttons(builder, hook_buttons)

    builder.row(build_editor_btn("🔄 Обновить", tg_id, edit=True))
    builder.row(build_admin_back_btn())

    return builder.as_markup()


def build_users_balance_change_kb(tg_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=BACK,
        callback_data=AdminUserEditorCallback(action="users_balance_edit", tg_id=tg_id).pack(),
    )
    return builder.as_markup()


async def build_users_balance_kb(
    session: AsyncSession,
    tg_id: int,
    page: int = 0,
    total_pages: int = 1,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if total_pages > 1:
        nav_buttons = []
        if page > 0:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="◀️",
                    callback_data=AdminUserEditorCallback(
                        action="users_balance_page", tg_id=tg_id, data=page - 1
                    ).pack(),
                )
            )
        nav_buttons.append(
            InlineKeyboardButton(
                text=f"{page + 1}/{total_pages}",
                callback_data="noop",
            )
        )
        if page < total_pages - 1:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="▶️",
                    callback_data=AdminUserEditorCallback(
                        action="users_balance_page", tg_id=tg_id, data=page + 1
                    ).pack(),
                )
            )
        builder.row(*nav_buttons)

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
    builder.row(
        InlineKeyboardButton(
            text="Выгрузить платежи",
            callback_data=AdminUserEditorCallback(action="users_balance_export", tg_id=tg_id).pack(),
        )
    )

    builder.row(build_editor_back_btn(tg_id, True))

    return builder.as_markup()


def build_users_key_show_kb(tg_id: int, key_ref: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=BACK,
        callback_data=AdminUserEditorCallback(action="users_key_edit", tg_id=tg_id, data=key_ref, edit=True).pack(),
    )
    return builder.as_markup()


async def build_users_key_expiry_kb(
    session: AsyncSession,
    tg_id: int,
    email: str,
    key_ref: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    client_id, preset_durations = await get_key_expiry_presets(session, email)
    resolved_key_ref = key_ref or build_admin_key_ref(client_id, email)

    for days in preset_durations:
        label = format_days(days)
        builder.row(
            InlineKeyboardButton(
                text=f"+ {label}",
                callback_data=AdminUserKeyEditorCallback(
                    action="add", tg_id=tg_id, data=resolved_key_ref, month=days
                ).pack(),
            ),
            InlineKeyboardButton(
                text=f"- {label}",
                callback_data=AdminUserKeyEditorCallback(
                    action="add", tg_id=tg_id, data=resolved_key_ref, month=-days
                ).pack(),
            ),
        )

    builder.row(
        InlineKeyboardButton(
            text="⏳ Добавить дни",
            callback_data=AdminUserKeyEditorCallback(action="add", tg_id=tg_id, data=resolved_key_ref).pack(),
        ),
        InlineKeyboardButton(
            text="⏳ Вычесть дни",
            callback_data=AdminUserKeyEditorCallback(action="take", tg_id=tg_id, data=resolved_key_ref).pack(),
        ),
    )

    builder.row(
        InlineKeyboardButton(
            text="⏳ Дата истечения",
            callback_data=AdminUserKeyEditorCallback(action="set", tg_id=tg_id, data=resolved_key_ref).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminUserEditorCallback(action="users_key_edit", tg_id=tg_id, data=resolved_key_ref).pack(),
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


def build_user_key_kb(tg_id: int, key_ref: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=BACK,
        callback_data=AdminUserEditorCallback(action="users_key_edit", tg_id=tg_id, data=key_ref).pack(),
    )
    builder.adjust(1)
    return builder.as_markup()


def build_key_edit_kb(
    key_details: dict,
    email: str,
    is_configurable: bool = False,
    key_ref: str | None = None,
    show_subscription_keys: bool = False,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    resolved_key_ref = key_ref or build_admin_key_ref(key_details.get("client_id"), email)
    tg_id = key_details["tg_id"]

    is_frozen = (
        key_details.get("is_frozen") if isinstance(key_details, dict) else getattr(key_details, "is_frozen", False)
    )

    def button(label: str, action: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=label,
            callback_data=AdminUserEditorCallback(action=action, data=resolved_key_ref, tg_id=tg_id).pack(),
        )

    builder.row(button("⏳ Срок", "users_expiry_edit"), button("📦 Тариф", "users_renew"))

    limits = [button("📊 Трафик", "users_traffic")]
    if is_configurable:
        limits.insert(0, button("⚙️ Конфигурация", "users_edit_config"))
    if bool(BUTTONS_CONFIG.get("HWID_RESET_BUTTON_ENABLE", HWID_RESET_BUTTON)):
        limits.append(button("💻 Устройства", "users_hwid_menu"))
    for i in range(0, len(limits), 2):
        builder.row(*limits[i : i + 2])

    service = [button("🔄 Перевыпустить", "users_reissue_menu"), button("♻️ Сбросить трафик", "users_reset_traffic")]
    if show_subscription_keys:
        service.append(button("🔑 Ключи", "users_keys_list"))
    for i in range(0, len(service), 2):
        builder.row(*service[i : i + 2])

    builder.row(
        button("▶️ Включить" if is_frozen else "⛔ Отключить", "users_unfreeze" if is_frozen else "users_freeze"),
        button("❌ Удалить", "users_delete_key"),
    )
    builder.row(build_editor_back_btn(tg_id, True))
    return builder.as_markup()


def build_reissue_menu_kb(key_ref: str, tg_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="📦 Полный перевыпуск",
        callback_data=AdminUserEditorCallback(action="users_update_key", data=key_ref, tg_id=tg_id).pack(),
    )
    builder.button(
        text="🔗 Сменить ссылку",
        callback_data=AdminUserEditorCallback(action="users_recreate_key", data=key_ref, tg_id=tg_id).pack(),
    )
    builder.button(
        text=BACK,
        callback_data=AdminUserEditorCallback(action="users_key_edit", data=key_ref, tg_id=tg_id).pack(),
    )
    builder.adjust(1)
    return builder.as_markup()


def build_hwid_menu_kb(
    key_ref: str,
    tg_id: int,
    page: int = 0,
    total_pages: int = 0,
    devices_on_page: int = 0,
    devices_per_page: int = 3,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for idx in range(devices_on_page):
        builder.row(
            InlineKeyboardButton(
                text=f"🔌 Отвязать #{page * devices_per_page + idx + 1}",
                callback_data=AdminUserEditorCallback(
                    action="users_hwid_unbind",
                    data=f"{key_ref}|{page}|{idx}",
                    tg_id=tg_id,
                ).pack(),
            )
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="◀️",
                callback_data=AdminUserEditorCallback(
                    action="users_hwid_page", data=f"{key_ref}|{page - 1}", tg_id=tg_id
                ).pack(),
            )
        )
    if total_pages > 0:
        nav.append(
            InlineKeyboardButton(
                text=f"{page + 1}/{total_pages}",
                callback_data=AdminUserEditorCallback(
                    action="users_hwid_page", data=f"{key_ref}|{page}", tg_id=tg_id
                ).pack(),
            )
        )
    if page + 1 < total_pages:
        nav.append(
            InlineKeyboardButton(
                text="▶️",
                callback_data=AdminUserEditorCallback(
                    action="users_hwid_page", data=f"{key_ref}|{page + 1}", tg_id=tg_id
                ).pack(),
            )
        )
    if nav:
        builder.row(*nav)
    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminUserEditorCallback(action="users_key_edit", data=key_ref, tg_id=tg_id).pack(),
        )
    )
    return builder.as_markup()


def build_key_delete_kb(tg_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Да, удалить",
            callback_data=AdminUserEditorCallback(
                action="users_delete_key_confirm",
                data="ok",
                tg_id=tg_id,
            ).pack(),
        )
    )
    builder.row(build_editor_back_btn(tg_id))
    builder.adjust(1)
    return builder.as_markup()


SITE_TABS = [
    ("keys", "🔑 Подписки"),
    ("profile", "👤 Профиль"),
    ("instructions", "📖 Инструкции"),
    ("referrals", "🤝 Рефералы"),
    ("partners", "💼 Партнёры"),
    ("gifts", "🎁 Подарки"),
    ("notifications", "🔔 Уведомления"),
]
SITE_TAB_LABELS = dict(SITE_TABS)


def build_user_site_tabs_kb(tg_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for tab_id, label in SITE_TABS:
        builder.row(
            InlineKeyboardButton(
                text=label,
                callback_data=AdminUserEditorCallback(action="users_site_tab", tg_id=tg_id, data=tab_id).pack(),
            )
        )
    builder.row(build_editor_btn(BACK, tg_id, edit=True))
    return builder.as_markup()


def build_user_site_send_kb(tg_id: int, tab: str) -> InlineKeyboardMarkup:
    label = SITE_TAB_LABELS.get(tab, "")
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=f"📤 Отправить «{label}»",
            callback_data=AdminUserEditorCallback(action="users_site_send", tg_id=tg_id, data=tab).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminUserEditorCallback(action="users_site", tg_id=tg_id).pack(),
        )
    )
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


async def build_cluster_selection_kb(session, tg_id: int, key_ref: str, action: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    clusters = await get_clusters(session)

    for cluster_id in clusters:
        builder.button(text=cluster_id, callback_data=f"{action}|{tg_id}|{key_ref}|{cluster_id}")

    builder.button(
        text=BACK, callback_data=AdminUserEditorCallback(action="users_key_edit", tg_id=tg_id, data=key_ref).pack()
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
            text=BACK,
            callback_data=AdminUserEditorCallback(action="users_editor", tg_id=tg_id, edit=True).pack(),
        )
    )

    return builder.as_markup()


class AdminUserGiftCallback(CallbackData, prefix="admin_gift"):
    action: str
    tg_id: int
    gift_id: str | None = None
    page: int = 0


GIFTS_PER_PAGE = 10


def build_user_gifts_kb(tg_id: int, gifts: list, page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    total_pages = (len(gifts) + GIFTS_PER_PAGE - 1) // GIFTS_PER_PAGE if gifts else 1
    start_idx = page * GIFTS_PER_PAGE
    end_idx = start_idx + GIFTS_PER_PAGE
    page_gifts = gifts[start_idx:end_idx]

    row_buttons = []
    for gift in page_gifts:
        created_str = gift.created_at.strftime("%d.%m.%Y") if gift.created_at else "—"
        row_buttons.append(
            InlineKeyboardButton(
                text=f"Удалить {created_str}",
                callback_data=f"user_gift_del|{tg_id}|{gift.gift_id}|{page}",
            )
        )
        if len(row_buttons) == 1:
            builder.row(*row_buttons)
            row_buttons = []
    if row_buttons:
        builder.row(*row_buttons)

    if total_pages > 1:
        nav_buttons = []
        if page > 0:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="◀️",
                    callback_data=f"user_gift_page|{tg_id}|{page - 1}",
                )
            )
        nav_buttons.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="▶️",
                    callback_data=f"user_gift_page|{tg_id}|{page + 1}",
                )
            )
        builder.row(*nav_buttons)

    builder.row(build_editor_back_btn(tg_id, True))
    return builder.as_markup()


def build_user_audit_kb(
    tg_id: int,
    channel_filter: str = "all",
    category_filter: str = "all",
    page: int = 0,
    has_prev: bool = False,
    has_next: bool = False,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="Все",
            callback_data=AdminUserEditorCallback(
                action="users_audit", tg_id=tg_id, data=f"all|{category_filter}|0"
            ).pack(),
        ),
        InlineKeyboardButton(
            text="API",
            callback_data=AdminUserEditorCallback(
                action="users_audit", tg_id=tg_id, data=f"api|{category_filter}|0"
            ).pack(),
        ),
        InlineKeyboardButton(
            text="Telegram",
            callback_data=AdminUserEditorCallback(
                action="users_audit", tg_id=tg_id, data=f"telegram|{category_filter}|0"
            ).pack(),
        ),
    )

    category_labels = {
        "all": "Все",
        "balance": "Баланс",
        "auth": "Auth",
        "payments": "Платежи",
        "subscriptions": "Подписки",
        "marketing": "Маркетинг",
    }
    category_row = [
        InlineKeyboardButton(
            text=category_labels[category_key],
            callback_data=AdminUserEditorCallback(
                action="users_audit",
                tg_id=tg_id,
                data=f"{channel_filter}|{category_key}|0",
            ).pack(),
        )
        for category_key in ("all", "balance", "auth", "payments", "subscriptions", "marketing")
    ]
    builder.row(*category_row[:3])
    builder.row(*category_row[3:])

    if has_prev or has_next:
        nav_buttons = []
        if has_prev:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="<",
                    callback_data=AdminUserEditorCallback(
                        action="users_audit_page",
                        tg_id=tg_id,
                        data=f"{channel_filter}|{category_filter}|{page - 1}",
                    ).pack(),
                )
            )
        nav_buttons.append(InlineKeyboardButton(text=str(page + 1), callback_data="noop"))
        if has_next:
            nav_buttons.append(
                InlineKeyboardButton(
                    text=">",
                    callback_data=AdminUserEditorCallback(
                        action="users_audit_page",
                        tg_id=tg_id,
                        data=f"{channel_filter}|{category_filter}|{page + 1}",
                    ).pack(),
                )
            )
        builder.row(*nav_buttons)

    builder.row(
        InlineKeyboardButton(
            text="Назад",
            callback_data=AdminUserEditorCallback(action="users_editor", tg_id=tg_id, edit=True).pack(),
        )
    )
    return builder.as_markup()


def build_gift_delete_confirm_kb(tg_id: int, gift_id: str, page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="✅ Да, удалить",
            callback_data=f"user_gift_del_c|{tg_id}|{gift_id}",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=f"user_gift_page|{tg_id}|{page}",
        )
    )

    return builder.as_markup()

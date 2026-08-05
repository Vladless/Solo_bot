from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..panel.keyboard import AdminPanelCallback, build_admin_back_btn


class AdminSenderCallback(CallbackData, prefix="admin_sender"):
    type: str
    data: str | None = None


class AdminSenderChannelCallback(CallbackData, prefix="admin_sender_ch"):
    channel: str


class ScheduledBroadcastCallback(CallbackData, prefix="sb"):
    action: str
    broadcast_id: str = "0"
    page: int = 0


class AdminPollCallback(CallbackData, prefix="admin_poll"):
    action: str
    poll_id: str = "0"
    page: int = 0
    value: str = ""


def build_sender_kb(include_scheduled: bool = True) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="👥 Все пользователи",
            callback_data=AdminSenderCallback(type="all").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="✅ С подпиской",
            callback_data=AdminSenderCallback(type="subscribed").pack(),
        ),
        InlineKeyboardButton(
            text="❌ Без подписки",
            callback_data=AdminSenderCallback(type="unsubscribed").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="📍 Не использовавшие триал",
            callback_data=AdminSenderCallback(type="untrial").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="📭 Без почты в ЛК",
            callback_data=AdminSenderCallback(type="no_email").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🧪 Триал",
            callback_data=AdminSenderCallback(type="trial").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🔥 Горячие лиды",
            callback_data=AdminSenderCallback(type="hotleads").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="📢 Кластер",
            callback_data=AdminSenderCallback(type="cluster-select").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🔗 По UTM-источнику",
            callback_data=AdminSenderCallback(type="source-select").pack(),
        )
    )
    if include_scheduled:
        builder.row(
            InlineKeyboardButton(
                text="🗓 Запланированные",
                callback_data=ScheduledBroadcastCallback(action="list").pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="📊 Опросы",
            callback_data=AdminPollCallback(action="menu").pack(),
        )
    )
    builder.row(build_admin_back_btn())

    return builder.as_markup()


def build_polls_menu_kb(polls: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="➕ Создать опрос",
            callback_data=AdminPollCallback(action="create").pack(),
        )
    )
    for poll in polls:
        status_icon = "🟢" if poll.status == "open" else "🔒"
        question = (poll.question or "").strip().replace("\n", " ")
        title = question[:40] + "…" if len(question) > 40 else question
        builder.row(
            InlineKeyboardButton(
                text=f"{status_icon} {title or 'без вопроса'}",
                callback_data=AdminPollCallback(action="view", poll_id=poll.id).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="◀️ Назад",
            callback_data=AdminPanelCallback(action="sender").pack(),
        )
    )
    return builder.as_markup()


POLL_AUDIENCES = [
    ("all", "👥 Все пользователи"),
    ("subscribed", "✅ С подпиской"),
    ("unsubscribed", "❌ Без подписки"),
    ("untrial", "📍 Не использовавшие триал"),
    ("trial", "🧪 Триал"),
    ("hotleads", "🔥 Горячие лиды"),
    ("no_email", "📭 Без почты в ЛК"),
]


def build_poll_preview_kb(is_anonymous: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=f"👁 Анонимный: {'✅ да' if is_anonymous else '❌ нет'}",
            callback_data=AdminPollCallback(action="anon").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(text="📤 Кому отправить", callback_data=AdminPollCallback(action="audience").pack()),
    )
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data=AdminPollCallback(action="menu").pack()),
    )
    return builder.as_markup()


def build_poll_audience_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for code, label in POLL_AUDIENCES:
        builder.row(InlineKeyboardButton(text=label, callback_data=AdminPollCallback(action="aud", value=code).pack()))
    builder.row(
        InlineKeyboardButton(text="◀️ Назад", callback_data=AdminPollCallback(action="back_preview").pack()),
    )
    return builder.as_markup()


def build_poll_detail_kb(poll_id: str, is_open: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🔄 Обновить", callback_data=AdminPollCallback(action="view", poll_id=poll_id).pack()
        ),
    )
    if is_open:
        builder.row(
            InlineKeyboardButton(
                text="🔒 Закрыть опрос",
                callback_data=AdminPollCallback(action="close", poll_id=poll_id).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="🗑 Удалить опрос",
            callback_data=AdminPollCallback(action="del_ask", poll_id=poll_id).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(text="◀️ К опросам", callback_data=AdminPollCallback(action="menu").pack()),
    )
    return builder.as_markup()


def build_poll_delete_confirm_kb(poll_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Да, удалить",
            callback_data=AdminPollCallback(action="del", poll_id=poll_id).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="◀️ Отмена",
            callback_data=AdminPollCallback(action="view", poll_id=poll_id).pack(),
        )
    )
    return builder.as_markup()


def build_clusters_kb(clusters: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for cluster in clusters:
        name = cluster["cluster_name"]
        builder.button(
            text=f"🌐 {name}",
            callback_data=AdminSenderCallback(type="cluster", data=name).pack(),
        )

    builder.adjust(2)
    builder.row(build_admin_back_btn())

    return builder.as_markup()


def build_sources_kb(sources: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for src in sources:
        code = src["code"]
        name = src.get("name") or code
        regs = src.get("registrations", 0)
        builder.row(
            InlineKeyboardButton(
                text=f"🔗 {name} ({regs})",
                callback_data=AdminSenderCallback(type="source", data=code).pack(),
            )
        )

    builder.row(build_admin_back_btn())

    return builder.as_markup()


_CHANNEL_OPTIONS = [
    ("bot", "📲 Бот"),
    ("site", "🌐 Сайт"),
    ("email", "📧 Почта"),
]

_CHANNEL_NAMES = {"bot": "бот", "site": "сайт", "email": "почта"}


def build_channel_kb(selected: list[str] | None = None) -> InlineKeyboardMarkup:
    chosen = set(selected or ["bot", "site"])
    builder = InlineKeyboardBuilder()
    for code, label in _CHANNEL_OPTIONS:
        mark = "✅" if code in chosen else "☐"
        builder.row(
            InlineKeyboardButton(
                text=f"{mark} {label}",
                callback_data=AdminSenderChannelCallback(channel=code).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="➡️ Продолжить",
            callback_data=AdminSenderChannelCallback(channel="continue").pack(),
        )
    )
    builder.row(build_admin_back_btn())
    return builder.as_markup()


def channel_label(channel: str) -> str:
    if channel in ("both", None, ""):
        channel = "bot,site"
    parts = [p.strip() for p in str(channel).split(",") if p.strip()]
    names = [_CHANNEL_NAMES.get(p, p) for p in parts]
    if not names:
        return "—"
    return " + ".join(names)


def build_broadcast_preview_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📤 Отправить", callback_data="send_broadcast"),
                InlineKeyboardButton(text="🗓 Запланировать", callback_data="schedule_broadcast"),
            ],
            [
                InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_broadcast"),
            ],
        ]
    )


def build_scheduled_broadcasts_list_kb(items: list, page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item in items:
        builder.row(
            InlineKeyboardButton(
                text=f"🗓 {item.id[:8]} | {item.status}",
                callback_data=ScheduledBroadcastCallback(action="view", broadcast_id=item.id, page=page).pack(),
            )
        )
    nav_row = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data=ScheduledBroadcastCallback(action="list", page=page - 1).pack(),
            )
        )
    if len(items) >= 5:
        nav_row.append(
            InlineKeyboardButton(
                text="▶️ Далее",
                callback_data=ScheduledBroadcastCallback(action="list", page=page + 1).pack(),
            )
        )
    if nav_row:
        builder.row(*nav_row)
    builder.row(
        InlineKeyboardButton(
            text="🔄 Обновить",
            callback_data=ScheduledBroadcastCallback(action="list", page=page).pack(),
        )
    )
    builder.row(build_admin_back_btn())
    return builder.as_markup()


def build_scheduled_broadcast_detail_kb(item, page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if item.status in {"scheduled", "failed"}:
        builder.row(
            InlineKeyboardButton(
                text="✏️ Сообщение",
                callback_data=ScheduledBroadcastCallback(action="edit_message", broadcast_id=item.id, page=page).pack(),
            ),
            InlineKeyboardButton(
                text="🕒 Время",
                callback_data=ScheduledBroadcastCallback(action="edit_time", broadcast_id=item.id, page=page).pack(),
            ),
        )
        builder.row(
            InlineKeyboardButton(
                text="👥 Аудитория",
                callback_data=ScheduledBroadcastCallback(
                    action="edit_audience", broadcast_id=item.id, page=page
                ).pack(),
            ),
            InlineKeyboardButton(
                text="⚡ Отправить сейчас",
                callback_data=ScheduledBroadcastCallback(action="send_now", broadcast_id=item.id, page=page).pack(),
            ),
        )
        builder.row(
            InlineKeyboardButton(
                text="❌ Отменить",
                callback_data=ScheduledBroadcastCallback(action="cancel", broadcast_id=item.id, page=page).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="🔄 Обновить",
            callback_data=ScheduledBroadcastCallback(action="view", broadcast_id=item.id, page=page).pack(),
        ),
        InlineKeyboardButton(
            text="📋 К списку",
            callback_data=ScheduledBroadcastCallback(action="list", page=page).pack(),
        ),
    )
    return builder.as_markup()

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..panel.keyboard import AdminPanelCallback, build_admin_back_btn


def build_audit_refresh_kb(source: str = "db") -> InlineKeyboardMarkup:
    """Клавиатура под сообщением аудита: выбор источника данных."""
    builder = InlineKeyboardBuilder()
    redis_text = "• Redis raw" if source == "redis" else "Redis raw"
    db_text = "• БД вчера" if source == "db" else "БД вчера"
    reset_text = "Сбросить Redis" if source == "redis" else "Сбросить БД"
    builder.button(text=redis_text, callback_data=AdminPanelCallback(action="audit_refresh_redis").pack())
    builder.button(text=db_text, callback_data=AdminPanelCallback(action="audit_refresh_db").pack())
    builder.button(text=reset_text, callback_data=AdminPanelCallback(action=f"audit_reset_ask_{source}").pack())
    builder.button(text="Администратор", callback_data=AdminPanelCallback(action="admin").pack())
    builder.adjust(2, 1, 1)
    return builder.as_markup()


def build_audit_source_kb() -> InlineKeyboardMarkup:
    """Клавиатура выбора источника аудита при первом открытии."""
    builder = InlineKeyboardBuilder()
    builder.button(text="Redis raw", callback_data=AdminPanelCallback(action="audit_refresh_redis").pack())
    builder.button(text="БД вчера", callback_data=AdminPanelCallback(action="audit_refresh_db").pack())
    builder.button(text="Администратор", callback_data=AdminPanelCallback(action="admin").pack())
    builder.adjust(2, 1)
    return builder.as_markup()


def build_audit_reset_confirm_kb(source: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Да, сбросить", callback_data=AdminPanelCallback(action=f"audit_reset_do_{source}").pack())
    builder.button(text="Отмена", callback_data=AdminPanelCallback(action=f"audit_refresh_{source}").pack())
    builder.button(text="Администратор", callback_data=AdminPanelCallback(action="admin").pack())
    builder.adjust(1, 1, 1)
    return builder.as_markup()


# Сегменты страницы статистики: ключ, подпись кнопки навигации (с эмодзи) и имя шапки экрана
# (без эмодзи — шапка идёт через menu_title с нижней чертой). Порядок = порядок листания.
STATS_SEGMENTS: tuple[tuple[str, str, str], ...] = (
    ("overview", "📊 Обзор", "Обзор"),
    ("clients", "👤 Клиенты", "Клиенты"),
    ("subs", "🔐 Подписки", "Подписки"),
    ("payments", "💰 Оплаты", "Оплаты"),
    ("tariffs", "📦 Тарифы", "Тарифы"),
    ("leads", "🔥 Лиды", "Лиды"),
    ("modules", "🧩 Модули", "Модули"),
)

# Контекстный экспорт для сегмента (индекс → подпись, действие).
_STATS_SEGMENT_EXPORT: dict[int, tuple[str, str]] = {
    1: ("📥 Клиенты", "stats_export_users_csv"),
    2: ("📥 Подписки", "stats_export_keys_csv"),
    3: ("📥 Оплаты", "stats_export_payments_csv"),
    5: ("📥 Лиды", "stats_export_hot_leads_csv"),
}


def build_stats_kb(index: int) -> InlineKeyboardMarkup:
    total = len(STATS_SEGMENTS)
    index = index % total

    builder = InlineKeyboardBuilder()
    for i, (_key, title, _plain) in enumerate(STATS_SEGMENTS):
        mark = "• " if i == index else ""
        builder.button(text=f"{mark}{title}", callback_data=AdminPanelCallback(action="stats", page=i + 1).pack())
    builder.adjust(1, 2)

    builder.row(
        InlineKeyboardButton(text="🔄 Обновить", callback_data=AdminPanelCallback(action="stats", page=index + 1).pack()),
        InlineKeyboardButton(text="📊 Графики", callback_data=AdminPanelCallback(action="stats_charts").pack()),
    )
    if index in _STATS_SEGMENT_EXPORT:
        export_text, export_action = _STATS_SEGMENT_EXPORT[index]
        builder.row(InlineKeyboardButton(text=export_text, callback_data=AdminPanelCallback(action=export_action).pack()))
    builder.row(build_admin_back_btn())
    return builder.as_markup()


def build_stats_charts_kb(period: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for p in (7, 30, 90):
        mark = "✅ " if p == period else ""
        builder.button(text=f"{mark}{p}д", callback_data=AdminPanelCallback(action=f"stats_chartp_{p}").pack())
    builder.adjust(3)
    builder.row(
        InlineKeyboardButton(text="🗑 Закрыть", callback_data=AdminPanelCallback(action="stats_charts_close").pack())
    )
    return builder.as_markup()

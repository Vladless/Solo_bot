from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from core.settings.tariffs_config import TARIFFS_CONFIG, update_tariffs_config
from filters.admin import IsAdminFilter
from settings.buttons import BACK

from ..panel.keyboard import AdminPanelCallback


router = Router()
router.callback_query.filter(IsAdminFilter())

PACK_MODES = ["", "traffic", "devices", "all"]


def format_pack_mode_label(mode: str | None) -> str:
    """Возвращает человекочитаемое название режима пакетов."""
    if not mode:
        return "выкл"
    if mode == "traffic":
        return "только трафик"
    if mode == "devices":
        return "только устройства"
    if mode == "all":
        return "трафик и устройства"
    return f"неизвестно ({mode})"


def build_tariffs_settings_kb() -> InlineKeyboardMarkup:
    """Клавиатура основного экрана настроек тарификации."""
    allow_downgrade = bool(TARIFFS_CONFIG.get("ALLOW_DOWNGRADE", True))
    pack_mode = TARIFFS_CONFIG.get("KEY_ADDONS_PACK_MODE") or ""
    recalc_enabled = bool(TARIFFS_CONFIG.get("KEY_ADDONS_RECALC_PRICE", False))

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text=f"Понижение: {'вкл' if allow_downgrade else 'выкл'}",
            callback_data=AdminPanelCallback(action="settings_tariffs_toggle_downgrade").pack(),
        )
    )

    builder.row(
        InlineKeyboardButton(
            text=f"Режим пакетов: {format_pack_mode_label(pack_mode)}",
            callback_data=AdminPanelCallback(action="settings_tariffs_packs").pack(),
        )
    )

    builder.row(
        InlineKeyboardButton(
            text=f"Перерасчёт при докупке: {'да' if recalc_enabled else 'нет'}",
            callback_data=AdminPanelCallback(action="settings_tariffs_toggle_addons_recalc").pack(),
        )
    )

    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminPanelCallback(action="settings").pack(),
        )
    )

    return builder.as_markup()


def build_tariffs_settings_text() -> str:
    """Текст основного экрана настроек тарификации."""
    allow_downgrade = bool(TARIFFS_CONFIG.get("ALLOW_DOWNGRADE", True))
    pack_mode = TARIFFS_CONFIG.get("KEY_ADDONS_PACK_MODE") or ""
    recalc_enabled = bool(TARIFFS_CONFIG.get("KEY_ADDONS_RECALC_PRICE", False))

    lines: list[str] = [
        "⚙️ Настройки тарификации",
        "",
        f"• Понижение условий при изменении: {'включено' if allow_downgrade else 'выключено'}",
        f"• Режим доплат: {format_pack_mode_label(pack_mode)}",
        f"• Перерасчёт при докупке: {'да' if recalc_enabled else 'нет'}",
        "",
        "ℹ️ Как работают режимы доплат:",
        "",
        "🔹 <b>Пакеты</b>",
        "<blockquote>Позволяет докупать трафик и устройства к активной подписке неограниченное количество раз.",
        "При продлении подписки все параметры возвращаются к исходно сконфигурированному тарифу.",
        "</blockquote>\n\n🔹 <b>Базовый конфигуратор (выкл. режим пакетов)</b>",
        "<blockquote>Позволяет выбрать параметры тарифа из доступных опций.",
        "Выбранные условия сохраняются и будут использоваться при последующих продлениях.",
        "При включённом понижении клиент сможет понижать условия тарифа.</blockquote>",
    ]
    return "\n".join(lines)


async def refresh_tariffs_settings_screen(callback: CallbackQuery) -> None:
    """Обновляет основной экран настроек тарификации."""
    await callback.message.edit_text(
        build_tariffs_settings_text(),
        reply_markup=build_tariffs_settings_kb(),
    )
    await callback.answer()


def build_tariffs_packs_kb() -> InlineKeyboardMarkup:
    """Клавиатура экрана выбора режима пакетов."""
    current = TARIFFS_CONFIG.get("KEY_ADDONS_PACK_MODE") or ""

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text=f"{'✅ ' if current == '' else ''}Выкл",
            callback_data=AdminPanelCallback(action="settings_tariffs_mode_off").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=f"{'✅ ' if current == 'traffic' else ''}Только трафик",
            callback_data=AdminPanelCallback(action="settings_tariffs_mode_traffic").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=f"{'✅ ' if current == 'devices' else ''}Только устройства",
            callback_data=AdminPanelCallback(action="settings_tariffs_mode_devices").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=f"{'✅ ' if current == 'all' else ''}Трафик и устройства",
            callback_data=AdminPanelCallback(action="settings_tariffs_mode_all").pack(),
        )
    )

    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminPanelCallback(action="settings_tariffs").pack(),
        )
    )

    return builder.as_markup()


def build_tariffs_packs_text() -> str:
    """Текст экрана выбора режима пакетов."""
    current = TARIFFS_CONFIG.get("KEY_ADDONS_PACK_MODE") or ""

    lines: list[str] = [
        "📦 Режим доплат пакетами",
        "",
        "При активной подписке можно продавать не новый тариф, а доплаты к текущим лимитам.",
        "",
        "Режимы:",
        "<blockquote>",
        "• Выкл — доплаты пакетами отключены, работает обычный конфигуратор лимитов (клиент меняет конфигурацию тарифа для продления).",
        "• Только трафик — пользователь докупает ГБ до продления, количество устройств не меняется.",
        "• Только устройства — пользователь докупает устройства до продления, лимит трафика не меняется.",
        "• Трафик и устройства — можно одновременно докупать и трафик, и устройства до продления как единый пакет.",
        f"</blockquote>\nТекущий режим: {format_pack_mode_label(current)}",
        "",
        "Выберите режим, который покажет варианты для доплаты.\n ❗Доплаты не переносятся при продлении.",
    ]
    return "\n".join(lines)


async def refresh_tariffs_packs_screen(callback: CallbackQuery) -> None:
    """Обновляет экран выбора режима пакетов."""
    await callback.message.edit_text(
        build_tariffs_packs_text(),
        reply_markup=build_tariffs_packs_kb(),
    )
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_tariffs"))
async def open_tariffs_settings(callback: CallbackQuery, session: AsyncSession) -> None:
    """Открывает основной экран настроек тарификации."""
    await refresh_tariffs_settings_screen(callback)


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_tariffs_toggle_downgrade"))
async def toggle_tariffs_downgrade(callback: CallbackQuery, session: AsyncSession) -> None:
    """Переключает флаг понижения условий."""
    current = bool(TARIFFS_CONFIG.get("ALLOW_DOWNGRADE", True))

    new_config: dict[str, Any] = dict(TARIFFS_CONFIG)
    new_config["ALLOW_DOWNGRADE"] = not current

    await update_tariffs_config(session, new_config)
    await refresh_tariffs_settings_screen(callback)


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_tariffs_toggle_addons_recalc"))
async def toggle_tariffs_addons_recalc(callback: CallbackQuery, session: AsyncSession) -> None:
    """Переключает перерасчёт при докупке."""
    current = bool(TARIFFS_CONFIG.get("KEY_ADDONS_RECALC_PRICE", False))

    new_config: dict[str, Any] = dict(TARIFFS_CONFIG)
    new_config["KEY_ADDONS_RECALC_PRICE"] = not current

    await update_tariffs_config(session, new_config)
    await refresh_tariffs_settings_screen(callback)


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_tariffs_packs"))
async def open_tariffs_packs(callback: CallbackQuery, session: AsyncSession) -> None:
    """Открывает экран выбора режима пакетов."""
    await refresh_tariffs_packs_screen(callback)


@router.callback_query(
    AdminPanelCallback.filter(
        F.action.in_([
            "settings_tariffs_mode_off",
            "settings_tariffs_mode_traffic",
            "settings_tariffs_mode_devices",
            "settings_tariffs_mode_all",
        ])
    )
)
async def set_tariffs_pack_mode(
    callback: CallbackQuery,
    callback_data: AdminPanelCallback,
    session: AsyncSession,
) -> None:
    """Сохраняет выбранный режим пакетов и обновляет экран."""
    action = callback_data.action

    if action == "settings_tariffs_mode_off":
        new_mode = ""
    elif action == "settings_tariffs_mode_traffic":
        new_mode = "traffic"
    elif action == "settings_tariffs_mode_devices":
        new_mode = "devices"
    elif action == "settings_tariffs_mode_all":
        new_mode = "all"
    else:
        new_mode = TARIFFS_CONFIG.get("KEY_ADDONS_PACK_MODE") or ""

    new_config: dict[str, Any] = dict(TARIFFS_CONFIG)
    new_config["KEY_ADDONS_PACK_MODE"] = new_mode

    await update_tariffs_config(session, new_config)
    await refresh_tariffs_packs_screen(callback)

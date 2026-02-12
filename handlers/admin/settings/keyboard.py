from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.settings.money_config import get_currency_mode
from handlers.buttons import BACK

from ..panel.keyboard import AdminPanelCallback, build_admin_back_btn
from .settings_config import (
    BUTTON_TITLES,
    MODES_TITLES,
    MONEY_FIELDS,
    NOTIFICATION_TIME_FIELDS,
    NOTIFICATION_TITLES,
    PAYMENT_PROVIDER_TITLES,
)


def build_toggle_section_keyboard(
    titles: dict[str, str],
    state: dict[str, bool],
    action: str,
    columns: int,
    back_action: str = "settings",
    extra_rows: list[list[InlineKeyboardButton]] | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for index, key in enumerate(titles.keys(), start=1):
        title = titles[key]
        current_state = bool(state.get(key, False))
        prefix = "✅" if current_state else "❌"
        builder.button(
            text=f"{prefix} {title}",
            callback_data=AdminPanelCallback(
                action=action,
                page=index,
            ).pack(),
        )

    builder.adjust(columns)

    if extra_rows:
        for row in extra_rows:
            builder.row(*row)

    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminPanelCallback(action=back_action).pack(),
        )
    )

    return builder.as_markup()


def build_settings_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text="Кассы",
        callback_data=AdminPanelCallback(action="settings_cashboxes").pack(),
    )
    builder.button(
        text="Деньги",
        callback_data=AdminPanelCallback(action="settings_money").pack(),
    )
    builder.button(
        text="Кнопки",
        callback_data=AdminPanelCallback(action="settings_buttons").pack(),
    )
    builder.button(
        text="Уведомления",
        callback_data=AdminPanelCallback(action="settings_notifications").pack(),
    )
    builder.button(
        text="Режимы",
        callback_data=AdminPanelCallback(action="settings_modes").pack(),
    )
    builder.button(
        text="Тарификация",
        callback_data=AdminPanelCallback(action="settings_tariffs").pack(),
    )

    builder.adjust(2, 2, 2)
    builder.row(build_admin_back_btn())

    return builder.as_markup()


def build_settings_buttons_kb(buttons_state: dict[str, bool]) -> InlineKeyboardMarkup:
    return build_toggle_section_keyboard(
        titles=BUTTON_TITLES,
        state=buttons_state,
        action="settings_button_toggle",
        columns=2,
        back_action="settings",
    )


def build_settings_cashboxes_kb(providers_state: dict[str, bool]) -> InlineKeyboardMarkup:
    order_button = InlineKeyboardButton(
        text="📋 Порядок касс",
        callback_data=AdminPanelCallback(action="settings_providers_order").pack(),
    )
    return build_toggle_section_keyboard(
        titles=PAYMENT_PROVIDER_TITLES,
        state=providers_state,
        action="settings_cashbox_toggle",
        columns=2,
        back_action="settings",
        extra_rows=[[order_button]],
    )


def build_providers_order_kb(sorted_names: list[str]) -> InlineKeyboardMarkup:
    """Клавиатура для управления порядком отображения касс."""
    builder = InlineKeyboardBuilder()

    for idx, name in enumerate(sorted_names):
        title = PAYMENT_PROVIDER_TITLES.get(name, name)
        pos = idx + 1
        builder.row(
            InlineKeyboardButton(
                text="⬆️",
                callback_data=AdminPanelCallback(
                    action="settings_order_up",
                    page=pos,
                ).pack(),
            ),
            InlineKeyboardButton(
                text=f"{pos}. {title}",
                callback_data=AdminPanelCallback(
                    action="settings_providers_order",
                ).pack(),
            ),
            InlineKeyboardButton(
                text="⬇️",
                callback_data=AdminPanelCallback(
                    action="settings_order_down",
                    page=pos,
                ).pack(),
            ),
        )

    builder.row(
        InlineKeyboardButton(
            text="🔄 Сбросить порядок",
            callback_data=AdminPanelCallback(action="settings_order_reset").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminPanelCallback(action="settings_cashboxes").pack(),
        )
    )

    return builder.as_markup()


def build_settings_notifications_kb(notifications_state: dict[str, object]) -> InlineKeyboardMarkup:
    intervals_button = InlineKeyboardButton(
        text="Интервалы",
        callback_data=AdminPanelCallback(action="settings_notifications_intervals").pack(),
    )

    return build_toggle_section_keyboard(
        titles=NOTIFICATION_TITLES,
        state={k: bool(notifications_state.get(k, False)) for k in NOTIFICATION_TITLES},
        action="settings_notification_toggle",
        columns=1,
        back_action="settings",
        extra_rows=[[intervals_button]],
    )


def build_settings_notifications_intervals_kb(notifications_state: dict[str, object]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    keys = list(NOTIFICATION_TIME_FIELDS.keys())
    for index, key in enumerate(keys, start=1):
        title = NOTIFICATION_TIME_FIELDS[key]
        value = notifications_state.get(key)
        value_text = "не задано" if value is None else str(value)

        builder.button(
            text=f"{title}: {value_text}",
            callback_data=AdminPanelCallback(
                action="settings_notification_interval_edit",
                page=index,
            ).pack(),
        )

    builder.adjust(1)

    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminPanelCallback(action="settings_notifications").pack(),
        )
    )

    return builder.as_markup()


def build_settings_modes_kb(modes_state: dict[str, bool]) -> InlineKeyboardMarkup:
    return build_toggle_section_keyboard(
        titles=MODES_TITLES,
        state=modes_state,
        action="settings_modes_toggle",
        columns=2,
        back_action="settings",
    )


def build_settings_money_kb(money_state: dict[str, object]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    field_keys = list(MONEY_FIELDS.keys())
    for index, key in enumerate(field_keys, start=1):
        title = MONEY_FIELDS[key]
        value = money_state.get(key)

        if key == "RUB_TO_USD":
            if value is False or value is None:
                value_text = "по ЦБ РФ"
            else:
                value_text = str(value)
        elif key == "CASHBACK":
            try:
                numeric_value = float(value) if value not in (None, False) else 0.0
            except (TypeError, ValueError):
                numeric_value = 0.0
            if numeric_value <= 0:
                value_text = "выкл"
            else:
                value_text = f"{numeric_value:g} %"
        else:
            value_text = "не задано" if value is None else str(value)

        builder.button(
            text=f"{title}: {value_text}",
            callback_data=AdminPanelCallback(
                action="settings_money_edit",
                page=index,
            ).pack(),
        )

    mode, one_screen = get_currency_mode()
    if mode == "RUB+USD" and one_screen:
        mode_text = "RUB+USD (одним экраном)"
    else:
        mode_text = mode

    builder.button(
        text=f"Режим валют: {mode_text}",
        callback_data=AdminPanelCallback(
            action="settings_money_currency",
            page=0,
        ).pack(),
    )

    builder.adjust(1)

    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminPanelCallback(action="settings").pack(),
        )
    )

    return builder.as_markup()

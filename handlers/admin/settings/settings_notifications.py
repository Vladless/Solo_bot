from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import NOTIFICATIONS_CONFIG, update_notifications_config
from filters.admin import IsAdminFilter

from ..panel.keyboard import AdminPanelCallback
from .keyboard import (
    NOTIFICATION_TIME_FIELDS,
    NOTIFICATION_TITLES,
    build_settings_notifications_intervals_kb,
    build_settings_notifications_kb,
)


router = Router(name="admin_settings_notifications")
router.callback_query.filter(IsAdminFilter())


class NotificationIntervalEditState(StatesGroup):
    waiting_for_value = State()


async def load_notification_settings() -> dict[str, object]:
    return dict(NOTIFICATIONS_CONFIG or {})


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_notifications"))
async def open_settings_notifications_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    notifications_state = await load_notification_settings()
    text = "Настройки уведомлений: включение и выключение."
    await callback.message.edit_text(text=text, reply_markup=build_settings_notifications_kb(notifications_state))
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_notifications_intervals"))
async def open_settings_notifications_intervals_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    notifications_state = await load_notification_settings()
    text = "Настройки интервалов и задержек уведомлений."
    await callback.message.edit_text(
        text=text,
        reply_markup=build_settings_notifications_intervals_kb(notifications_state),
    )
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_notification_toggle"))
async def toggle_notification_setting(
    callback: CallbackQuery,
    callback_data: AdminPanelCallback,
    session: AsyncSession,
) -> None:
    keys = list(NOTIFICATION_TITLES.keys())
    idx = callback_data.page

    if not 1 <= idx <= len(keys):
        await callback.answer("Неизвестная настройка", show_alert=True)
        return

    key = keys[idx - 1]

    config = dict(NOTIFICATIONS_CONFIG or {})
    current = bool(config.get(key, False))
    config[key] = not current

    await update_notifications_config(session, config)
    await session.commit()

    notifications_state = await load_notification_settings()
    await callback.message.edit_reply_markup(
        reply_markup=build_settings_notifications_kb(notifications_state),
    )
    await callback.answer("Настройка обновлена")


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_notification_interval_edit"))
async def edit_notification_interval_setting(
    callback: CallbackQuery,
    callback_data: AdminPanelCallback,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    keys = list(NOTIFICATION_TIME_FIELDS.keys())
    idx = callback_data.page

    if not 1 <= idx <= len(keys):
        await callback.answer("Неизвестная настройка", show_alert=True)
        return

    key = keys[idx - 1]
    title = NOTIFICATION_TIME_FIELDS[key]
    current_value = (NOTIFICATIONS_CONFIG or {}).get(key)

    await state.set_state(NotificationIntervalEditState.waiting_for_value)
    await state.update_data(setting_key=key)

    text = (
        f'Введите новое значение для "{title}" (целое число).\n'
        f"Текущее значение: {current_value if current_value is not None else 'не задано'}"
    )
    await callback.message.edit_text(text=text)
    await callback.answer()


@router.message(NotificationIntervalEditState.waiting_for_value)
async def notification_interval_value_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    text_value = (message.text or "").strip()

    try:
        new_value = int(text_value)
    except ValueError:
        await message.answer("Введите целое число.")
        return

    data = await state.get_data()
    key = data.get("setting_key")
    if not key:
        await state.clear()
        await message.answer("Ошибка состояния. Попробуйте снова.")
        return

    config = dict(NOTIFICATIONS_CONFIG or {})
    config[key] = new_value

    await update_notifications_config(session, config)
    await session.commit()
    await state.clear()

    notifications_state = await load_notification_settings()
    await message.answer(
        "Интервал обновлён.",
        reply_markup=build_settings_notifications_intervals_kb(notifications_state),
    )

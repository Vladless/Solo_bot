from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import MONEY_CONFIG, update_money_config

from ..panel.keyboard import AdminPanelCallback
from .keyboard import MONEY_FIELDS, build_settings_money_kb


router = Router()


class MoneySettingsState(StatesGroup):
    waiting_value = State()


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_money"))
async def open_settings_money(callback: CallbackQuery) -> None:
    text = (
        "Настройки денег и мультивалютности.\n\n"
        "Здесь можно:\n"
        "• выбрать режим валют: RUB, USD или RUB+USD;\n"
        "• задать наценку на конвертацию (FX);\n"
        "• задать фиксированный курс USD/RUB или использовать курс ЦБ РФ;\n"
        "• включить кэшбэк и задать процент от платежа.\n\n"
        "Кэшбэк считается, если указан положительный процент."
    )
    await callback.message.edit_text(
        text=text,
        reply_markup=build_settings_money_kb(MONEY_CONFIG),
    )
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_money_edit"))
async def edit_money_field_start(
    callback: CallbackQuery,
    callback_data: AdminPanelCallback,
    state: FSMContext,
) -> None:
    index = callback_data.page
    keys = list(MONEY_FIELDS.keys())

    if index is None or index < 1 or index > len(keys):
        await callback.answer("Некорректное поле", show_alert=True)
        return

    key = keys[index - 1]
    await state.set_state(MoneySettingsState.waiting_value)
    await state.update_data(money_field_key=key)

    if key == "FX_MARKUP":
        text = "Введите наценку на валютные операции в процентах (например 0, 3.5, 10):"
    elif key == "RUB_TO_USD":
        text = "Введите курс USD/RUB (число, например 100).\nУкажите 0, чтобы использовать курс ЦБ РФ."
    elif key == "CASHBACK":
        text = "Введите размер кэшбэка в процентах (например 0, 5, 10).\n0 или отрицательное значение выключит кэшбэк."
    else:
        text = "Введите новое значение:"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=AdminPanelCallback(action="settings_money").pack(),
                )
            ]
        ]
    )

    await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()


@router.message(MoneySettingsState.waiting_value)
async def edit_money_field_save(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    data = await state.get_data()
    key = data.get("money_field_key")

    if not key:
        await state.clear()
        await message.answer(
            "Ошибка состояния, попробуйте ещё раз.",
            reply_markup=build_settings_money_kb(MONEY_CONFIG),
        )
        return

    raw = (message.text or "").strip().replace(",", ".")

    if key in ("FX_MARKUP", "CASHBACK"):
        try:
            value_float = float(raw)
        except ValueError:
            await message.answer("Некорректное число. Введите, например, 0, 3.5 или 10.")
            return

        if key == "CASHBACK" and value_float <= 0:
            value = False
        else:
            value = value_float
    elif key == "RUB_TO_USD":
        try:
            value_float = float(raw)
        except ValueError:
            await message.answer("Некорректное число. Введите, например, 90 или 100.")
            return

        if value_float <= 0:
            value = False
        else:
            value = value_float
    else:
        value = raw

    money_config = MONEY_CONFIG.copy()
    money_config[key] = value

    await update_money_config(session, money_config)
    await state.clear()

    await message.answer(
        "Настройки денег обновлены.",
        reply_markup=build_settings_money_kb(MONEY_CONFIG),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_money_currency"))
async def open_currency_mode_menu(callback: CallbackQuery) -> None:
    money_config = MONEY_CONFIG
    mode = str(money_config.get("CURRENCY_MODE") or "RUB").upper()

    if mode not in ("RUB", "USD", "RUB+USD"):
        mode = "RUB"

    text = (
        "Выберите режим валют:\n\n"
        "• RUB — все цены только в рублях;\n"
        "• USD — все цены только в долларах;\n"
        "• RUB+USD — мультивалюта, оба варианта."
    )

    rub_checked = mode == "RUB"
    usd_checked = mode == "USD"
    multi_checked = mode == "RUB+USD"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=("✅ RUB" if rub_checked else "RUB"),
                    callback_data=AdminPanelCallback(action="settings_money_currency_set", page=1).pack(),
                ),
                InlineKeyboardButton(
                    text=("✅ USD" if usd_checked else "USD"),
                    callback_data=AdminPanelCallback(action="settings_money_currency_set", page=2).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=("✅ RUB+USD" if multi_checked else "RUB+USD"),
                    callback_data=AdminPanelCallback(action="settings_money_currency_set", page=3).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Назад",
                    callback_data=AdminPanelCallback(action="settings_money").pack(),
                )
            ],
        ]
    )

    await callback.message.edit_text(text=text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_money_currency_set"))
async def set_currency_mode(
    callback: CallbackQuery,
    callback_data: AdminPanelCallback,
    session: AsyncSession,
) -> None:
    mode_index = callback_data.page

    if mode_index not in (1, 2, 3):
        await callback.answer("Некорректный выбор", show_alert=True)
        return

    if mode_index == 1:
        new_mode = "RUB"
    elif mode_index == 2:
        new_mode = "USD"
    else:
        new_mode = "RUB+USD"

    money_config = MONEY_CONFIG.copy()
    money_config["CURRENCY_MODE"] = new_mode

    await update_money_config(session, money_config)

    await open_currency_mode_menu(callback)

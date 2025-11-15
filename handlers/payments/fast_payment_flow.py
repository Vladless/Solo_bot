from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import USE_NEW_PAYMENT_FLOW
from core.bootstrap import PAYMENTS_CONFIG, MONEY_CONFIG
from database.temporary_data import create_temporary_data
from handlers import buttons as btn
from handlers.payments.currency_flow import (
    build_currency_choice_kb,
    shortfall_lead_text,
    currency_label,
)
from handlers.payments.providers import get_providers_with_hooks
from handlers.texts import FAST_PAY_CHOOSE_CURRENCY, FAST_PAY_CHOOSE_PROVIDER
from handlers.utils import edit_or_send_message
from logger import logger


router = Router()


async def get_payment_providers_config() -> dict[str, bool]:
    config = PAYMENTS_CONFIG or {}
    return dict(config)


def get_currency_mode() -> str:
    mode_cfg = MONEY_CONFIG.get("CURRENCY_MODE", "RUB")
    mode = str(mode_cfg or "RUB").upper()
    if mode not in ("RUB", "USD", "RUB+USD"):
        mode = "RUB"
    return mode


async def _run_provider_flow(
    provider: str,
    callback_query: CallbackQuery,
    session: Any,
    state: FSMContext,
    required_amount: int | None,
) -> bool:
    import importlib

    payment_config = await get_payment_providers_config()
    providers_map = await get_providers_with_hooks(payment_config)
    provider_upper = provider.upper()
    cfg = providers_map.get(provider_upper) or {}
    fast_handler_name = cfg.get("fast")
    if not fast_handler_name:
        return False

    module_name_from_config = cfg.get("module")
    if module_name_from_config:
        module_name = f"handlers.payments.{module_name_from_config}.handlers"
    else:
        module_name = f"handlers.payments.{provider_upper.lower()}.handlers"

    try:
        module = importlib.import_module(module_name)
        func = getattr(module, fast_handler_name)
    except Exception as error:
        logger.error(f"[FAST_FLOW] Импорт {provider_upper}.{fast_handler_name} из {module_name} не удался: {error}")
        return False

    try:
        if provider_upper == "STARS":
            try:
                await callback_query.message.delete()
            except Exception as error:
                logger.warning(f"[FAST_FLOW] Не удалось удалить меню перед STARS: {error}")
        await func(callback_query, session)
        return True
    except Exception as error:
        logger.error(f"[FAST_FLOW] Ошибка при вызове {provider_upper}.{fast_handler_name}(): {error}")
        return False


async def try_fast_payment_flow(
    callback_query: CallbackQuery,
    session: Any,
    state: FSMContext,
    *,
    tg_id: int,
    temp_key: str,
    temp_payload: dict,
    required_amount: int | None = None,
) -> bool:
    await create_temporary_data(session, tg_id, temp_key, temp_payload)

    if not USE_NEW_PAYMENT_FLOW:
        return False

    payment_config = await get_payment_providers_config()
    providers_map = await get_providers_with_hooks(payment_config)

    providers = (
        [USE_NEW_PAYMENT_FLOW]
        if isinstance(USE_NEW_PAYMENT_FLOW, str)
        else [str(provider) for provider in (USE_NEW_PAYMENT_FLOW or [])]
    )
    providers = [
        provider
        for provider in providers
        if (providers_map.get(str(provider).upper()) or {}).get("fast")
        and (providers_map.get(str(provider).upper()) or {}).get("enabled", True)
    ]

    mode = get_currency_mode()
    multicurrency_enabled = mode == "RUB+USD"

    if not multicurrency_enabled:
        allowed_currency = "RUB" if mode == "RUB" else "USD"
        providers = [
            provider
            for provider in providers
            if (providers_map.get(provider.upper()) or {}).get("currency") in (allowed_currency, "RUB+USD")
        ]

    if not providers:
        return False

    if len(providers) == 1 and not multicurrency_enabled:
        single_provider = providers[0].upper()
        cfg = providers_map.get(single_provider) or {}
        currency = cfg.get("currency")
        if currency:
            await state.update_data(chosen_currency=currency)
        if await _run_provider_flow(single_provider, callback_query, session, state, required_amount):
            return True
        return False

    if multicurrency_enabled:
        show_stars = bool((providers_map.get("STARS") or {}).get("enabled"))
        show_tribute = bool((providers_map.get("TRIBUTE") or {}).get("enabled"))
        keyboard = build_currency_choice_kb(show_stars=show_stars, show_tribute=show_tribute)
        lead_text = await shortfall_lead_text(
            session,
            tg_id,
            required_amount,
            getattr(callback_query.from_user, "language_code", None),
        )
        text = f"{lead_text}.\n\n{FAST_PAY_CHOOSE_CURRENCY}"
        await state.update_data(
            temp_key=temp_key,
            temp_payload=temp_payload,
            required_amount=required_amount,
            fastflow_providers=providers,
        )
        await edit_or_send_message(
            target_message=callback_query.message,
            text=text,
            reply_markup=keyboard.as_markup(),
        )
        return True

    keyboard = InlineKeyboardBuilder()
    for provider in providers:
        provider_upper = provider.upper()
        button_text = getattr(btn, provider_upper, provider_upper)
        keyboard.row(InlineKeyboardButton(text=button_text, callback_data=f"choose_payment_provider|{provider_upper}"))

    keyboard.row(InlineKeyboardButton(text=btn.MAIN_MENU, callback_data="profile"))

    lead_text = await shortfall_lead_text(
        session,
        tg_id,
        required_amount,
        getattr(callback_query.from_user, "language_code", None),
    )
    await state.update_data(temp_key=temp_key, temp_payload=temp_payload, required_amount=required_amount)
    await edit_or_send_message(
        target_message=callback_query.message,
        text=f"{lead_text}.\n\n{FAST_PAY_CHOOSE_PROVIDER}",
        reply_markup=keyboard.as_markup(),
    )
    return True


@router.callback_query(F.data.startswith("choose_payment_currency|"))
async def choose_payment_currency(callback_query: CallbackQuery, state: FSMContext, session: Any):
    payment_config = await get_payment_providers_config()
    providers_map = await get_providers_with_hooks(payment_config)

    currency = callback_query.data.split("|")[1]
    data = await state.get_data()

    providers = data.get("fastflow_providers") or (
        [USE_NEW_PAYMENT_FLOW]
        if isinstance(USE_NEW_PAYMENT_FLOW, str)
        else [str(provider) for provider in (USE_NEW_PAYMENT_FLOW or [])]
    )
    filtered = [
        provider_upper
        for provider_upper in (provider.upper() for provider in providers)
        if (providers_map.get(provider_upper) or {}).get("currency") == currency
        and (providers_map.get(provider_upper) or {}).get("fast")
        and (providers_map.get(provider_upper) or {}).get("enabled", True)
    ]
    await state.update_data(chosen_currency=currency)

    if not filtered:
        keyboard = InlineKeyboardBuilder().row(InlineKeyboardButton(text="← Назад", callback_data="profile"))
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Для выбранной валюты нет доступных касс. Выберите другую валюту или вернитесь в меню.",
            reply_markup=keyboard.as_markup(),
        )
        return

    if len(filtered) == 1:
        only_provider = filtered[0]
        if await _run_provider_flow(only_provider, callback_query, session, state, data.get("required_amount")):
            return
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Этот способ временно недоступен.",
            reply_markup=InlineKeyboardBuilder()
            .row(InlineKeyboardButton(text=btn.MAIN_MENU, callback_data="profile"))
            .as_markup(),
        )
        return

    keyboard = InlineKeyboardBuilder()
    for provider_upper in filtered:
        button_text = getattr(btn, provider_upper, provider_upper)
        keyboard.row(
            InlineKeyboardButton(text=button_text, callback_data=f"choose_payment_provider|{provider_upper}")
        )

    keyboard.row(InlineKeyboardButton(text=btn.MAIN_MENU, callback_data="profile"))

    lead_text = await shortfall_lead_text(
        session,
        callback_query.from_user.id,
        data.get("required_amount"),
        getattr(callback_query.from_user, "language_code", None),
        force_currency=currency,
    )
    text = f"{lead_text}.\n\nВалюта: {currency_label(currency)}\n{FAST_PAY_CHOOSE_PROVIDER}"
    await edit_or_send_message(target_message=callback_query.message, text=text, reply_markup=keyboard.as_markup())


@router.callback_query(F.data.startswith("choose_payment_provider|"))
async def choose_payment_provider(callback_query: CallbackQuery, state: FSMContext, session: Any):
    payment_config = await get_payment_providers_config()
    providers_map = await get_providers_with_hooks(payment_config)

    provider = callback_query.data.split("|")[1].upper()
    cfg = providers_map.get(provider) or {}
    if not cfg.get("fast") or not cfg.get("enabled", True):
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Этот способ временно недоступен.",
            reply_markup=InlineKeyboardBuilder()
            .row(InlineKeyboardButton(text=btn.MAIN_MENU, callback_data="profile"))
            .as_markup(),
        )
        return

    currency = cfg.get("currency")
    if currency:
        await state.update_data(chosen_currency=currency)

    data = await state.get_data()
    await _run_provider_flow(provider, callback_query, session, state, data.get("required_amount"))

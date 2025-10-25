from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import USE_NEW_PAYMENT_FLOW, MULTICURRENCY_ENABLE, PROVIDERS_ENABLED
from handlers.texts import FAST_PAY_CHOOSE_CURRENCY, FAST_PAY_CHOOSE_PROVIDER

from database.temporary_data import create_temporary_data
from handlers import buttons as btn
from handlers.utils import edit_or_send_message
from logger import logger

from handlers.payments.currency_flow import (
    build_currency_choice_kb,
    shortfall_lead_text,
    currency_label,
)
from handlers.payments.providers import get_providers_with_hooks

router = Router()


async def _run_provider_flow(
    provider: str,
    callback_query: CallbackQuery,
    session: Any,
    state: FSMContext,
    required_amount: int | None,
) -> bool:
    import importlib

    PROVIDERS = await get_providers_with_hooks(PROVIDERS_ENABLED)
    up = provider.upper()
    cfg = (PROVIDERS.get(up) or {})
    fast_name = cfg.get("fast")
    if not fast_name:
        return False

    module_name_from_config = cfg.get("module")
    if module_name_from_config:
        module_name = f"handlers.payments.{module_name_from_config}.handlers"
    else:
        module_name = f"handlers.payments.{up.lower()}.handlers"

    try:
        module = importlib.import_module(module_name)
        func = getattr(module, fast_name)
    except Exception as e:
        logger.error(f"[FAST_FLOW] Импорт {up}.{fast_name} из {module_name} не удался: {e}")
        return False

    try:
        if up == "STARS":
            try:
                await callback_query.message.delete()
            except Exception as e:
                logger.warning(f"[FAST_FLOW] Не удалось удалить меню перед STARS: {e}")
        await func(callback_query, session)
        return True
    except Exception as e:
        logger.error(f"[FAST_FLOW] Ошибка при вызове {up}.{fast_name}(): {e}")
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

    PROVIDERS = await get_providers_with_hooks(PROVIDERS_ENABLED)

    providers = (
        [USE_NEW_PAYMENT_FLOW] if isinstance(USE_NEW_PAYMENT_FLOW, str)
        else [str(p) for p in (USE_NEW_PAYMENT_FLOW or [])]
    )
    providers = [
        p for p in providers
        if (PROVIDERS.get(str(p).upper()) or {}).get("fast")
        and (PROVIDERS.get(str(p).upper()) or {}).get("enabled", True)
    ]
    if not providers:
        return False

    if len(providers) == 1:
        up = providers[0].upper()
        cfg = PROVIDERS.get(up) or {}
        currency = cfg.get("currency")
        if currency:
            await state.update_data(chosen_currency=currency)
        if await _run_provider_flow(up, callback_query, session, state, required_amount):
            return True
        return False

    if MULTICURRENCY_ENABLE:
        show_stars = bool((PROVIDERS.get("STARS") or {}).get("enabled"))
        show_tribute = bool((PROVIDERS.get("TRIBUTE") or {}).get("enabled"))
        kb = build_currency_choice_kb(show_stars=show_stars, show_tribute=show_tribute)
        lead = await shortfall_lead_text(
            session, tg_id, required_amount, getattr(callback_query.from_user, "language_code", None)
        )
        text = f"{lead}.\n\n{FAST_PAY_CHOOSE_CURRENCY}"
        await state.update_data(
            temp_key=temp_key,
            temp_payload=temp_payload,
            required_amount=required_amount,
            fastflow_providers=providers,
        )
        await edit_or_send_message(target_message=callback_query.message, text=text, reply_markup=kb.as_markup())
        return True

    kb = InlineKeyboardBuilder()
    for p in providers:
        up = p.upper()
        btn_text = getattr(btn, up, up)
        kb.row(InlineKeyboardButton(text=btn_text, callback_data=f"choose_payment_provider|{up}"))

    kb.row(InlineKeyboardButton(text=btn.MAIN_MENU, callback_data="profile"))

    lead = await shortfall_lead_text(
        session, tg_id, required_amount, getattr(callback_query.from_user, "language_code", None)
    )
    await state.update_data(temp_key=temp_key, temp_payload=temp_payload, required_amount=required_amount)
    await edit_or_send_message(
        target_message=callback_query.message,
        text=f"{lead}.\n\n{FAST_PAY_CHOOSE_PROVIDER}",
        reply_markup=kb.as_markup(),
    )
    return True


@router.callback_query(F.data.startswith("choose_payment_currency|"))
async def choose_payment_currency(callback_query: CallbackQuery, state: FSMContext, session: Any):
    PROVIDERS = await get_providers_with_hooks(PROVIDERS_ENABLED)
    currency = callback_query.data.split("|")[1]
    data = await state.get_data()

    providers = data.get("fastflow_providers") or (
        [USE_NEW_PAYMENT_FLOW] if isinstance(USE_NEW_PAYMENT_FLOW, str)
        else [str(p) for p in (USE_NEW_PAYMENT_FLOW or [])]
    )
    filtered = [
        p.upper() for p in providers
        if (PROVIDERS.get(str(p).upper()) or {}).get("currency") == currency
        and (PROVIDERS.get(str(p).upper()) or {}).get("fast")
        and (PROVIDERS.get(str(p).upper()) or {}).get("enabled", True)
    ]
    await state.update_data(chosen_currency=currency)

    if not filtered:
        kb = InlineKeyboardBuilder().row(InlineKeyboardButton(text="← Назад", callback_data="profile"))
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Для выбранной валюты нет доступных касс. Выберите другую валюту или вернитесь в меню.",
            reply_markup=kb.as_markup(),
        )
        return

    if len(filtered) == 1:
        only = filtered[0]
        if await _run_provider_flow(only, callback_query, session, state, data.get("required_amount")):
            return
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Этот способ временно недоступен.",
            reply_markup=InlineKeyboardBuilder().row(
                InlineKeyboardButton(text=btn.MAIN_MENU, callback_data="profile")
            ).as_markup(),
        )
        return

    kb = InlineKeyboardBuilder()
    for p in filtered:
        btn_text = getattr(btn, p, p)
        kb.row(InlineKeyboardButton(text=btn_text, callback_data=f"choose_payment_provider|{p}"))

    kb.row(InlineKeyboardButton(text=btn.MAIN_MENU, callback_data="profile"))

    lead = await shortfall_lead_text(
        session,
        callback_query.from_user.id,
        data.get("required_amount"),
        getattr(callback_query.from_user, "language_code", None),
        force_currency=currency,
    )
    text = f"{lead}.\n\nВалюта: {currency_label(currency)}\n{FAST_PAY_CHOOSE_PROVIDER}"
    await edit_or_send_message(target_message=callback_query.message, text=text, reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("choose_payment_provider|"))
async def choose_payment_provider(callback_query: CallbackQuery, state: FSMContext, session: Any):
    PROVIDERS = await get_providers_with_hooks(PROVIDERS_ENABLED)
    provider = callback_query.data.split("|")[1].upper()
    cfg = PROVIDERS.get(provider) or {}
    if not cfg.get("fast") or not cfg.get("enabled", True):
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Этот способ временно недоступен.",
            reply_markup=InlineKeyboardBuilder().row(
                InlineKeyboardButton(text=btn.MAIN_MENU, callback_data="profile")
            ).as_markup(),
        )
        return

    currency = cfg.get("currency")
    if currency:
        await state.update_data(chosen_currency=currency)

    data = await state.get_data()
    await _run_provider_flow(provider, callback_query, session, state, data.get("required_amount"))

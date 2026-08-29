from math import ceil
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from core.bootstrap import PAYMENTS_CONFIG
from core.settings.buttons_config import BUTTONS_CONFIG
from core.settings.money_config import get_currency_mode
from database import (
    check_coupon_usage,
    get_balance,
    get_coupon_by_code,
    has_any_coupon_usage,
)
from database.coupons import apply_percent_coupon
from database.temporary_data import create_temporary_data, get_temporary_data
from handlers.payments.currency_flow import (
    build_currency_choice_kb,
    currency_label,
    shortfall_lead_text,
)
from handlers.utils import edit_or_send_message
from logger import logger
from services.payments.providers import get_providers_with_hooks, sort_provider_names
from settings import buttons as btn
from settings.config import TRIBUTE_LINK, USE_NEW_PAYMENT_FLOW
from settings.texts import (
    FASTFLOW_COUPON_APPLIED_TEMPLATE,
    FAST_PAY_CHOOSE_CURRENCY,
    FAST_PAY_CHOOSE_PROVIDER,
    RESUME_CHECKOUT_EXPIRED,
)


router = Router()


class FastFlowCouponState(StatesGroup):
    waiting_for_coupon_code = State()


async def get_payment_providers_config() -> dict[str, bool]:
    config = PAYMENTS_CONFIG or {}
    return dict(config)


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

    configured = [str(p) for p in (USE_NEW_PAYMENT_FLOW or [])]
    configured_upper = [p.upper() for p in configured]
    configured_set = set(configured_upper)

    providers: list[str] = []
    for p_up in configured_upper:
        cfg = providers_map.get(p_up) or {}
        if cfg.get("fast") and cfg.get("enabled", True):
            providers.append(p_up)
    providers = sort_provider_names(providers, providers_map)

    mode, one_screen = get_currency_mode()
    multicurrency_mode = mode == "RUB+USD"

    if not multicurrency_mode:
        allowed_currency = "RUB" if mode == "RUB" else "USD"
        filtered: list[str] = []
        for p_up in providers:
            cfg = providers_map.get(p_up) or {}
            curr = str(cfg.get("currency") or "").upper()
            if curr in (allowed_currency, "RUB+USD"):
                filtered.append(p_up)
        providers = filtered

    if not providers and "TRIBUTE" not in configured_set:
        return False

    tribute_cfg = providers_map.get("TRIBUTE") or {}
    tribute_link = (TRIBUTE_LINK or "").strip()
    tribute_enabled = "TRIBUTE" in configured_set and tribute_cfg.get("enabled", True) and bool(tribute_link)

    stars_cfg = providers_map.get("STARS") or {}
    stars_enabled_for_fast = "STARS" in configured_set and stars_cfg.get("fast") and stars_cfg.get("enabled", True)

    if multicurrency_mode and not one_screen:
        show_stars = stars_enabled_for_fast
        show_tribute = tribute_enabled
        if not providers and not show_tribute:
            return False

        keyboard_original = build_currency_choice_kb(show_stars=show_stars, show_tribute=show_tribute)

        rows = keyboard_original.export()
        profile_row = None
        kept_rows: list[list[InlineKeyboardButton]] = []

        for row in rows:
            if any(getattr(button, "callback_data", None) == "profile" for button in row):
                if profile_row is None:
                    profile_row = row
                continue
            kept_rows.append(row)

        keyboard = InlineKeyboardBuilder()
        for row in kept_rows:
            keyboard.row(*row)

        if BUTTONS_CONFIG.get("COUPON_BUTTON_ENABLE", True):
            keyboard.row(InlineKeyboardButton(text=btn.COUPON, callback_data="fastflow_coupon"))

        if profile_row:
            keyboard.row(*profile_row)
        else:
            keyboard.row(InlineKeyboardButton(text=btn.MAIN_MENU, callback_data="profile"))

        lead_text = await shortfall_lead_text(
            session,
            tg_id,
            required_amount,
            getattr(callback_query.from_user, "language_code", None),
        )
        text = f"{lead_text}\n\n{FAST_PAY_CHOOSE_CURRENCY}"
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

    total_options = len(providers) + (1 if tribute_enabled else 0)
    if len(providers) == 1 and total_options == 1:
        single_provider = providers[0]
        cfg = providers_map.get(single_provider) or {}
        currency = cfg.get("currency")
        if currency:
            await state.update_data(chosen_currency=currency)
        await state.update_data(temp_key=temp_key, temp_payload=temp_payload, required_amount=required_amount)
        if await _run_provider_flow(single_provider, callback_query, session, state, required_amount):
            return True
        return False

    keyboard = InlineKeyboardBuilder()
    for provider_upper in providers:
        button_text = getattr(btn, provider_upper, provider_upper)
        if one_screen:
            cfg = providers_map.get(provider_upper) or {}
            curr = cfg.get("currency")
            if curr and curr != "RUB+USD":
                button_text = f"{button_text} ({currency_label(curr)})"
        keyboard.row(
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"choose_payment_provider|{provider_upper}",
            )
        )

    if tribute_enabled:
        keyboard.row(
            InlineKeyboardButton(
                text=getattr(btn, "TRIBUTE", "TRIBUTE"),
                url=tribute_link,
            )
        )

    if BUTTONS_CONFIG.get("COUPON_BUTTON_ENABLE", True):
        keyboard.row(InlineKeyboardButton(text=btn.COUPON, callback_data="fastflow_coupon"))
    keyboard.row(InlineKeyboardButton(text=btn.MAIN_MENU, callback_data="profile"))

    lead_text = await shortfall_lead_text(
        session,
        tg_id,
        required_amount,
        getattr(callback_query.from_user, "language_code", None),
    )
    await state.update_data(
        temp_key=temp_key,
        temp_payload=temp_payload,
        required_amount=required_amount,
    )
    await edit_or_send_message(
        target_message=callback_query.message,
        text=f"{lead_text}\n\n{FAST_PAY_CHOOSE_PROVIDER}",
        reply_markup=keyboard.as_markup(),
    )
    return True


@router.callback_query(F.data == "resume_checkout")
async def resume_checkout(callback_query: CallbackQuery, state: FSMContext, session: Any):
    tg_id = callback_query.from_user.id

    expired_markup = (
        InlineKeyboardBuilder()
        .row(InlineKeyboardButton(text=btn.MAIN_MENU, callback_data="profile"))
        .as_markup()
    )

    temp = await get_temporary_data(session, tg_id)
    if not temp or not isinstance(temp.get("data"), dict):
        await edit_or_send_message(
            target_message=callback_query.message,
            text=RESUME_CHECKOUT_EXPIRED,
            reply_markup=expired_markup,
        )
        await callback_query.answer()
        return

    temp_key = str(temp["state"])
    payload = dict(temp["data"])

    required_amount = payload.get("required_amount")
    if required_amount is None:
        price = payload.get("selected_price_rub") or payload.get("cost") or payload.get("original_price")
        if price is not None:
            balance = await get_balance(session, tg_id)
            required_amount = max(0, ceil(float(price) - float(balance)))

    handled = False
    if required_amount is not None:
        handled = await try_fast_payment_flow(
            callback_query,
            session,
            state,
            tg_id=tg_id,
            temp_key=temp_key,
            temp_payload=payload,
            required_amount=int(required_amount),
        )

    if not handled:
        await edit_or_send_message(
            target_message=callback_query.message,
            text=RESUME_CHECKOUT_EXPIRED,
            reply_markup=expired_markup,
        )

    await callback_query.answer()


@router.callback_query(F.data == "fastflow_back")
async def fastflow_back(callback_query: CallbackQuery, state: FSMContext, session: Any):
    """Возврат из экрана выбора суммы в потоке /buy к выбору способа оплаты (без перехода на экран баланса)."""
    amount_not_found_text = "Сумма не найдена"
    data = await state.get_data()
    temp_key = data.get("temp_key")
    temp_payload = data.get("temp_payload")
    required_amount = data.get("required_amount")

    if not temp_key or not isinstance(temp_payload, dict) or required_amount is None:
        await edit_or_send_message(
            target_message=callback_query.message,
            text=amount_not_found_text,
            reply_markup=InlineKeyboardBuilder()
            .row(InlineKeyboardButton(text=btn.MAIN_MENU, callback_data="profile"))
            .as_markup(),
        )
        await callback_query.answer()
        return

    await try_fast_payment_flow(
        callback_query,
        session,
        state,
        tg_id=callback_query.from_user.id,
        temp_key=str(temp_key),
        temp_payload=dict(temp_payload),
        required_amount=int(required_amount),
    )
    await callback_query.answer()


@router.callback_query(F.data == "fastflow_coupon_back")
async def fastflow_coupon_back(callback_query: CallbackQuery, state: FSMContext, session: Any):
    amount_not_found_text = "Сумма не найдена"

    data = await state.get_data()
    temp_key = data.get("temp_key")
    temp_payload = data.get("temp_payload")
    required_amount = data.get("required_amount")

    await state.set_state(None)

    if not temp_key or not isinstance(temp_payload, dict) or required_amount is None:
        await edit_or_send_message(
            target_message=callback_query.message,
            text=amount_not_found_text,
            reply_markup=InlineKeyboardBuilder()
            .row(InlineKeyboardButton(text=btn.MAIN_MENU, callback_data="profile"))
            .as_markup(),
        )
        await callback_query.answer()
        return

    await try_fast_payment_flow(
        callback_query,
        session,
        state,
        tg_id=callback_query.from_user.id,
        temp_key=str(temp_key),
        temp_payload=dict(temp_payload),
        required_amount=int(required_amount),
    )
    await callback_query.answer()


@router.callback_query(F.data == "fastflow_coupon", flags={"popup": True})
async def fastflow_coupon(callback_query: CallbackQuery, state: FSMContext):
    input_text = "Введите купон:"
    amount_not_found_text = "Сумма не найдена"

    data = await state.get_data()
    if data.get("required_amount") is None:
        await callback_query.answer(amount_not_found_text, show_alert=True)
        return

    await state.set_state(FastFlowCouponState.waiting_for_coupon_code)
    await edit_or_send_message(
        target_message=callback_query.message,
        text=input_text,
        reply_markup=InlineKeyboardBuilder()
        .row(InlineKeyboardButton(text=btn.BACK, callback_data="fastflow_coupon_back"))
        .as_markup(),
    )


@router.callback_query(F.data == "buy_confirm_balance")
async def buy_confirm_balance(callback_query: CallbackQuery, state: FSMContext, session: Any):
    """Оформление с баланса после экрана с предложением купона."""
    data = await state.get_data()
    temp_key = data.get("temp_key")
    payload = data.get("temp_payload")
    if not temp_key or not isinstance(payload, dict):
        await callback_query.answer("Данные покупки не найдены", show_alert=True)
        return
    await callback_query.answer()
    await state.set_state(None)
    await _finish_from_balance(callback_query.message, session, str(temp_key), payload)


async def _finish_from_balance(message, session, temp_key: str, payload: dict) -> bool:
    """Доплачивать нечего — закрываем покупку с баланса, а не показываем кассы."""
    from handlers.payments.utils import _handle_temp_state

    try:
        done = await _handle_temp_state(session, message.from_user.id, temp_key, payload, 0)
    except Exception as e:
        logger.error("[FastFlow] покупка с баланса не завершилась: {}", e)
        return False
    if not done:
        await message.answer("❌ Не удалось завершить покупку. Попробуйте ещё раз.")
    return done


@router.message(FastFlowCouponState.waiting_for_coupon_code)
async def fastflow_apply_coupon(message: Message, state: FSMContext, session: Any):
    input_text = "Введите купон:"
    amount_not_found_text = "Сумма не найдена"
    not_found_text = "Купон не найден"
    exhausted_text = "Купон исчерпан"
    already_used_text = "Вы уже использовали этот купон"
    new_users_only_text = "Купон доступен только новым пользователям"
    not_applicable_text = "Купон не применим к текущей сумме"
    days_coupon_text = "Это купон на дни подписки — активируйте его в меню «Купон», он продлит активный ключ."
    no_methods_text = "Нет доступных способов оплаты"

    back_markup = (
        InlineKeyboardBuilder()
        .row(InlineKeyboardButton(text=btn.BACK, callback_data="fastflow_coupon_back"))
        .as_markup()
    )

    code = (message.text or "").strip()
    if not code:
        await message.answer(input_text, reply_markup=back_markup)
        return

    data = await state.get_data()
    required_amount = data.get("required_amount")
    if required_amount is None:
        await state.set_state(None)
        await message.answer(amount_not_found_text, reply_markup=back_markup)
        return

    temp_key = data.get("temp_key")
    temp_payload = data.get("temp_payload")
    if not temp_key or not isinstance(temp_payload, dict):
        await state.set_state(None)
        await message.answer(amount_not_found_text, reply_markup=back_markup)
        return

    coupon = await get_coupon_by_code(session, code)
    if not coupon:
        await message.answer(not_found_text, reply_markup=back_markup)
        return

    if bool(getattr(coupon, "is_used", False)):
        await message.answer(exhausted_text, reply_markup=back_markup)
        return

    usage_count = getattr(coupon, "usage_count", None)
    usage_limit = getattr(coupon, "usage_limit", None)
    if usage_count is not None and usage_limit is not None and int(usage_count) >= int(usage_limit):
        await message.answer(exhausted_text, reply_markup=back_markup)
        return

    if await check_coupon_usage(session, coupon.id, message.from_user.id):
        await message.answer(already_used_text, reply_markup=back_markup)
        return

    if bool(getattr(coupon, "new_users_only", False)):
        if await has_any_coupon_usage(session, message.from_user.id):
            await message.answer(new_users_only_text, reply_markup=back_markup)
            return

    balance_now = await get_balance(session, message.from_user.id)

    base_price_raw = temp_payload.get("selected_price_rub")
    if base_price_raw is None:
        base_price_raw = temp_payload.get("cost")
    if base_price_raw is None:
        try:
            base_price_raw = int(float(balance_now) + float(required_amount))
        except Exception:
            base_price_raw = required_amount

    try:
        base_price = int(base_price_raw)
    except (TypeError, ValueError):
        base_price = int(required_amount)

    percent_raw = int(getattr(coupon, "percent", 0) or 0)
    amount_raw = int(getattr(coupon, "amount", 0) or 0)
    days_raw = int(getattr(coupon, "days", 0) or 0)

    if percent_raw <= 0 and days_raw > 0:
        await message.answer(days_coupon_text, reply_markup=back_markup)
        return

    if percent_raw <= 0 and amount_raw > 0:
        # Купон на баланс закрывает недостачу деньгами: зачисляем и пересчитываем.
        from services.coupons import apply_fixed_coupon
        from services.errors import ServiceError

        try:
            await apply_fixed_coupon(session=session, user_id=message.from_user.id, tg_id=message.from_user.id, code=code)
        except ServiceError as e:
            await message.answer(f"❌ {e.message}", reply_markup=back_markup)
            return
        except Exception as e:
            logger.error("[FastFlow] купон на баланс не применён: {}", e)
            await message.answer(not_applicable_text, reply_markup=back_markup)
            return

        balance_after = await get_balance(session, message.from_user.id)
        price_now = int(temp_payload.get("selected_price_rub") or temp_payload.get("cost") or required_amount)
        left = int(max(0, ceil(float(price_now) - float(balance_after))))
        payload_after = dict(temp_payload)
        payload_after["required_amount"] = left
        await create_temporary_data(session, message.from_user.id, str(temp_key), payload_after)
        await state.update_data(required_amount=left, temp_payload=payload_after)
        await state.set_state(None)
        if left == 0:
            await _finish_from_balance(message, session, str(temp_key), payload_after)
            return
        await message.answer(
            f"✅ Купон активирован, на баланс начислено {amount_raw}. Осталось доплатить {left}.",
            reply_markup=back_markup,
        )
        return

    new_price, discount = apply_percent_coupon(int(base_price), coupon)
    if int(discount) <= 0:
        await message.answer(not_applicable_text, reply_markup=back_markup)
        return

    required_amount_new = int(max(0, ceil(float(new_price) - float(balance_now))))

    percent_value = int(getattr(coupon, "percent", 0) or 0)

    temp_payload_updated = dict(temp_payload)
    temp_payload_updated["required_amount"] = int(required_amount_new)
    temp_payload_updated["pending_coupon_id"] = int(coupon.id)
    # Без кода скидка теряется: списание пересчитывает цену заново по тарифу.
    temp_payload_updated["applied_coupon_code"] = code
    if "selected_price_rub" in temp_payload_updated:
        temp_payload_updated["selected_price_rub"] = int(new_price)
    if "cost" in temp_payload_updated:
        temp_payload_updated["cost"] = int(new_price)

    await create_temporary_data(session, message.from_user.id, str(temp_key), temp_payload_updated)

    await state.update_data(
        required_amount=int(required_amount_new),
        temp_payload=temp_payload_updated,
        applied_coupon={
            "code": code,
            "percent": percent_value,
            "discount": int(discount),
            "old_price": int(base_price),
            "new_price": int(new_price),
        },
    )
    await state.set_state(None)

    if required_amount_new == 0:
        await _finish_from_balance(message, session, str(temp_key), temp_payload_updated)
        return

    payment_config = await get_payment_providers_config()
    providers_map = await get_providers_with_hooks(payment_config)

    configured = [str(p) for p in (USE_NEW_PAYMENT_FLOW or [])]
    configured_upper = [p.upper() for p in configured]
    configured_set = set(configured_upper)

    providers: list[str] = []
    for p_up in configured_upper:
        cfg = providers_map.get(p_up) or {}
        if cfg.get("fast") and cfg.get("enabled", True):
            providers.append(p_up)
    providers = sort_provider_names(providers, providers_map)

    mode, one_screen = get_currency_mode()
    multicurrency_mode = mode == "RUB+USD"

    if not multicurrency_mode:
        allowed_currency = "RUB" if mode == "RUB" else "USD"
        filtered: list[str] = []
        for p_up in providers:
            cfg = providers_map.get(p_up) or {}
            curr = str(cfg.get("currency") or "").upper()
            if curr in (allowed_currency, "RUB+USD"):
                filtered.append(p_up)
        providers = filtered

    tribute_cfg = providers_map.get("TRIBUTE") or {}
    tribute_link = (TRIBUTE_LINK or "").strip()
    tribute_enabled = "TRIBUTE" in configured_set and tribute_cfg.get("enabled", True) and bool(tribute_link)

    stars_cfg = providers_map.get("STARS") or {}
    stars_enabled_for_fast = "STARS" in configured_set and stars_cfg.get("fast") and stars_cfg.get("enabled", True)

    lead_text = await shortfall_lead_text(
        session,
        message.from_user.id,
        int(required_amount_new),
        getattr(message.from_user, "language_code", None),
    )

    coupon_text = FASTFLOW_COUPON_APPLIED_TEMPLATE.format(
        code=code,
        percent=percent_value,
        old_price=int(base_price),
        discount=int(discount),
        new_price=int(new_price),
    )

    if multicurrency_mode and not one_screen:
        keyboard_original = build_currency_choice_kb(show_stars=stars_enabled_for_fast, show_tribute=tribute_enabled)

        rows = keyboard_original.export()
        profile_row = None
        kept_rows: list[list[InlineKeyboardButton]] = []

        for row in rows:
            if any(getattr(button, "callback_data", None) == "profile" for button in row):
                if profile_row is None:
                    profile_row = row
                continue
            kept_rows.append(row)

        keyboard = InlineKeyboardBuilder()
        for row in kept_rows:
            keyboard.row(*row)

        if BUTTONS_CONFIG.get("COUPON_BUTTON_ENABLE", True):
            keyboard.row(InlineKeyboardButton(text=btn.COUPON_RESTART, callback_data="fastflow_coupon"))

        if profile_row:
            keyboard.row(*profile_row)
        else:
            keyboard.row(InlineKeyboardButton(text=btn.MAIN_MENU, callback_data="profile"))

        await message.answer(
            f"{lead_text}\n\n{coupon_text}\n\n{FAST_PAY_CHOOSE_CURRENCY}",
            reply_markup=keyboard.as_markup(),
        )
        return

    if not providers and not tribute_enabled:
        await message.answer(no_methods_text)
        return

    keyboard = InlineKeyboardBuilder()
    for provider_upper in providers:
        button_text = getattr(btn, provider_upper, provider_upper)
        if one_screen:
            cfg = providers_map.get(provider_upper) or {}
            curr = cfg.get("currency")
            if curr and curr != "RUB+USD":
                button_text = f"{button_text} ({currency_label(curr)})"
        keyboard.row(
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"choose_payment_provider|{provider_upper}",
            )
        )

    if tribute_enabled:
        keyboard.row(
            InlineKeyboardButton(
                text=getattr(btn, "TRIBUTE", "TRIBUTE"),
                url=tribute_link,
            )
        )

    if BUTTONS_CONFIG.get("COUPON_BUTTON_ENABLE", True):
        keyboard.row(InlineKeyboardButton(text=btn.COUPON_RESTART, callback_data="fastflow_coupon"))
    keyboard.row(InlineKeyboardButton(text=btn.MAIN_MENU, callback_data="profile"))

    await message.answer(
        f"{lead_text}\n\n{coupon_text}\n\n{FAST_PAY_CHOOSE_PROVIDER}",
        reply_markup=keyboard.as_markup(),
    )


@router.callback_query(F.data.startswith("choose_payment_currency|"))
async def choose_payment_currency(callback_query: CallbackQuery, state: FSMContext, session: Any):
    payment_config = await get_payment_providers_config()
    providers_map = await get_providers_with_hooks(payment_config)

    currency = callback_query.data.split("|")[1]
    data = await state.get_data()

    providers = data.get("fastflow_providers") or []
    if not providers:
        configured = [str(p) for p in (USE_NEW_PAYMENT_FLOW or [])]
        providers = [p.upper() for p in configured]

    filtered = sort_provider_names(
        [
            provider_upper
            for provider_upper in (p.upper() for p in providers)
            if (providers_map.get(provider_upper) or {}).get("currency") == currency
            and (providers_map.get(provider_upper) or {}).get("fast")
            and (providers_map.get(provider_upper) or {}).get("enabled", True)
        ],
        providers_map,
    )
    await state.update_data(chosen_currency=currency)

    if not filtered:
        keyboard = InlineKeyboardBuilder().row(InlineKeyboardButton(text=btn.BACK, callback_data="profile"))
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
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"choose_payment_provider|{provider_upper}",
            )
        )

    if BUTTONS_CONFIG.get("COUPON_BUTTON_ENABLE", True):
        keyboard.row(InlineKeyboardButton(text=btn.COUPON, callback_data="fastflow_coupon"))
    keyboard.row(InlineKeyboardButton(text=btn.MAIN_MENU, callback_data="profile"))

    lead_text = await shortfall_lead_text(
        session,
        callback_query.from_user.id,
        data.get("required_amount"),
        getattr(callback_query.from_user, "language_code", None),
        force_currency=currency,
    )
    text = f"{lead_text}\n\nВалюта: {currency_label(currency)}\n{FAST_PAY_CHOOSE_PROVIDER}"
    await edit_or_send_message(
        target_message=callback_query.message,
        text=text,
        reply_markup=keyboard.as_markup(),
    )


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

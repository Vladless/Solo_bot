from datetime import datetime, timezone

import pytz

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from database import (
    get_tariff_by_id,
)
from handlers.utils import edit_or_send_message, render_text
from settings.buttons import MAIN_MENU
from settings.texts import (
    RENEWAL_SWITCH_CREDIT_DAYS,
    RENEWAL_SWITCH_CREDIT_KEEP,
    RENEWAL_SWITCH_CREDIT_REFUND,
    RENEWAL_SWITCH_PAY,
    RENEWAL_SWITCH_PAY_FREE,
    RENEWAL_SWITCH_PAY_KEEP,
    RENEWAL_SWITCH_PAY_REFUND,
    RENEWAL_SWITCH_TEXT,
)


moscow_tz = pytz.timezone("Europe/Moscow")


def _tariff_config_label(duration_days: int, device_limit: int | None, addon_devices: int = 0) -> str:
    from handlers.tariffs.addons.utils import format_devices_label
    from services.formatting import format_duration_days

    parts = [format_duration_days(int(duration_days or 0))]
    if device_limit is not None:
        dev = format_devices_label(device_limit)
        if addon_devices > 0:
            dev = f"{dev} + {addon_devices} докуплено"
        parts.append(dev)
    return ", ".join(parts)


def _build_switch_text(*, old_label: str, new_label: str, quote) -> str:
    """Собирает экран смены тарифа по шаблону из файла текстов."""
    from services.formatting import format_days

    if quote.keeps_period:
        credit = RENEWAL_SWITCH_CREDIT_KEEP.format(credit=quote.credit_rub)
        if quote.net_cost_rub > 0:
            pay = RENEWAL_SWITCH_PAY_KEEP.format(amount=quote.net_cost_rub)
        elif quote.refund_to_balance_rub > 0:
            pay = RENEWAL_SWITCH_PAY_REFUND.format(amount=quote.refund_to_balance_rub)
        else:
            pay = RENEWAL_SWITCH_PAY_FREE
    elif quote.credit_days > 0:
        credit = RENEWAL_SWITCH_CREDIT_DAYS.format(
            credit=quote.credit_value_rub,
            days=format_days(quote.credit_days),
        )
        pay = RENEWAL_SWITCH_PAY.format(amount=quote.net_cost_rub) if quote.net_cost_rub > 0 else RENEWAL_SWITCH_PAY_FREE
    else:
        credit = RENEWAL_SWITCH_CREDIT_REFUND.format(credit=quote.credit_rub)
        if quote.net_cost_rub > 0:
            pay = RENEWAL_SWITCH_PAY.format(amount=quote.net_cost_rub)
        else:
            pay = RENEWAL_SWITCH_PAY_REFUND.format(amount=quote.refund_to_balance_rub)

    return render_text(
        RENEWAL_SWITCH_TEXT,
        old=old_label,
        new=new_label,
        credit=credit,
        pay=pay,
    )
from .flow import normalize_expiry_ms


async def _maybe_show_switch_confirm(
    callback_query: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    *,
    tg_id: int,
    client_id: str,
    email: str,
    new_tariff_id: int,
    record: dict,
    selected_device: int | None,
    selected_traffic: int | None,
) -> bool:
    """Экран подтверждения смены тарифа (остаток → на баланс). True — экран показан."""
    from services.keys import compute_renewal_quote

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    quote = await compute_renewal_quote(
        session,
        billing_user_id=tg_id,
        key_email=email,
        current_tariff_id=record.get("tariff_id"),
        current_selected_device=record.get("selected_device_limit"),
        current_selected_traffic=record.get("selected_traffic_limit"),
        current_expiry_ms=normalize_expiry_ms(record.get("expiry_time")),
        now_ms=now_ms,
        new_tariff_id=int(new_tariff_id),
        new_selected_device=selected_device,
        new_selected_traffic=selected_traffic,
    )
    if not quote.is_switch or (quote.credit_rub <= 0 and quote.credit_days <= 0):
        return False

    await state.update_data(
        renew_sw_client_id=client_id,
        renew_sw_email=email,
        renew_sw_tariff_id=int(new_tariff_id),
        renew_sw_selected_device=selected_device,
        renew_sw_selected_traffic=selected_traffic,
    )

    old_tariff = await get_tariff_by_id(session, int(record.get("tariff_id"))) if record.get("tariff_id") else None
    new_tariff = await get_tariff_by_id(session, int(new_tariff_id))

    old_dev = record.get("selected_device_limit") if (old_tariff and old_tariff.get("configurable")) else None
    new_dev = quote.selected_device_limit if (new_tariff and new_tariff.get("configurable")) else None

    old_addon = 0
    if old_dev is not None:
        cur_dev = record.get("current_device_limit")
        if cur_dev is not None:
            old_addon = max(0, int(cur_dev) - int(old_dev))

    old_label = _tariff_config_label(
        int(old_tariff.get("duration_days") or 0) if old_tariff else 0,
        int(old_dev) if old_dev is not None else None,
        old_addon,
    )
    new_label = _tariff_config_label(
        quote.duration_days,
        int(new_dev) if new_dev is not None else None,
    )

    text = _build_switch_text(old_label=old_label, new_label=new_label, quote=quote)
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✅ Подтвердить смену", callback_data="renew_sw_confirm"))
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
    await edit_or_send_message(
        target_message=callback_query.message,
        text=text,
        reply_markup=builder.as_markup(),
    )
    return True

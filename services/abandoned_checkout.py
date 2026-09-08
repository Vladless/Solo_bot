from datetime import datetime, timedelta
from urllib.parse import urlencode

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models.users import TemporaryData
from database.notifications import add_notification, check_notification_time
from database.web_notifications import notify_web
from logger import logger
from settings.buttons import COMPLETE_PAYMENT
from settings.texts import (
    ABANDONED_CHECKOUT_ADDONS_BODY,
    ABANDONED_CHECKOUT_ADDONS_TITLE,
    ABANDONED_CHECKOUT_NEW_BODY,
    ABANDONED_CHECKOUT_NEW_TITLE,
    ABANDONED_CHECKOUT_RENEWAL_BODY,
    ABANDONED_CHECKOUT_RENEWAL_TITLE,
)


_WAITING_STATES = (
    "waiting_for_payment",
    "waiting_for_renewal_payment",
    "waiting_for_addons_payment",
)
_MIN_AGE_MINUTES = 30
_MAX_AGE_HOURS = 48
_DEDUP_HOURS = 24
_NOTIF_TYPE = "abandoned_checkout"


def _resume_href(state: str, data: dict | None) -> str:
    """Ссылка «докончить оплату» для web-уведомления: тот же тариф и те же опции."""
    payload = data if isinstance(data, dict) else {}
    client_id = str(payload.get("client_id") or "").strip()
    tariff_id = payload.get("tariff_id")
    params: dict[str, str] = {}

    if state == "waiting_for_renewal_payment" and client_id:
        params = {"flow": "renew", "subKey": client_id}
        if tariff_id:
            params["tariff_id"] = str(int(tariff_id))
    elif state == "waiting_for_addons_payment" and client_id:
        params = {"flow": "addons", "subKey": client_id}
        for key, param in (
            ("selected_device_limit", "selected_device_limit"),
            ("selected_traffic_gb", "selected_traffic_gb"),
        ):
            value = payload.get(key)
            if value is not None:
                params[param] = str(value)
    elif tariff_id:
        params = {"tariff_id": str(int(tariff_id))}
        for key, param in (
            ("selected_device_limit", "selected_device_limit"),
            ("selected_traffic_limit_gb", "selected_traffic_gb"),
        ):
            value = payload.get(key)
            if value is not None:
                params[param] = str(value)
    else:
        required_amount = payload.get("required_amount")
        if required_amount:
            params = {"flow": "topup", "amount": str(int(required_amount))}

    if not params:
        return "/dashboard?tab=keys"
    return "/checkout?" + urlencode(params)


def _compose(state: str) -> tuple[str, str]:
    if state == "waiting_for_renewal_payment":
        return (ABANDONED_CHECKOUT_RENEWAL_TITLE, ABANDONED_CHECKOUT_RENEWAL_BODY)
    if state == "waiting_for_addons_payment":
        return (ABANDONED_CHECKOUT_ADDONS_TITLE, ABANDONED_CHECKOUT_ADDONS_BODY)
    return (ABANDONED_CHECKOUT_NEW_TITLE, ABANDONED_CHECKOUT_NEW_BODY)


async def send_abandoned_checkout_reminders(session: AsyncSession) -> int:
    """Находит брошенные оформления (висящие waiting_*-intent'ы) и шлёт одно напоминание.
    Сигнал: temporary_data чистится при успешной оплате, поэтому провисевший intent = брошен."""
    now = datetime.utcnow()
    oldest = now - timedelta(hours=_MAX_AGE_HOURS)
    newest = now - timedelta(minutes=_MIN_AGE_MINUTES)
    rows = (
        (
            await session.execute(
                select(TemporaryData).where(
                    and_(
                        TemporaryData.state.in_(_WAITING_STATES),
                        TemporaryData.updated_at >= oldest,
                        TemporaryData.updated_at <= newest,
                        TemporaryData.tg_id.isnot(None),
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return 0

    sent = 0
    for row in rows:
        try:
            tg_id = int(row.tg_id)
        except (TypeError, ValueError):
            continue
        try:
            allowed = await check_notification_time(session, tg_id, _NOTIF_TYPE, hours=_DEDUP_HOURS)
        except Exception:
            allowed = False
        if not allowed:
            continue

        title, body = _compose(str(row.state or ""))

        try:
            from bot import bot

            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=COMPLETE_PAYMENT, callback_data="resume_checkout")]]
            )
            await bot.send_message(tg_id, f"{title}\n\n{body}", parse_mode=None, reply_markup=keyboard)
        except Exception as exc:
            logger.warning("[AbandonedCheckout] tg-сообщение {} не отправлено: {}", tg_id, exc)

        try:
            await notify_web(
                session,
                tg_id=tg_id,
                type="payment_pending",
                title=title,
                message=body,
                data={"href": _resume_href(str(row.state or ""), row.data)},
            )
        except Exception:
            pass

        try:
            await add_notification(session, tg_id, _NOTIF_TYPE, commit=False)
        except Exception:
            pass

        sent += 1

    return sent

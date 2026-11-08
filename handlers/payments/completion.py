from datetime import datetime

import pytz

from aiogram import Bot
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import bot
from core.settings.tariffs_config import TARIFFS_CONFIG
from database.access.resolution import UserId, notify_telegram_chat_id
from database.keys import get_key_by_server, get_key_details
from handlers.keys.key_mode.key_cluster_mode import send_or_edit_key_created_view
from handlers.keys.utils import build_key_callback
from handlers.notifications.webapp_only import webapp_only_markup
from handlers.utils import get_russian_month
from logger import logger
from services.formatting import build_renewal_done_text
from services.keys import normalize_expiry_ms
from services.tariffs.tariff_display import get_key_tariff_display
from settings.buttons import MAIN_MENU, MY_SUB
from settings.texts import ADDONS_APPLIED_TEXT, ADDONS_PACK_SUCCESS_TEXT


async def notify_completed_addons(
    session: AsyncSession, user_id: int, client_id: str, *, pack_mode: bool, bot_instance: Bot | None = None
) -> None:
    """Подтверждает оплаченную докупку без открытия экрана подписки."""
    owner = UserId(user_id)
    key = await get_key_by_server(session, owner, str(client_id))
    if key is None or key.user_id != int(owner) or key.client_id != str(client_id):
        logger.warning("[Payments] Подписка оплаченной докупки не найдена у владельца {}", int(owner))
        return
    record = await get_key_details(session, key.email)
    if not record or record.get("user_id") != int(owner) or record.get("client_id") != key.client_id:
        logger.warning("[Payments] Данные оплаченной докупки не соответствуют владельцу {}", int(owner))
        return
    chat_id = await notify_telegram_chat_id(session, owner)
    if chat_id is None:
        return
    keyboard = InlineKeyboardBuilder()
    keyboard.row(
        InlineKeyboardButton(text=MY_SUB, callback_data=build_key_callback("view_key", key.client_id, key.email))
    )
    keyboard.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
    sender = bot_instance if bot_instance is not None else bot
    await sender.send_message(
        chat_id=chat_id,
        text=ADDONS_PACK_SUCCESS_TEXT if pack_mode else ADDONS_APPLIED_TEXT,
        reply_markup=webapp_only_markup() or keyboard.as_markup(),
    )


async def notify_completed_checkout(
    session: AsyncSession, user_id: int, flow: str, client_id: str, *, addon_pack_mode: bool | None = None
) -> None:
    """Показывает результат уже выполненной покупки или продления."""
    if flow == "key_addons":
        await notify_completed_addons(
            session,
            user_id,
            client_id,
            pack_mode=bool(TARIFFS_CONFIG.get("KEY_ADDONS_PACK_MODE")) if addon_pack_mode is None else addon_pack_mode,
        )
        return
    if flow not in {"tariff_purchase", "trial_purchase", "key_renewal"}:
        return
    owner = UserId(user_id)
    key = await get_key_by_server(session, owner, str(client_id))
    if key is None or key.user_id != int(owner):
        logger.warning("[Payments] Подписка оплаченного заказа не найдена у владельца {}", int(owner))
        return
    record = await get_key_details(session, key.email)
    if not record or record.get("user_id") != int(owner) or record.get("client_id") != key.client_id:
        logger.warning("[Payments] Данные оплаченной подписки не соответствуют владельцу {}", int(owner))
        return
    if flow in {"tariff_purchase", "trial_purchase"}:
        await send_or_edit_key_created_view(
            session=session,
            tg_id=owner,
            key_record=record,
            client_id=key.client_id,
            email=key.email,
            final_link=record.get("link") or "",
            cluster_id=key.server_id,
            plan=key.tariff_id,
            target_message=None,
        )
        return
    tariff_name, _, traffic_limit, device_limit, *_ = await get_key_tariff_display(session, record)
    expiry = datetime.fromtimestamp(normalize_expiry_ms(key.expiry_time) / 1000, tz=pytz.timezone("Europe/Moscow"))
    expiry_date = expiry.strftime("%d %B %Y, %H:%M").replace(expiry.strftime("%B"), get_russian_month(expiry))
    text = build_renewal_done_text(
        tariff_name=tariff_name,
        traffic_limit=traffic_limit,
        device_limit=device_limit,
        expiry_date=expiry_date,
    )
    keyboard = InlineKeyboardBuilder()
    keyboard.row(
        InlineKeyboardButton(text=MY_SUB, callback_data=build_key_callback("view_key", key.client_id, key.email))
    )
    keyboard.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
    chat_id = await notify_telegram_chat_id(session, owner)
    if chat_id is not None:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=webapp_only_markup() or keyboard.as_markup())

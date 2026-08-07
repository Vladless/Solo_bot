from datetime import datetime
from math import ceil

import pytz

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import bot
from database import (
    get_balance,
    get_key_details,
    get_tariff_by_id,
)
from database.access.resolution import notify_telegram_chat_id
from database.models import Server
from handlers.payments.fast_payment_flow import try_fast_payment_flow
from handlers.utils import edit_or_send_message, get_russian_month
from hooks.hook_buttons import insert_hook_buttons
from hooks.processors import (
    process_renewal_complete,
)
from logger import logger
from services.formatting import build_renewal_done_text
from services.payments.currency_rates import format_for_user
from services.tariffs.tariff_display import GB, get_effective_limits_for_key
from settings.buttons import MAIN_MENU, MY_SUB, PAYMENT
from settings.config import USE_NEW_PAYMENT_FLOW
from settings.texts import (
    INSUFFICIENT_FUNDS_RENEWAL_MSG,
)

from ..utils import (
    build_key_callback,
)


moscow_tz = pytz.timezone("Europe/Moscow")


def normalize_expiry_ms(raw_value: int | float | None) -> int:
    """Нормализует таймстамп истечения в миллисекунды."""
    if not raw_value:
        return 0
    value = int(raw_value)
    if value > 10**13:
        value *= 1000
    return value


async def _finalize_renewal(
    callback_query: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    *,
    tg_id: int,
    client_id: str,
    email: str,
    tariff_id: int,
    duration_days: int,
    total_gb: int,
    cost: float,
    full_price: int,
    new_expiry_time: int,
    selected_device: int | None,
    selected_traffic: int | None,
) -> None:
    """Списание и завершение смены тарифа. cost — нетто (new_full − остаток), может быть < 0."""
    balance = round(await get_balance(session, tg_id), 2)
    cost = round(float(cost), 2)

    if balance < cost:
        required_amount = ceil(cost - balance)

        if USE_NEW_PAYMENT_FLOW:
            temp_payload = {
                "tariff_id": int(tariff_id),
                "client_id": client_id,
                "cost": cost,
                "required_amount": required_amount,
                "new_expiry_time": int(new_expiry_time),
                "selected_duration_days": int(duration_days),
                "total_gb": int(total_gb or 0),
                "email": email,
                "selected_price_rub": int(full_price),
            }
            if selected_device is not None:
                temp_payload["selected_device_limit"] = selected_device
            if selected_traffic is not None:
                temp_payload["selected_traffic_limit"] = selected_traffic

            handled = await try_fast_payment_flow(
                callback_query,
                session,
                state,
                tg_id=tg_id,
                temp_key="waiting_for_renewal_payment",
                temp_payload=temp_payload,
                required_amount=required_amount,
            )
            if handled:
                return

        language_code = getattr(callback_query.from_user, "language_code", None)
        required_amount_text = await format_for_user(session, tg_id, float(required_amount), language_code)

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=PAYMENT, callback_data="pay"))
        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
        await edit_or_send_message(
            target_message=callback_query.message,
            text=INSUFFICIENT_FUNDS_RENEWAL_MSG.format(required_amount=required_amount_text),
            reply_markup=builder.as_markup(),
        )
        return

    await complete_key_renewal(
        session=session,
        tg_id=tg_id,
        client_id=client_id,
        email=email,
        new_expiry_time=int(new_expiry_time),
        total_gb=int(total_gb or 0),
        cost=cost,
        callback_query=callback_query,
        tariff_id=int(tariff_id),
        selected_device_limit=selected_device,
        selected_traffic_limit=selected_traffic,
        selected_price_rub=int(full_price),
        credited_to_balance_rub=max(0, int(round(-cost))),
    )


async def resolve_cluster_name(session: AsyncSession, server_or_cluster: str) -> str | None:
    """Определяет имя кластера по server_id или cluster_name."""
    result = await session.execute(select(Server).where(Server.cluster_name == server_or_cluster).limit(1))
    server = result.scalars().first()
    if server:
        return server_or_cluster

    result = await session.execute(select(Server.cluster_name).where(Server.server_name == server_or_cluster).limit(1))
    row = result.scalar()
    return row


async def complete_key_renewal(
    session: AsyncSession,
    tg_id: int,
    client_id: str,
    email: str,
    new_expiry_time: int,
    total_gb: int,
    cost: float,
    callback_query: CallbackQuery | None,
    tariff_id: int,
    selected_device_limit: int | None = None,
    selected_traffic_limit: int | None = None,
    selected_price_rub: int | None = None,
    credited_to_balance_rub: int = 0,
):
    """Продлевает подписку через сервис и отправляет Telegram-уведомление."""
    from services.errors import ServiceError
    from services.keys import execute_renewal

    try:
        logger.info(f"[Info] Продление ключа {client_id} по тарифу ID={tariff_id} (Start)")

        tg_notify = await notify_telegram_chat_id(session, tg_id)
        renewal_hook_chat = tg_notify if tg_notify is not None else tg_id

        waiting_message = None
        wait_text = "⏳ Подождите. Идет продление подписки…"
        try:
            if callback_query:
                await edit_or_send_message(
                    target_message=callback_query.message,
                    text=wait_text,
                    reply_markup=None,
                )
            elif tg_notify is not None:
                waiting_message = await bot.send_message(tg_notify, wait_text)
            else:
                logger.info(f"[Renew] Нет Telegram-чата для экрана ожидания (ref={tg_id}), пропуск")
        except Exception as e:
            logger.warning(f"[Renew] Не удалось показать экран ожидания: {e}")

        key_info = await get_key_details(session, email)
        if not key_info:
            logger.error(f"[Error] Ключ с client_id={client_id} не найден в БД.")
            return False

        server_or_cluster = key_info["server_id"]

        try:
            await execute_renewal(
                session=session,
                billing_user_id=tg_id,
                client_id=client_id,
                key_email=email,
                key_server_id=server_or_cluster,
                tariff_id=tariff_id,
                new_expiry_time=new_expiry_time,
                total_gb=total_gb,
                cost=cost,
                selected_device_limit=selected_device_limit,
                selected_traffic_limit=selected_traffic_limit,
                selected_price_rub=selected_price_rub,
            )
        except ServiceError as e:
            logger.error(f"[Error] Сервис продления: {e.message}")
            err_text = f"⚠️ {e.message}"
            err_kb = InlineKeyboardBuilder()
            err_kb.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
            err_markup = err_kb.as_markup()
            try:
                if callback_query:
                    await edit_or_send_message(
                        target_message=callback_query.message,
                        text=err_text,
                        reply_markup=err_markup,
                    )
                elif waiting_message:
                    await edit_or_send_message(
                        target_message=waiting_message,
                        text=err_text,
                        reply_markup=err_markup,
                    )
                elif tg_notify is not None:
                    await bot.send_message(tg_notify, err_text, reply_markup=err_markup)
            except Exception as notify_err:
                logger.warning(f"[Renew] Не удалось показать ошибку продления: {notify_err}")
            return False

        tariff = await get_tariff_by_id(session, tariff_id)
        tariff_name = tariff["name"] if tariff else ""
        subgroup_title = tariff.get("subgroup_title", "") if tariff else ""

        device_limit_effective = selected_device_limit
        traffic_limit_gb_effective = selected_traffic_limit or 0

        if tariff and tariff.get("configurable"):
            sel_dev = int(selected_device_limit) if selected_device_limit is not None else None
            sel_trf = int(selected_traffic_limit) if selected_traffic_limit is not None else None
            dev_eff, trf_bytes = await get_effective_limits_for_key(
                session=session,
                tariff_id=tariff_id,
                selected_device_limit=sel_dev,
                selected_traffic_gb=sel_trf,
            )
            device_limit_effective = dev_eff
            traffic_limit_gb_effective = int(trf_bytes / GB) if trf_bytes else 0

        formatted_expiry_date = datetime.fromtimestamp(new_expiry_time / 1000, tz=moscow_tz).strftime("%d %B %Y, %H:%M")
        formatted_expiry_date = formatted_expiry_date.replace(
            datetime.fromtimestamp(new_expiry_time / 1000, tz=moscow_tz).strftime("%B"),
            get_russian_month(datetime.fromtimestamp(new_expiry_time / 1000, tz=moscow_tz)),
        )

        response_message = build_renewal_done_text(
            tariff_name=tariff_name,
            traffic_limit=traffic_limit_gb_effective,
            device_limit=device_limit_effective,
            expiry_date=formatted_expiry_date,
        )

        if credited_to_balance_rub and credited_to_balance_rub > 0:
            new_balance = round(await get_balance(session, tg_id), 2)
            response_message += (
                f"\n💰 Остаток прежней подписки <b>{int(credited_to_balance_rub)} ₽</b> "
                f"зачислен на баланс. Текущий баланс: <b>{new_balance:g} ₽</b>"
            )

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=MY_SUB, callback_data=build_key_callback("view_key", client_id, email)))
        hook_commands = await process_renewal_complete(
            chat_id=renewal_hook_chat, admin=False, session=session, email=email, client_id=client_id
        )
        if hook_commands:
            builder = insert_hook_buttons(builder, hook_commands)

        renewal_media_path = "img/pic_renewed.jpg"

        try:
            if callback_query:
                await edit_or_send_message(
                    target_message=callback_query.message,
                    text=response_message,
                    reply_markup=builder.as_markup(),
                    media_path=renewal_media_path,
                )
            elif waiting_message:
                await edit_or_send_message(
                    target_message=waiting_message,
                    text=response_message,
                    reply_markup=builder.as_markup(),
                    media_path=renewal_media_path,
                )
            elif tg_notify is not None:
                await bot.send_message(tg_notify, response_message, reply_markup=builder.as_markup())
            else:
                logger.info(f"[Renew] Нет Telegram-чата для итогового сообщения (ref={tg_id}), пропуск")
        except Exception as e:
            logger.error(f"[Error] Ошибка при выводе финального сообщения: {e}")
            if tg_notify is not None:
                await bot.send_message(tg_notify, response_message, reply_markup=builder.as_markup())

        logger.info(f"[Info] Продление ключа {client_id} завершено успешно (User: {tg_id})")
        return True

    except Exception as e:
        logger.error(f"[Error] Ошибка в complete_key_renewal: {e}")
        return False

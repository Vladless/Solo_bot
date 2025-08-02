import time

from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import (
    get_key_details,
    get_servers,
    get_tariff_by_id,
    mark_key_as_frozen,
    mark_key_as_unfrozen,
)
from handlers.buttons import APPLY, BACK, CANCEL
from handlers.keys.operations import renew_key_in_cluster, toggle_client_on_cluster
from handlers.texts import (
    FREEZE_SUBSCRIPTION_CONFIRM_MSG,
    SUBSCRIPTION_FROZEN_MSG,
    SUBSCRIPTION_UNFROZEN_MSG,
    UNFREEZE_SUBSCRIPTION_CONFIRM_MSG,
)
from handlers.utils import edit_or_send_message, handle_error
from logger import logger


router = Router()


@router.callback_query(F.data.startswith("unfreeze_subscription|"))
async def process_callback_unfreeze_subscription(callback_query: CallbackQuery, session: Any):
    key_name = callback_query.data.split("|")[1]
    confirm_text = UNFREEZE_SUBSCRIPTION_CONFIRM_MSG

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=APPLY,
            callback_data=f"unfreeze_subscription_confirm|{key_name}",
        ),
        InlineKeyboardButton(
            text=CANCEL,
            callback_data=f"view_key|{key_name}",
        ),
    )

    await edit_or_send_message(
        target_message=callback_query.message,
        text=confirm_text,
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("unfreeze_subscription_confirm|"))
async def process_callback_unfreeze_subscription_confirm(callback_query: CallbackQuery, session: Any):
    """
    Размораживает (включает) подписку.
    """
    tg_id = callback_query.message.chat.id
    key_name = callback_query.data.split("|")[1]

    try:
        record = await get_key_details(session, key_name)
        if not record:
            await callback_query.message.answer("Ключ не найден.")
            return

        email = record["email"]
        client_id = record["client_id"]
        cluster_id = record["server_id"]

        result = await toggle_client_on_cluster(cluster_id, email, client_id, enable=True, session=session)
        if result["status"] != "success":
            logger.warning(f"Не удалось включить подписку: {result.get('error') or result.get('results')}")

        servers = await get_servers(session)
        cluster_servers = servers.get(cluster_id, [])

        if not cluster_servers:
            text_error = "Сервер не найден."
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text=BACK, callback_data=f"view_key|{key_name}"))
            await edit_or_send_message(callback_query.message, text_error, builder.as_markup())
            return

        tariff = await get_tariff_by_id(session, record["tariff_id"]) if record.get("tariff_id") else None

        if not tariff:
            logger.info("[Unfreeze] Тариф не найден — применяем дефолтные значения.")
            total_gb = 0
            hwid_limit = 0
        else:
            total_gb = int(tariff.get("traffic_limit") or 0)
            hwid_limit = int(tariff.get("device_limit") or 0)

        now_ms = int(time.time() * 1000)
        leftover = record["expiry_time"]
        if leftover < 0:
            leftover = 0
        new_expiry_time = now_ms + leftover

        await mark_key_as_unfrozen(session, record["tg_id"], client_id, new_expiry_time)
        await session.commit()

        max(leftover / (1000 * 86400), 0.01)
        logger.info(
            f"[Unfreeze Debug] Запуск renew_key_in_cluster с expiry={new_expiry_time}, gb={total_gb}, hwid={hwid_limit}"
        )

        await renew_key_in_cluster(
            cluster_id=cluster_id,
            email=email,
            client_id=client_id,
            new_expiry_time=new_expiry_time,
            total_gb=total_gb,
            session=session,
            hwid_device_limit=hwid_limit,
            reset_traffic=False,
        )

        text_ok = SUBSCRIPTION_UNFROZEN_MSG
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=BACK, callback_data=f"view_key|{key_name}"))
        await edit_or_send_message(callback_query.message, text_ok, builder.as_markup())

    except Exception as e:
        await handle_error(tg_id, callback_query, f"Ошибка при включении подписки: {e}")


@router.callback_query(F.data.startswith("freeze_subscription|"))
async def process_callback_freeze_subscription(callback_query: CallbackQuery, session: Any):
    """
    Показывает пользователю диалог подтверждения заморозки (отключения) подписки.
    """
    key_name = callback_query.data.split("|")[1]

    confirm_text = FREEZE_SUBSCRIPTION_CONFIRM_MSG

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=APPLY,
            callback_data=f"freeze_subscription_confirm|{key_name}",
        ),
        InlineKeyboardButton(
            text=CANCEL,
            callback_data=f"view_key|{key_name}",
        ),
    )

    await edit_or_send_message(
        target_message=callback_query.message,
        text=confirm_text,
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("freeze_subscription_confirm|"))
async def process_callback_freeze_subscription_confirm(callback_query: CallbackQuery, session: Any):
    """
    Замораживает (отключает) подписку.
    """
    tg_id = callback_query.message.chat.id
    key_name = callback_query.data.split("|")[1]

    try:
        record = await get_key_details(session, key_name)
        if not record:
            await callback_query.message.answer("Ключ не найден.")
            return

        email = record["email"]
        client_id = record["client_id"]
        cluster_id = record["server_id"]

        result = await toggle_client_on_cluster(cluster_id, email, client_id, enable=False, session=session)

        if result["status"] == "success":
            now_ms = int(time.time() * 1000)
            time_left = record["expiry_time"] - now_ms
            if time_left < 0:
                time_left = 0

            await mark_key_as_frozen(session, record["tg_id"], client_id, time_left)
            await session.commit()

            text_ok = SUBSCRIPTION_FROZEN_MSG
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text=BACK, callback_data=f"view_key|{key_name}"))
            await edit_or_send_message(
                target_message=callback_query.message,
                text=text_ok,
                reply_markup=builder.as_markup(),
            )

        else:
            text_error = (
                f"Произошла ошибка при заморозке подписки.\nДетали: {result.get('error') or result.get('results')}"
            )
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text=BACK, callback_data=f"view_key|{key_name}"))
            await edit_or_send_message(
                target_message=callback_query.message,
                text=text_error,
                reply_markup=builder.as_markup(),
            )

    except Exception as e:
        await handle_error(tg_id, callback_query, f"Ошибка при заморозке подписки: {e}")

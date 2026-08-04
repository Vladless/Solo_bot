import uuid

from datetime import datetime

import pytz

from aiogram import Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    Message,
    WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import update

from bot import bot
from core.bootstrap import BUTTONS_CONFIG, MODES_CONFIG
from database import (
    get_key_details,
    get_trial,
    get_vless_enabled,
    update_balance,
    update_trial,
)
from database.access.resolution import notify_telegram_chat_id, resolve_user_optional
from database.models import Key
from handlers.keys.utils import build_key_callback
from handlers.utils import (
    build_support_button,
    edit_or_send_message,
    generate_random_email,
    get_least_loaded_cluster,
    is_full_remnawave_cluster,
)
from hooks.hook_buttons import insert_hook_buttons
from hooks.processors import (
    process_cluster_override,
    process_intercept_key_creation_message,
    process_key_creation_complete,
    process_remnawave_webapp_override,
)
from logger import logger
from services.errors import InsufficientFundsError
from services.operations import create_key_on_cluster
from services.tariffs.tariff_display import (
    build_key_created_message,
    get_effective_limits_for_key,
    resolve_price_to_charge,
)
from settings.buttons import (
    CONNECT_DEVICE,
    MAIN_MENU,
    MY_SUB,
    ROUTER_BUTTON,
    SUPPORT,
    TV_BUTTON,
)
from settings.config import REMNAWAVE_WEBAPP, REMNAWAVE_WEBAPP_OPEN_IN_BROWSER


router = Router()
moscow_tz = pytz.timezone("Europe/Moscow")


async def send_or_edit_key_created_view(
    session,
    tg_id: int,
    *,
    key_record: dict,
    client_id: str,
    email: str,
    final_link: str,
    cluster_id: str,
    key_name: str | None = None,
    plan: int | None = None,
    selected_device_limit: int | None = None,
    selected_traffic_gb: int | None = None,
    target_message: Message | CallbackQuery | None = None,
) -> None:
    if key_name is None:
        key_name = email

    target = None
    safe_to_edit = False
    if isinstance(target_message, CallbackQuery) and target_message.message:
        target = target_message.message
        safe_to_edit = True
    elif isinstance(target_message, Message):
        target = target_message
        safe_to_edit = True

    tg_notify = await notify_telegram_chat_id(session, tg_id)

    try:
        vless_enabled = False
        try:
            if plan:
                vless_enabled = await get_vless_enabled(session, plan)
            elif key_record.get("tariff_id"):
                vless_enabled = await get_vless_enabled(session, key_record["tariff_id"])
        except Exception:
            vless_enabled = False

        tv_button_enabled = bool(BUTTONS_CONFIG.get("ANDROID_TV_BUTTON_ENABLE"))

        builder = InlineKeyboardBuilder()
        if vless_enabled:
            builder.row(
                InlineKeyboardButton(
                    text=ROUTER_BUTTON, callback_data=build_key_callback("connect_router", client_id, key_name)
                )
            )
        else:
            if await is_full_remnawave_cluster(cluster_id, session):
                use_webapp = bool(MODES_CONFIG.get("REMNAWAVE_WEBAPP_ENABLED", REMNAWAVE_WEBAPP))
                open_in_browser = bool(
                    MODES_CONFIG.get("REMNAWAVE_WEBAPP_OPEN_IN_BROWSER", REMNAWAVE_WEBAPP_OPEN_IN_BROWSER)
                )
                if use_webapp and final_link:
                    use_webapp = await process_remnawave_webapp_override(
                        remnawave_webapp=use_webapp,
                        final_link=final_link,
                        session=session,
                    )

                if (
                    use_webapp
                    and final_link
                    and isinstance(final_link, str)
                    and final_link.startswith(("http://", "https://"))
                ):
                    if open_in_browser:
                        builder.row(InlineKeyboardButton(text=CONNECT_DEVICE, url=final_link))
                    else:
                        builder.row(InlineKeyboardButton(text=CONNECT_DEVICE, web_app=WebAppInfo(url=final_link)))
                    if tv_button_enabled:
                        builder.row(
                            InlineKeyboardButton(
                                text=TV_BUTTON, callback_data=build_key_callback("connect_tv", client_id, email)
                            )
                        )
                else:
                    builder.row(
                        InlineKeyboardButton(
                            text=CONNECT_DEVICE, callback_data=build_key_callback("connect_device", client_id, key_name)
                        )
                    )
            else:
                builder.row(
                    InlineKeyboardButton(
                        text=CONNECT_DEVICE, callback_data=build_key_callback("connect_device", client_id, key_name)
                    )
                )

        builder.row(
            InlineKeyboardButton(text=MY_SUB, callback_data=build_key_callback("view_key", client_id, key_name))
        )
        support_btn = await build_support_button()
        if support_btn:
            builder.row(support_btn)
        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

        if tg_notify is not None and await process_intercept_key_creation_message(
            chat_id=tg_notify,
            session=session,
            target_message=target_message,
        ):
            return

        hook_commands = (
            await process_key_creation_complete(
                chat_id=tg_notify,
                admin=False,
                session=session,
                email=email,
                key_name=key_name,
            )
            if tg_notify is not None
            else []
        )
        if hook_commands:
            builder = insert_hook_buttons(builder, hook_commands)

        key_message_text = await build_key_created_message(
            session=session,
            key_record=key_record,
            final_link=final_link,
            selected_device_limit=selected_device_limit,
            selected_traffic_gb=selected_traffic_gb,
        )

        default_media_path = "img/pic.jpg"
        if safe_to_edit and target is not None:
            await edit_or_send_message(
                target_message=target,
                text=key_message_text,
                reply_markup=builder.as_markup(),
                media_path=default_media_path,
            )
        elif tg_notify is not None:
            await bot.send_message(
                chat_id=tg_notify,
                text=key_message_text,
                reply_markup=builder.as_markup(),
            )
    except Exception as e:
        logger.error(
            f"[Key Created View] Ошибка отправки/редактирования окна о создании ключа для пользователя {tg_id}: {e}"
        )


async def key_cluster_mode(
    tg_id: int,
    expiry_time: datetime,
    state,
    session,
    message_or_query: Message | CallbackQuery | None = None,
    plan: int | None = None,
    selected_device_limit: int | None = None,
    selected_traffic_gb: int | None = None,
    selected_price_rub: int | None = None,
    skip_balance_charge: bool | None = None,
    is_trial: bool | None = None,
):
    target_message = None
    safe_to_edit = False

    if isinstance(message_or_query, CallbackQuery) and message_or_query.message:
        target_message = message_or_query.message
        safe_to_edit = True
    elif isinstance(message_or_query, Message):
        target_message = message_or_query
        safe_to_edit = True

    tg_notify = await notify_telegram_chat_id(session, tg_id)

    while True:
        key_name = await generate_random_email(session=session)
        existing_key = await get_key_details(session, key_name)
        if not existing_key:
            break

    client_id = str(uuid.uuid4())
    email = key_name.lower()
    expiry_timestamp = int(expiry_time.timestamp() * 1000)

    try:
        owner = await resolve_user_optional(session, tg_id)
        if owner is None:
            error_message = "Пользователь не найден."
            if safe_to_edit:
                await edit_or_send_message(target_message=target_message, text=error_message, reply_markup=None)
            elif tg_notify is not None:
                await bot.send_message(chat_id=tg_notify, text=error_message)
            return
        uid = owner.id

        data = await state.get_data() if state else {}
        if is_trial is None:
            is_trial = data.get("is_trial", False)
        skip_balance_charge = bool(skip_balance_charge)

        if selected_device_limit is None:
            selected_device_limit = data.get("config_selected_device_limit") or data.get("selected_device_limit")

        if selected_traffic_gb is None:
            selected_traffic_gb = data.get("config_selected_traffic_gb") or data.get("selected_traffic_limit_gb")

        effective_tariff_id = plan or data.get("tariff_id")

        device_limit, traffic_limit_bytes = await get_effective_limits_for_key(
            session=session,
            tariff_id=effective_tariff_id,
            selected_device_limit=selected_device_limit,
            selected_traffic_gb=selected_traffic_gb,
        )

        forced_cluster = await process_cluster_override(
            tg_id=tg_id,
            state_data=data,
            session=session,
            plan=plan,
        )

        if forced_cluster:
            least_loaded_cluster = forced_cluster
        else:
            try:
                least_loaded_cluster = await get_least_loaded_cluster(session)
            except ValueError as e:
                logger.error(f"Нет доступных кластеров: {e}")
                error_message = str(e)

                if safe_to_edit:
                    await edit_or_send_message(
                        target_message=target_message,
                        text=error_message,
                        reply_markup=None,
                    )
                elif tg_notify is not None:
                    await bot.send_message(chat_id=tg_notify, text=error_message)
                return

        if device_limit is None:
            device_limit = 0
        if traffic_limit_bytes is None:
            traffic_limit_bytes = 0

        if selected_price_rub is not None:
            price_to_charge = selected_price_rub
        else:
            price_to_charge = await resolve_price_to_charge(session, data)

        await create_key_on_cluster(
            cluster_id=least_loaded_cluster,
            tg_id=tg_id,
            client_id=client_id,
            email=email,
            expiry_timestamp=expiry_timestamp,
            plan=plan,
            session=session,
            hwid_limit=device_limit,
            traffic_limit_bytes=traffic_limit_bytes,
            is_trial=is_trial,
        )

        logger.info(f"[Key Creation] Ключ создан на кластере {least_loaded_cluster} для пользователя {tg_id}")

        await session.execute(
            update(Key)
            .where(Key.user_id == uid, Key.email == email)
            .values(
                selected_device_limit=selected_device_limit,
                selected_traffic_limit=selected_traffic_gb,
                selected_price_rub=price_to_charge,
            )
        )

        key_record = await get_key_details(session, email)
        if not key_record:
            raise ValueError(f"Ключ не найден после создания: {email}")

        final_link = key_record.get("link", "")

        if is_trial:
            trial_status = await get_trial(session, tg_id)
            if trial_status in [0, -1]:
                await update_trial(session, tg_id, 1)

        if price_to_charge and not skip_balance_charge:
            debited = await update_balance(session, tg_id, -int(price_to_charge))
            if debited is None:
                raise InsufficientFundsError("Недостаточно средств на балансе")

    except Exception as e:
        logger.error(f"[Error] Ошибка при создании ключа для пользователя {tg_id}: {e}")
        error_message = "❌ Произошла ошибка при создании подписки. Пожалуйста, попробуйте снова."

        if safe_to_edit:
            await edit_or_send_message(
                target_message=target_message,
                text=error_message,
                reply_markup=None,
            )
        elif tg_notify is not None:
            await bot.send_message(chat_id=tg_notify, text=error_message)
        return

    await send_or_edit_key_created_view(
        session=session,
        tg_id=tg_id,
        key_record=key_record,
        client_id=client_id,
        email=email,
        key_name=key_name,
        final_link=final_link,
        cluster_id=least_loaded_cluster,
        plan=plan,
        selected_device_limit=selected_device_limit,
        selected_traffic_gb=selected_traffic_gb,
        target_message=message_or_query,
    )

    if state:
        await state.clear()

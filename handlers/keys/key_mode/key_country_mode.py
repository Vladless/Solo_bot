import asyncio
import uuid

from datetime import UTC, datetime
from typing import Any

import pytz

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder
from py3xui import AsyncApi
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from bot import bot
from config import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    CONNECT_PHONE_BUTTON,
    PUBLIC_LINK,
    REMNAWAVE_LOGIN,
    REMNAWAVE_PASSWORD,
    SUPPORT_CHAT_URL,
)
from database import (
    add_user,
    check_server_name_by_cluster,
    check_user_exists,
    get_key_details,
    get_trial,
    update_balance,
    update_trial,
)
from database.models import Key, Server, Tariff
from handlers.buttons import BACK, CONNECT_DEVICE, CONNECT_PHONE, MAIN_MENU, MY_SUB, PC_BUTTON, SUPPORT, TV_BUTTON
from handlers.keys.key_utils import create_client_on_server
from handlers.texts import SELECT_COUNTRY_MSG, key_message_success
from handlers.utils import (
    edit_or_send_message,
    generate_random_email,
    get_least_loaded_cluster,
    is_full_remnawave_cluster,
)
from logger import logger
from panels.remnawave import RemnawaveAPI
from panels.three_xui import delete_client, get_xui_instance


router = Router()

moscow_tz = pytz.timezone("Europe/Moscow")


async def key_country_mode(
    tg_id: int,
    expiry_time: datetime,
    state: FSMContext,
    session: AsyncSession,
    message_or_query: Message | CallbackQuery | None = None,
    old_key_name: str = None,
    plan: int = None,
):
    target_message = None
    safe_to_edit = False

    if state and plan:
        await state.update_data(tariff_id=plan)

    if isinstance(message_or_query, CallbackQuery) and message_or_query.message:
        target_message = message_or_query.message
        safe_to_edit = True
    elif isinstance(message_or_query, Message):
        target_message = message_or_query
        safe_to_edit = True

    try:
        least_loaded_cluster = await get_least_loaded_cluster(session)
    except ValueError as e:
        logger.error(f"Нет доступных кластеров: {e}")
        text = str(e)
        if safe_to_edit:
            await edit_or_send_message(target_message=target_message, text=text, reply_markup=None)
        else:
            await bot.send_message(chat_id=tg_id, text=text)
        return

    result = await session.execute(
        select(
            Server.server_name,
            Server.api_url,
            Server.panel_type,
            Server.enabled,
            Server.max_keys,
        ).where(Server.cluster_name == least_loaded_cluster)
    )
    servers = result.mappings().all()

    if not servers:
        logger.error(f"❌ Нет серверов в кластере {least_loaded_cluster}")
        text = "❌ Нет доступных серверов в выбранном кластере."
        if safe_to_edit:
            await edit_or_send_message(target_message=target_message, text=text, reply_markup=None)
        else:
            await bot.send_message(chat_id=tg_id, text=text)
        return

    available_servers = []
    tasks = [asyncio.create_task(check_server_availability(server, session)) for server in servers]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for server, result in zip(servers, results, strict=False):
        if result is True:
            available_servers.append(server["server_name"])

    if not available_servers:
        logger.warning(f"[Country Selection] Нет доступных серверов в кластере {least_loaded_cluster}")
        text = "❌ Нет доступных серверов в выбранном кластере."
        if safe_to_edit:
            await edit_or_send_message(target_message=target_message, text=text, reply_markup=None)
        else:
            await bot.send_message(chat_id=tg_id, text=text)
        return

    logger.info(f"[Country Selection] Доступные сервера в кластере {least_loaded_cluster}: {available_servers}")

    builder = InlineKeyboardBuilder()
    ts = int(expiry_time.timestamp())
    for server_name in available_servers:
        if old_key_name:
            callback_data = f"select_country|{server_name}|{ts}|{old_key_name}"
        else:
            callback_data = f"select_country|{server_name}|{ts}"
        builder.row(InlineKeyboardButton(text=server_name, callback_data=callback_data))

    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    if safe_to_edit:
        await edit_or_send_message(
            target_message=target_message,
            text=SELECT_COUNTRY_MSG,
            reply_markup=builder.as_markup(),
        )
    else:
        await bot.send_message(
            chat_id=tg_id,
            text=SELECT_COUNTRY_MSG,
            reply_markup=builder.as_markup(),
        )


@router.callback_query(F.data.startswith("change_location|"))
async def change_location_callback(callback_query: CallbackQuery, session: Any):
    try:
        data = callback_query.data.split("|")
        if len(data) < 2:
            await callback_query.answer("❌ Некорректные данные", show_alert=True)
            return

        old_key_name = data[1]
        record = await get_key_details(session, old_key_name)
        if not record:
            await callback_query.answer("❌ Ключ не найден", show_alert=True)
            return

        expiry_timestamp = record["expiry_time"]
        ts = int(expiry_timestamp / 1000)

        current_server = record["server_id"]

        cluster_info = await check_server_name_by_cluster(session, current_server)
        if not cluster_info:
            await callback_query.answer("❌ Кластер для текущего сервера не найден", show_alert=True)
            return

        cluster_name = cluster_info["cluster_name"]

        servers = (
            (
                await session.execute(
                    select(
                        Server.server_name,
                        Server.api_url,
                        Server.panel_type,
                        Server.enabled,
                        Server.max_keys,
                    )
                    .where(Server.cluster_name == cluster_name)
                    .where(Server.server_name != current_server)
                )
            )
            .mappings()
            .all()
        )
        if not servers:
            await callback_query.answer("❌ Доступных серверов в кластере не найдено", show_alert=True)
            return

        available_servers = []
        tasks = []

        for server in servers:
            server_info = {
                "server_name": server["server_name"],
                "api_url": server["api_url"],
                "panel_type": server["panel_type"],
                "enabled": server.get("enabled", True),
                "max_keys": server.get("max_keys"),
            }
            task = asyncio.create_task(check_server_availability(server_info, session))
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for server, result in zip(servers, results, strict=False):
            if result is True:
                available_servers.append(server["server_name"])

        if not available_servers:
            await callback_query.answer("❌ Нет доступных серверов для смены локации", show_alert=True)
            return

        logger.info(f"Доступные страны для смены локации: {available_servers}")

        builder = InlineKeyboardBuilder()
        for country in available_servers:
            callback_data = f"select_country|{country}|{ts}|{old_key_name}"
            builder.row(InlineKeyboardButton(text=country, callback_data=callback_data))
        builder.row(InlineKeyboardButton(text=BACK, callback_data=f"view_key|{old_key_name}"))

        await edit_or_send_message(
            target_message=callback_query.message,
            text="🌍 Пожалуйста, выберите новую локацию для вашей подписки:",
            reply_markup=builder.as_markup(),
            media_path=None,
        )
    except Exception as e:
        logger.error(f"Ошибка при смене локации для пользователя {callback_query.from_user.id}: {e}")
        await callback_query.answer("❌ Ошибка смены локации. Попробуйте снова.", show_alert=True)


@router.callback_query(F.data.startswith("select_country|"))
async def handle_country_selection(callback_query: CallbackQuery, session: Any, state: FSMContext):
    """
    Обрабатывает выбор страны.
    Формат callback data:
      select_country|{selected_country}|{ts} [|{old_key_name} (опционально)]
    Если передан old_key_name – значит, происходит смена локации.
    """
    data = callback_query.data.split("|")
    if len(data) < 3:
        await callback_query.message.answer("❌ Некорректные данные. Попробуйте снова.")
        return

    selected_country = data[1]
    try:
        ts = int(data[2])
    except ValueError:
        await callback_query.message.answer("❌ Некорректное время истечения. Попробуйте снова.")
        return

    expiry_time = datetime.fromtimestamp(ts, tz=moscow_tz)

    old_key_name = data[3] if len(data) > 3 else None

    tg_id = callback_query.from_user.id
    logger.info(f"Пользователь {tg_id} выбрал страну: {selected_country}")
    logger.info(f"Получено время истечения (timestamp): {ts}")

    await finalize_key_creation(
        tg_id,
        expiry_time,
        selected_country,
        state,
        session,
        callback_query,
        old_key_name,
    )


async def finalize_key_creation(
    tg_id: int,
    expiry_time: datetime,
    selected_country: str,
    state: FSMContext | None,
    session: AsyncSession,
    callback_query: CallbackQuery,
    old_key_name: str = None,
    tariff_id: int | None = None,
):
    from_user = callback_query.from_user

    if not await check_user_exists(session, tg_id):
        await add_user(
            session=session,
            tg_id=from_user.id,
            username=from_user.username,
            first_name=from_user.first_name,
            last_name=from_user.last_name,
            language_code=from_user.language_code,
            is_bot=from_user.is_bot,
        )
        logger.info(f"[User] Новый пользователь {tg_id} добавлен")

    expiry_time = expiry_time.astimezone(moscow_tz)

    if old_key_name:
        old_key_details = await get_key_details(session, old_key_name)
        if not old_key_details:
            await callback_query.message.answer("❌ Ключ не найден. Попробуйте снова.")
            return

        key_name = old_key_name
        client_id = old_key_details["client_id"]
        email = old_key_details["email"]
        expiry_timestamp = old_key_details["expiry_time"]
        tariff_id = old_key_details.get("tariff_id") or tariff_id
    else:
        while True:
            key_name = generate_random_email()
            existing_key = await get_key_details(session, key_name)
            if not existing_key:
                break
        client_id = str(uuid.uuid4())
        email = key_name.lower()
        expiry_timestamp = int(expiry_time.timestamp() * 1000)

    traffic_limit_bytes = None
    device_limit = 0
    data = await state.get_data() if state else {}
    is_trial = data.get("is_trial", False)

    if data.get("tariff_id") or tariff_id:
        tariff_id = data.get("tariff_id") or tariff_id
        result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
        tariff = result.scalar_one_or_none()
        if tariff:
            if tariff.traffic_limit is not None:
                traffic_limit_bytes = int(tariff.traffic_limit) * 1024**3
            if tariff.device_limit is not None:
                device_limit = int(tariff.device_limit)

    public_link = None
    remnawave_link = None
    created_at = int(datetime.now(moscow_tz).timestamp() * 1000)

    try:
        result = await session.execute(select(Server).where(Server.server_name == selected_country))
        server_info = result.scalar_one_or_none()
        if not server_info:
            raise ValueError(f"Сервер {selected_country} не найден")

        panel_type = server_info.panel_type.lower()
        cluster_info = await check_server_name_by_cluster(session, server_info.server_name)
        if not cluster_info:
            raise ValueError(f"Кластер для сервера {server_info.server_name} не найден")

        is_full_remnawave = await is_full_remnawave_cluster(cluster_info["cluster_name"], session)

        if old_key_name:
            old_server_id = old_key_details["server_id"]
            if old_server_id:
                result = await session.execute(select(Server).where(Server.server_name == old_server_id))
                old_server_info = result.scalar_one_or_none()
                if old_server_info:
                    try:
                        if old_server_info.panel_type.lower() == "3x-ui":
                            xui = await get_xui_instance(old_server_info.api_url)
                            await delete_client(xui, old_server_info.inbound_id, email, client_id)
                            await session.execute(
                                update(Key).where(Key.tg_id == tg_id, Key.email == email).values(key=None)
                            )
                        elif old_server_info.panel_type.lower() == "remnawave":
                            remna = RemnawaveAPI(old_server_info.api_url)
                            if await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
                                await remna.delete_user(client_id)
                                await session.execute(
                                    update(Key)
                                    .where(Key.tg_id == tg_id, Key.email == email)
                                    .values(remnawave_link=None)
                                )
                    except Exception as e:
                        logger.warning(f"[Delete] Ошибка при удалении клиента: {e}")

        if panel_type == "remnawave" or is_full_remnawave:
            remna = RemnawaveAPI(server_info.api_url)
            if not await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
                raise ValueError(f"❌ Не удалось авторизоваться в Remnawave ({server_info.server_name})")

            expire_at = datetime.fromtimestamp(expiry_timestamp / 1000, UTC).isoformat() + "Z"
            user_data = {
                "username": email,
                "trafficLimitStrategy": "NO_RESET",
                "expireAt": expire_at,
                "telegramId": tg_id,
                "activeUserInbounds": [server_info.inbound_id],
            }
            if traffic_limit_bytes:
                user_data["trafficLimitBytes"] = traffic_limit_bytes
            if device_limit:
                user_data["hwidDeviceLimit"] = device_limit

            result = await remna.create_user(user_data)
            if not result:
                raise ValueError("❌ Ошибка при создании пользователя в Remnawave")

            client_id = result.get("uuid")
            remnawave_link = result.get("subscriptionUrl")

            if old_key_name:
                await session.execute(
                    update(Key).where(Key.tg_id == tg_id, Key.email == email).values(client_id=client_id)
                )

        if panel_type == "3x-ui":
            semaphore = asyncio.Semaphore(2)
            await create_client_on_server(
                server_info={
                    "api_url": server_info.api_url,
                    "inbound_id": server_info.inbound_id,
                    "server_name": server_info.server_name,
                    "panel_type": server_info.panel_type,
                },
                tg_id=tg_id,
                client_id=client_id,
                email=email,
                expiry_timestamp=expiry_timestamp,
                semaphore=semaphore,
                session=session,
                plan=tariff_id,
                is_trial=is_trial,
            )
            public_link = f"{PUBLIC_LINK}{email}/{tg_id}"

        logger.info(f"[Key Creation] Подписка создана для пользователя {tg_id} на сервере {selected_country}")

        if old_key_name:
            update_data = {"server_id": selected_country}
            if panel_type == "3x-ui":
                update_data["key"] = public_link
            elif panel_type == "remnawave":
                update_data["remnawave_link"] = remnawave_link
            await session.execute(update(Key).where(Key.tg_id == tg_id, Key.email == email).values(**update_data))
        else:
            new_key = Key(
                tg_id=tg_id,
                client_id=client_id,
                email=email,
                created_at=created_at,
                expiry_time=expiry_timestamp,
                key=public_link,
                remnawave_link=remnawave_link,
                server_id=selected_country,
                tariff_id=tariff_id,
            )
            session.add(new_key)

            if is_trial:
                trial_status = await get_trial(session, tg_id)
                if trial_status in [0, -1]:
                    await update_trial(session, tg_id, 1)

            if tariff_id:
                result = await session.execute(select(Tariff.price_rub).where(Tariff.id == tariff_id))
                row = result.scalar_one_or_none()
                if row:
                    await update_balance(session, tg_id, -row)

        await session.commit()

    except Exception as e:
        logger.error(f"[Key Finalize] Ошибка при создании ключа для пользователя {tg_id}: {e}")
        await callback_query.message.answer("❌ Произошла ошибка при создании подписки. Попробуйте снова.")
        return

    builder = InlineKeyboardBuilder()
    is_full_remnawave = await is_full_remnawave_cluster(cluster_info["cluster_name"], session)
    if (panel_type == "remnawave" or is_full_remnawave) and (public_link or remnawave_link):
        builder.row(
            InlineKeyboardButton(
                text=CONNECT_DEVICE,
                web_app=WebAppInfo(url=public_link or remnawave_link),
            )
        )
        builder.row(InlineKeyboardButton(text=TV_BUTTON, callback_data=f"connect_tv|{email}"))
    elif CONNECT_PHONE_BUTTON:
        builder.row(InlineKeyboardButton(text=CONNECT_PHONE, callback_data=f"connect_phone|{key_name}"))
        builder.row(
            InlineKeyboardButton(text=PC_BUTTON, callback_data=f"connect_pc|{email}"),
            InlineKeyboardButton(text=TV_BUTTON, callback_data=f"connect_tv|{email}"),
        )
    else:
        builder.row(InlineKeyboardButton(text=CONNECT_DEVICE, callback_data=f"connect_device|{key_name}"))

    builder.row(InlineKeyboardButton(text=MY_SUB, callback_data=f"view_key|{key_name}"))
    builder.row(InlineKeyboardButton(text=SUPPORT, url=SUPPORT_CHAT_URL))
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    link_to_show = public_link or remnawave_link or "Ссылка не найдена"

    tariff_info = None
    if tariff_id:
        result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
        tariff_info = result.scalar_one_or_none()

    tariff_duration = tariff_info["name"]
    subgroup_title = tariff_info.get("subgroup_title", "") if tariff_info else ""

    key_message_text = key_message_success(
        link_to_show,
        tariff_name=tariff_duration,
        traffic_limit=tariff_info.get("traffic_limit", 0) if tariff_info else 0,
        device_limit=tariff_info.get("device_limit", 0) if tariff_info else 0,
        subgroup_title=subgroup_title,
    )

    await edit_or_send_message(
        target_message=callback_query.message,
        text=key_message_text,
        reply_markup=builder.as_markup(),
        media_path="img/pic.jpg",
    )

    if state:
        await state.clear()


async def check_server_availability(server_info: dict, session: AsyncSession) -> bool:
    server_name = server_info.get("server_name", "unknown")
    panel_type = server_info.get("panel_type", "3x-ui").lower()
    enabled = server_info.get("enabled", True)
    max_keys = server_info.get("max_keys")

    if not enabled:
        logger.info(f"[Ping] Сервер {server_name} выключен (enabled = FALSE).")
        return False

    try:
        if max_keys is not None:
            result = await session.execute(select(func.count()).select_from(Key).where(Key.server_id == server_name))
            key_count = result.scalar()

            if key_count >= max_keys:
                logger.info(f"[Ping] Сервер {server_name} достиг лимита ключей: {key_count}/{max_keys}.")
                return False

    except SQLAlchemyError as e:
        logger.warning(f"[Ping] Ошибка при проверке лимита ключей на сервере {server_name}: {e}")
        return False

    try:
        if panel_type == "remnawave":
            remna = RemnawaveAPI(server_info["api_url"])
            await asyncio.wait_for(remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD), timeout=5.0)
            logger.info(f"[Ping] Remnawave сервер {server_name} доступен.")
            return True

        else:
            xui = AsyncApi(
                server_info["api_url"],
                username=ADMIN_USERNAME,
                password=ADMIN_PASSWORD,
                logger=logger,
            )
            await asyncio.wait_for(xui.login(), timeout=5.0)
            logger.info(f"[Ping] 3x-ui сервер {server_name} доступен.")
            return True

    except TimeoutError:
        logger.warning(f"[Ping] Сервер {server_name} не ответил вовремя.")
        return False
    except Exception as e:
        logger.warning(f"[Ping] Ошибка при проверке сервера {server_name}: {e}")
        return False

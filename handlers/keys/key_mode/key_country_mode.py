import asyncio
import uuid

from datetime import datetime
from typing import Any

import asyncpg
import pytz

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder
from py3xui import AsyncApi

from config import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    CONNECT_PHONE_BUTTON,
    DATABASE_URL,
    PUBLIC_LINK,
    REMNAWAVE_LOGIN,
    REMNAWAVE_PASSWORD,
    RENEWAL_PRICES,
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
from handlers.buttons import BACK, CONNECT_DEVICE, CONNECT_PHONE, MAIN_MENU, PC_BUTTON, SUPPORT, TV_BUTTON
from handlers.keys.key_utils import create_client_on_server
from handlers.texts import (
    SELECT_COUNTRY_MSG,
    key_message_success,
)
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
    session: Any,
    message_or_query: Message | CallbackQuery | None = None,
    old_key_name: str = None,
):
    target_message = message_or_query.message if isinstance(message_or_query, CallbackQuery) else message_or_query

    least_loaded_cluster = await get_least_loaded_cluster()
    servers = await session.fetch(
        "SELECT server_name, api_url, panel_type FROM servers WHERE cluster_name = $1",
        least_loaded_cluster,
    )

    if not servers:
        logger.error(f"Нет серверов в кластере {least_loaded_cluster}")
        error_message = "❌ Нет доступных серверов для создания ключа."
        await edit_or_send_message(
            target_message=target_message,
            text=error_message,
            reply_markup=None,
        )
        return

    available_servers = []
    tasks = [asyncio.create_task(check_server_availability(server)) for server in servers]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for server, result in zip(servers, results, strict=False):
        if result is True:
            available_servers.append(server["server_name"])

    if not available_servers:
        logger.error(f"Нет доступных серверов в кластере {least_loaded_cluster}")
        error_message = "❌ Нет доступных серверов для создания ключа."
        await edit_or_send_message(
            target_message=target_message,
            text=error_message,
            reply_markup=None,
        )
        return

    logger.info(f"[Country Selection] Доступные серверы для выбора: {available_servers}")

    builder = InlineKeyboardBuilder()
    ts = int(expiry_time.timestamp())

    for country in available_servers:
        if old_key_name:
            callback_data = f"select_country|{country}|{ts}|{old_key_name}"
        else:
            callback_data = f"select_country|{country}|{ts}"
        builder.row(InlineKeyboardButton(text=country, callback_data=callback_data))

    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    await edit_or_send_message(
        target_message=target_message,
        text=SELECT_COUNTRY_MSG,
        reply_markup=builder.as_markup(),
        media_path=None,
    )


@router.callback_query(F.data.startswith("change_location|"))
async def change_location_callback(callback_query: CallbackQuery, session: Any):
    try:
        data = callback_query.data.split("|")
        if len(data) < 2:
            await callback_query.answer("❌ Некорректные данные", show_alert=True)
            return

        old_key_name = data[1]
        record = await get_key_details(old_key_name, session)
        if not record:
            await callback_query.answer("❌ Ключ не найден", show_alert=True)
            return

        expiry_timestamp = record["expiry_time"]
        ts = int(expiry_timestamp / 1000)

        current_server = record["server_id"]

        cluster_info = await check_server_name_by_cluster(current_server, session)
        if not cluster_info:
            await callback_query.answer("❌ Кластер для текущего сервера не найден", show_alert=True)
            return

        cluster_name = cluster_info["cluster_name"]

        servers = await session.fetch(
            "SELECT server_name, api_url, panel_type, enabled, max_keys FROM servers WHERE cluster_name = $1 AND server_name != $2",
            cluster_name,
            current_server,
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
            task = asyncio.create_task(check_server_availability(server_info))
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

    await finalize_key_creation(tg_id, expiry_time, selected_country, state, session, callback_query, old_key_name)


async def finalize_key_creation(
    tg_id: int,
    expiry_time: datetime,
    selected_country: str,
    state: FSMContext | None,
    session: Any,
    callback_query: CallbackQuery,
    old_key_name: str = None,
):
    if not await check_user_exists(tg_id):
        if isinstance(callback_query, CallbackQuery):
            from_user = callback_query.from_user
        else:
            from_user = callback_query.from_user

        await add_user(
            tg_id=from_user.id,
            username=from_user.username,
            first_name=from_user.first_name,
            last_name=from_user.last_name,
            language_code=from_user.language_code,
            is_bot=from_user.is_bot,
            session=session,
        )
        logger.info(f"[User] Новый пользователь {tg_id} добавлен")

    expiry_time = expiry_time.astimezone(moscow_tz)

    if old_key_name:
        old_key_details = await get_key_details(old_key_name, session)
        if not old_key_details:
            await callback_query.message.answer("❌ Ключ не найден. Попробуйте снова.")
            return

        key_name = old_key_name
        client_id = old_key_details["client_id"]
        email = old_key_details["email"]
        expiry_timestamp = old_key_details["expiry_time"]
    else:
        while True:
            key_name = generate_random_email()
            existing_key = await get_key_details(key_name, session)
            if not existing_key:
                break
        client_id = str(uuid.uuid4())
        email = key_name.lower()
        expiry_timestamp = int(expiry_time.timestamp() * 1000)

    remna = None

    try:
        server_info = await session.fetchrow(
            "SELECT api_url, inbound_id, server_name, panel_type FROM servers WHERE server_name = $1",
            selected_country,
        )
        if not server_info:
            raise ValueError(f"Сервер {selected_country} не найден.")

        panel_type = server_info["panel_type"].lower()

        public_link = None
        remnawave_link = None
        created_at = int(datetime.now(moscow_tz).timestamp() * 1000)

        cluster_info = await check_server_name_by_cluster(selected_country, session)
        if not cluster_info:
            raise ValueError(f"Кластер для сервера {selected_country} не найден")

        is_full_remnawave = await is_full_remnawave_cluster(cluster_info["cluster_name"], session)

        if old_key_name:
            old_server_id = old_key_details.get("server_id")
            if old_server_id:
                old_server_info = await session.fetchrow(
                    "SELECT api_url, inbound_id, server_name, panel_type FROM servers WHERE server_name = $1",
                    old_server_id,
                )
                if old_server_info:
                    old_panel_type = old_server_info["panel_type"].lower()
                    try:
                        if old_panel_type == "3x-ui":
                            xui = await get_xui_instance(old_server_info["api_url"])

                            await delete_client(
                                xui,
                                old_server_info["inbound_id"],
                                email,
                                client_id,
                            )
                            await session.execute(
                                "UPDATE keys SET key = NULL WHERE tg_id = $1 AND email = $2",
                                tg_id,
                                email,
                            )
                            logger.info(f"[Delete] Удалён клиент {email} с 3x-ui сервера {old_server_id}")
                        elif old_panel_type == "remnawave":
                            remna = RemnawaveAPI(old_server_info["api_url"])
                            if await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
                                await remna.delete_user(client_id)
                                await session.execute(
                                    "UPDATE keys SET remnawave_link = NULL WHERE tg_id = $1 AND email = $2",
                                    tg_id,
                                    email,
                                )
                                logger.info(f"[Delete] Удалён клиент {client_id} с Remnawave сервера {old_server_id}")
                            else:
                                logger.warning(f"[Delete] Не удалось авторизоваться в Remnawave ({old_server_id})")
                    except Exception as e:
                        logger.warning(f"[Delete] Ошибка при удалении клиента с сервера {old_server_id}: {e}")

        if panel_type == "remnawave" or is_full_remnawave:
            remna = RemnawaveAPI(server_info["api_url"])
            if not await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
                raise ValueError(f"❌ Не удалось авторизоваться в Remnawave ({selected_country})")

            expire_at = datetime.utcfromtimestamp(expiry_timestamp / 1000).isoformat() + "Z"
            user_data = {
                "username": email,
                "trafficLimitStrategy": "NO_RESET",
                "expireAt": expire_at,
                "telegramId": tg_id,
                "activeUserInbounds": [server_info["inbound_id"]],
            }
            result = await remna.create_user(user_data)
            if not result:
                raise ValueError("❌ Ошибка при создании пользователя в Remnawave")

            client_id = result.get("uuid")
            remnawave_link = result.get("subscriptionUrl")
            logger.info(f"[Key Creation] Remnawave пользователь создан: {result}")

            if old_key_name:
                await session.execute(
                    "UPDATE keys SET client_id = $1 WHERE tg_id = $2 AND email = $3",
                    client_id,
                    tg_id,
                    email,
                )

        if panel_type == "3x-ui":
            semaphore = asyncio.Semaphore(2)
            await create_client_on_server(
                server_info=server_info,
                tg_id=tg_id,
                client_id=client_id,
                email=email,
                expiry_timestamp=expiry_timestamp,
                semaphore=semaphore,
            )
            public_link = f"{PUBLIC_LINK}{email}/{tg_id}"

        logger.info(f"[Key Creation] Подписка создана для пользователя {tg_id} на сервере {selected_country}")

        if old_key_name:
            await session.execute(
                "UPDATE keys SET server_id = $1 WHERE tg_id = $2 AND email = $3",
                selected_country,
                tg_id,
                old_key_name,
            )
            if panel_type == "3x-ui":
                await session.execute(
                    "UPDATE keys SET key = $1 WHERE tg_id = $2 AND email = $3",
                    public_link,
                    tg_id,
                    email,
                )
            elif panel_type == "remnawave":
                await session.execute(
                    "UPDATE keys SET remnawave_link = $1 WHERE tg_id = $2 AND email = $3",
                    remnawave_link,
                    tg_id,
                    email,
                )

        else:
            await session.execute(
                """
                INSERT INTO keys (tg_id, client_id, email, created_at, expiry_time, key, remnawave_link, server_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                tg_id,
                client_id,
                email,
                created_at,
                expiry_timestamp,
                public_link,
                remnawave_link,
                selected_country,
            )
            data = await state.get_data()
            if data.get("is_trial"):
                trial_status = await get_trial(tg_id, session)
                if trial_status in [0, -1]:
                    await update_trial(tg_id, 1, session)
            if data.get("plan_id"):
                plan_price = RENEWAL_PRICES.get(data["plan_id"])
                await update_balance(tg_id, -plan_price, session)

    except Exception as e:
        logger.error(f"[Key Finalize] Ошибка при создании ключа для пользователя {tg_id}: {e}")
        await callback_query.message.answer("❌ Произошла ошибка при создании подписки. Попробуйте снова.")
        return

    builder = InlineKeyboardBuilder()

    is_full_remnawave = await is_full_remnawave_cluster(cluster_info["cluster_name"], session)
    if is_full_remnawave and (public_link or remnawave_link):
        builder.row(
            InlineKeyboardButton(
                text=CONNECT_DEVICE,
                web_app=WebAppInfo(url=public_link or remnawave_link),
            )
        )
    elif CONNECT_PHONE_BUTTON:
        builder.row(InlineKeyboardButton(text=CONNECT_PHONE, callback_data=f"connect_phone|{key_name}"))
        builder.row(
            InlineKeyboardButton(text=PC_BUTTON, callback_data=f"connect_pc|{email}"),
            InlineKeyboardButton(text=TV_BUTTON, callback_data=f"connect_tv|{email}"),
        )
    else:
        builder.row(InlineKeyboardButton(text=CONNECT_DEVICE, callback_data=f"connect_device|{key_name}"))
    builder.row(InlineKeyboardButton(text=SUPPORT, url=SUPPORT_CHAT_URL))

    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    remaining_time = expiry_time - datetime.now(moscow_tz)
    days = remaining_time.days

    link_to_show = public_link or remnawave_link or "Ссылка не найдена"
    key_message_text = key_message_success(link_to_show, f"⏳ Осталось дней: {days} 📅")

    await edit_or_send_message(
        target_message=callback_query.message,
        text=key_message_text,
        reply_markup=builder.as_markup(),
        media_path="img/pic.jpg",
    )

    if state:
        await state.clear()


async def check_server_availability(server_info: dict, session: Any = None) -> bool:
    server_name = server_info.get("server_name", "unknown")
    panel_type = server_info.get("panel_type", "3x-ui").lower()
    enabled = server_info.get("enabled", True)
    max_keys = server_info.get("max_keys")

    if not enabled:
        logger.info(f"[Ping] Сервер {server_name} выключен (enabled = FALSE).")
        return False

    connection = None
    external_session = session is not None

    try:
        if not external_session:
            connection = await asyncpg.connect(DATABASE_URL)
            session = connection

        if max_keys is not None:
            count_query = "SELECT COUNT(*) FROM keys WHERE server_id = $1"
            key_count = await session.fetchval(count_query, server_name)
            if key_count >= max_keys:
                logger.info(f"[Ping] Сервер {server_name} достиг лимита ключей: {key_count}/{max_keys}.")
                return False

    except Exception as e:
        logger.warning(f"[Ping] Ошибка при проверке лимита ключей на сервере {server_name}: {e}")
        return False
    finally:
        if connection:
            await connection.close()

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

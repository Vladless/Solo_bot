import asyncio
import uuid

from datetime import datetime
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
    REMNAWAVE_LOGIN,
    REMNAWAVE_PASSWORD,
    REMNAWAVE_WEBAPP,
    REMNAWAVE_WEBAPP_OPEN_IN_BROWSER,
    SUPPORT_CHAT_URL,
)
from core.bootstrap import BUTTONS_CONFIG, MODES_CONFIG
from database import (
    add_user,
    check_server_name_by_cluster,
    check_user_exists,
    filter_cluster_by_subgroup,
    get_key_details,
    get_tariff_by_id,
    get_trial,
    update_balance,
    update_trial,
)
from database.models import Key, Server, ServerSpecialgroup
from handlers.buttons import (
    BACK,
    CONNECT_DEVICE,
    MAIN_MENU,
    MY_SUB,
    ROUTER_BUTTON,
    SUPPORT,
    TV_BUTTON,
)
from handlers.keys.operations import create_client_on_server
from handlers.keys.operations.aggregated_links import make_aggregated_link
from handlers.tariffs.tariff_display import (
    build_key_created_message,
    get_effective_limits_for_key,
)
from handlers.texts import SELECT_COUNTRY_MSG
from handlers.utils import (
    ALLOWED_GROUP_CODES,
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
from panels._3xui import delete_client, get_xui_instance
from panels.remnawave import RemnawaveAPI, get_vless_link_for_remnawave_by_username


router = Router()
moscow_tz = pytz.timezone("Europe/Moscow")
GB = 1024 * 1024 * 1024


async def key_country_mode(
    tg_id: int,
    expiry_time: datetime,
    state: FSMContext,
    session: AsyncSession,
    message_or_query: Message | CallbackQuery | None = None,
    old_key_name: str | None = None,
    plan: int | None = None,
    selected_device_limit: int | None = None,
    selected_traffic_gb: int | None = None,
    selected_price_rub: int | None = None,
):
    target_message = None
    safe_to_edit = False

    if state and plan:
        await state.update_data(tariff_id=plan)

    if state and any(value is not None for value in (selected_device_limit, selected_traffic_gb, selected_price_rub)):
        data = await state.get_data()
        if selected_device_limit is not None:
            data["config_selected_device_limit"] = selected_device_limit
        if selected_traffic_gb is not None:
            data["config_selected_traffic_gb"] = selected_traffic_gb
        if selected_price_rub is not None:
            data["config_selected_price_rub"] = selected_price_rub
        await state.set_data(data)

    if isinstance(message_or_query, CallbackQuery) and message_or_query.message:
        target_message = message_or_query.message
        safe_to_edit = True
    elif isinstance(message_or_query, Message):
        target_message = message_or_query
        safe_to_edit = True

    data = await state.get_data() if state else {}

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
            text = str(e)
            if safe_to_edit:
                await edit_or_send_message(target_message=target_message, text=text, reply_markup=None)
            else:
                await bot.send_message(chat_id=tg_id, text=text)
            return

    subgroup_title = None
    tariff: dict[str, Any] | None = None
    if plan:
        tariff = await get_tariff_by_id(session, int(plan))
        if tariff:
            subgroup_title = tariff.get("subgroup_title")

    q = select(
        Server.id,
        Server.server_name,
        Server.api_url,
        Server.panel_type,
        Server.enabled,
        Server.max_keys,
    ).where(Server.cluster_name == least_loaded_cluster)
    servers = [dict(m) for m in (await session.execute(q)).mappings().all()]

    if not servers:
        text = "❌ Нет доступных серверов в выбранном кластере."
        if safe_to_edit:
            await edit_or_send_message(target_message=target_message, text=text, reply_markup=None)
        else:
            await bot.send_message(chat_id=tg_id, text=text)
        return

    server_ids = [s["id"] for s in servers]
    groups_map: dict[int, list[str]] = {}
    if server_ids:
        r = await session.execute(
            select(ServerSpecialgroup.server_id, ServerSpecialgroup.group_code).where(
                ServerSpecialgroup.server_id.in_(server_ids)
            )
        )
        for sid, gc in r.all():
            groups_map.setdefault(sid, []).append(gc)

    for server in servers:
        server["special_groups"] = [g for g in groups_map.get(server["id"], []) if g in ALLOWED_GROUP_CODES]

    if subgroup_title:
        servers = await filter_cluster_by_subgroup(
            session, servers, subgroup_title, least_loaded_cluster, tariff_id=plan
        )
        if not servers:
            text = "❌ Нет доступных серверов в выбранном кластере."
            if safe_to_edit:
                await edit_or_send_message(target_message=target_message, text=text, reply_markup=None)
            else:
                await bot.send_message(chat_id=tg_id, text=text)
            return

    special = None
    if tariff:
        gc = (tariff.get("group_code") or "").lower()
        if gc in ALLOWED_GROUP_CODES:
            special = gc

    if special:
        bound_servers = [s for s in servers if special in (s.get("special_groups") or [])]
        if bound_servers:
            servers = bound_servers
        else:
            text = f"❌ Нет доступных серверов для тарифа с группой '{special}'."
            if safe_to_edit:
                await edit_or_send_message(target_message=target_message, text=text, reply_markup=None)
            else:
                await bot.send_message(chat_id=tg_id, text=text)
            return

    available_servers: list[str] = []
    tasks = [asyncio.create_task(check_server_availability(dict(server), session)) for server in servers]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for server, result_ok in zip(servers, results, strict=False):
        if result_ok is True:
            available_servers.append(server["server_name"])

    if not available_servers:
        text = "❌ Нет доступных серверов в выбранном кластере."
        if safe_to_edit:
            await edit_or_send_message(target_message=target_message, text=text, reply_markup=None)
        else:
            await bot.send_message(chat_id=tg_id, text=text)
        return

    builder = InlineKeyboardBuilder()
    ts = int(expiry_time.timestamp())

    for i in range(0, len(available_servers), 2):
        row_buttons = []
        for server_name in available_servers[i : i + 2]:
            if old_key_name:
                callback_data = f"select_country|{server_name}|{ts}|{old_key_name}"
            else:
                if plan:
                    callback_data = f"select_country|{server_name}|{ts}||{plan}"
                else:
                    callback_data = f"select_country|{server_name}|{ts}"
            row_buttons.append(InlineKeyboardButton(text=server_name, callback_data=callback_data))
        builder.row(*row_buttons)

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

        key_tariff_id = record.get("tariff_id")
        tariff_dict: dict[str, Any] | None = None
        subgroup_title = None
        if key_tariff_id:
            tariff_dict = await get_tariff_by_id(session, int(key_tariff_id))
            if tariff_dict:
                subgroup_title = tariff_dict.get("subgroup_title")

        q = (
            select(
                Server.id,
                Server.server_name,
                Server.api_url,
                Server.panel_type,
                Server.enabled,
                Server.max_keys,
            )
            .where(Server.cluster_name == cluster_name)
            .where(Server.server_name != current_server)
        )
        servers = [dict(m) for m in (await session.execute(q)).mappings().all()]
        if not servers:
            await callback_query.answer("❌ Доступных серверов в кластере не найдено", show_alert=True)
            return

        server_ids = [s["id"] for s in servers]
        groups_map: dict[int, list[str]] = {}
        if server_ids:
            r = await session.execute(
                select(ServerSpecialgroup.server_id, ServerSpecialgroup.group_code).where(
                    ServerSpecialgroup.server_id.in_(server_ids)
                )
            )
            for sid, gc in r.all():
                groups_map.setdefault(sid, []).append(gc)

        for server in servers:
            server["special_groups"] = [g for g in groups_map.get(server["id"], []) if g in ALLOWED_GROUP_CODES]

        available_servers: list[str] = []
        tasks = [
            asyncio.create_task(
                check_server_availability(
                    {
                        "server_name": s["server_name"],
                        "api_url": s["api_url"],
                        "panel_type": s["panel_type"],
                        "enabled": s.get("enabled", True),
                        "max_keys": s.get("max_keys"),
                    },
                    session,
                )
            )
            for s in servers
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for server, result_ok in zip(servers, results, strict=False):
            if result_ok is True:
                available_servers.append(server["server_name"])

        if subgroup_title and available_servers:
            available_servers_dict = [s for s in servers if s["server_name"] in available_servers]
            filtered_servers = await filter_cluster_by_subgroup(
                session,
                available_servers_dict,
                subgroup_title.strip(),
                cluster_name,
                tariff_id=key_tariff_id,
            )
            if filtered_servers:
                available_servers = [s["server_name"] for s in filtered_servers]
            else:
                builder = InlineKeyboardBuilder()
                builder.row(InlineKeyboardButton(text=BACK, callback_data=f"view_key|{old_key_name}"))
                await edit_or_send_message(
                    target_message=callback_query.message,
                    text="❌ Нет доступных стран для смены локации.",
                    reply_markup=builder.as_markup(),
                )
                return

        if available_servers and tariff_dict:
            special = None
            gc = (tariff_dict.get("group_code") or "").lower()
            if gc and gc in ALLOWED_GROUP_CODES:
                special = gc

            if special:
                available_servers_dict = [s for s in servers if s["server_name"] in available_servers]
                bound_servers = [s for s in available_servers_dict if special in (s.get("special_groups") or [])]
                if bound_servers:
                    available_servers = [s["server_name"] for s in bound_servers]
                else:
                    builder = InlineKeyboardBuilder()
                    builder.row(InlineKeyboardButton(text=BACK, callback_data=f"view_key|{old_key_name}"))
                    await edit_or_send_message(
                        target_message=callback_query.message,
                        text="❌ Нет доступных стран для смены локации.",
                        reply_markup=builder.as_markup(),
                    )
                    return

        if not available_servers:
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text=BACK, callback_data=f"view_key|{old_key_name}"))
            await edit_or_send_message(
                target_message=callback_query.message,
                text="❌ Нет доступных стран для смены локации.",
                reply_markup=builder.as_markup(),
            )
            return

        builder = InlineKeyboardBuilder()

        for i in range(0, len(available_servers), 2):
            row_buttons = []
            for country in available_servers[i : i + 2]:
                callback_data = f"select_country|{country}|{ts}|{old_key_name}"
                row_buttons.append(InlineKeyboardButton(text=country, callback_data=callback_data))
            builder.row(*row_buttons)

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

    old_key_name = data[3] if len(data) > 3 and data[3] else None
    tariff_id = int(data[4]) if len(data) > 4 and data[4] else None

    tg_id = callback_query.from_user.id

    fsm_data = await state.get_data()
    if fsm_data.get("creating_key"):
        try:
            await callback_query.answer("⏳ Уже обрабатываю…")
        except Exception:
            pass
        return

    await state.update_data(creating_key=True)

    try:
        await callback_query.answer("Обрабатываю…")
        if callback_query.message:
            await callback_query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    try:
        expiry_time = datetime.fromtimestamp(ts, tz=moscow_tz)
        await finalize_key_creation(
            tg_id=tg_id,
            expiry_time=expiry_time,
            selected_country=selected_country,
            state=state,
            session=session,
            callback_query=callback_query,
            old_key_name=old_key_name,
            tariff_id=tariff_id,
        )
    finally:
        fsm_data = await state.get_data()
        if fsm_data.get("creating_key"):
            await state.update_data(creating_key=False)


async def finalize_key_creation(
    tg_id: int,
    expiry_time: datetime,
    selected_country: str,
    state: FSMContext | None,
    session: AsyncSession,
    callback_query: CallbackQuery,
    old_key_name: str | None = None,
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

    expiry_time = expiry_time.astimezone(moscow_tz)

    old_key_details: dict[str, Any] | None = None
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
            key_name = await generate_random_email(session=session)
            existing_key = await get_key_details(session, key_name)
            if not existing_key:
                break
        client_id = str(uuid.uuid4())
        email = key_name.lower()
        expiry_timestamp = int(expiry_time.timestamp() * 1000)

    data = await state.get_data() if state else {}
    is_trial = data.get("is_trial", False)

    selected_traffic_gb = data.get("config_selected_traffic_gb")
    if selected_traffic_gb is None:
        selected_traffic_gb = data.get("selected_traffic_limit_gb")

    selected_device_limit = data.get("config_selected_device_limit")
    if selected_device_limit is None:
        selected_device_limit = data.get("selected_device_limit")

    if old_key_details:
        if selected_traffic_gb is None:
            stored_traffic = old_key_details.get("selected_traffic_limit")
            if stored_traffic is not None:
                selected_traffic_gb = int(stored_traffic)
        if selected_device_limit is None:
            stored_devices = old_key_details.get("selected_device_limit")
            if stored_devices is not None:
                selected_device_limit = int(stored_devices)

    price_to_charge = data.get("selected_price_rub")

    effective_tariff_id = data.get("tariff_id") or tariff_id
    tariff: dict[str, Any] | None = None
    if effective_tariff_id:
        tariff_id = int(effective_tariff_id)
        tariff = await get_tariff_by_id(session, tariff_id)

    device_limit, traffic_limit_bytes = await get_effective_limits_for_key(
        session=session,
        tariff_id=tariff_id,
        selected_device_limit=selected_device_limit,
        selected_traffic_gb=selected_traffic_gb,
    )

    if selected_traffic_gb is not None:
        traffic_limit_gb = int(selected_traffic_gb)
    else:
        traffic_limit_gb = int(traffic_limit_bytes / GB) if traffic_limit_bytes else 0

    if price_to_charge is None and tariff and not old_key_name:
        price_to_charge = tariff.get("price_rub")

    need_vless_key = bool(tariff.get("vless")) if tariff else False

    public_link = None
    remnawave_link = None
    created_at = int(datetime.now(moscow_tz).timestamp() * 1000)

    try:
        result = await session.execute(select(Server).where(Server.server_name == selected_country))
        server_info = result.scalar_one_or_none()
        if not server_info:
            raise ValueError(f"Сервер {selected_country} не найден")

        cluster_info = await check_server_name_by_cluster(session, server_info.server_name)
        if not cluster_info:
            raise ValueError(f"Кластер для сервера {server_info.server_name} не найден")

        cluster_name = cluster_info["cluster_name"]
        is_full_remnawave = await is_full_remnawave_cluster(cluster_name, session)

        if old_key_name and old_key_details:
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
                            remna_del = RemnawaveAPI(old_server_info.api_url)
                            if await remna_del.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
                                await remna_del.delete_user(client_id)
                                await session.execute(
                                    update(Key)
                                    .where(Key.tg_id == tg_id, Key.email == email)
                                    .values(remnawave_link=None)
                                )
                    except Exception as e:
                        logger.warning(f"[Delete] Ошибка при удалении клиента: {e}")

        panel_type = server_info.panel_type.lower()

        if panel_type == "remnawave" or is_full_remnawave:
            remna = RemnawaveAPI(server_info.api_url)
            if not await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
                raise ValueError(f"❌ Не удалось авторизоваться в Remnawave ({server_info.server_name})")

            expire_at = datetime.utcfromtimestamp(expiry_timestamp / 1000).isoformat() + "Z"
            user_data: dict[str, Any] = {
                "username": email,
                "trafficLimitStrategy": "NO_RESET",
                "expireAt": expire_at,
                "telegramId": tg_id,
                "activeInternalSquads": [server_info.inbound_id],
                "uuid": client_id,
            }
            if traffic_limit_bytes:
                user_data["trafficLimitBytes"] = traffic_limit_bytes
            if device_limit:
                user_data["hwidDeviceLimit"] = device_limit

            result = await remna.create_user(user_data)
            if not result:
                raise ValueError("❌ Ошибка при создании пользователя в Remnawave")

            client_id = result.get("uuid") or result.get("id") or client_id

            remnawave_link = None
            if need_vless_key:
                try:
                    vless_link = await get_vless_link_for_remnawave_by_username(remna, email, email)
                except Exception:
                    vless_link = None
                if vless_link:
                    remnawave_link = vless_link

            if not remnawave_link:
                try:
                    sub = await remna.get_subscription_by_username(email)
                except Exception:
                    sub = None

                if sub:
                    if need_vless_key and not remnawave_link:
                        links = sub.get("links") or []
                        remnawave_link = next(
                            (l for l in links if isinstance(l, str) and l.lower().startswith("vless://")),
                            None,
                        )

                    if not remnawave_link:
                        remnawave_link = sub.get("subscriptionUrl")

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
                total_traffic_limit_bytes=traffic_limit_bytes,
                device_limit_value=device_limit,
            )

        subgroup_code = tariff.get("subgroup_title") if tariff and tariff.get("subgroup_title") else None
        cluster_all = [
            {
                "server_name": server_info.server_name,
                "api_url": server_info.api_url,
                "panel_type": server_info.panel_type,
                "inbound_id": getattr(server_info, "inbound_id", None),
                "enabled": True,
                "max_keys": getattr(server_info, "max_keys", None),
            }
        ]

        link_to_show = await make_aggregated_link(
            session=session,
            cluster_all=cluster_all,
            cluster_id=cluster_name,
            email=email,
            client_id=client_id,
            tg_id=tg_id,
            subgroup_code=subgroup_code,
            remna_link_override=remnawave_link,
            plan=tariff_id,
        )

        public_link = link_to_show

        if old_key_name:
            update_data: dict[str, Any] = {
                "server_id": selected_country,
                "key": None,
                "remnawave_link": None,
            }
            if public_link and public_link.startswith("vless://"):
                update_data["key"] = public_link
            elif public_link and public_link.startswith("http"):
                update_data["key"] = public_link
            if remnawave_link:
                update_data["remnawave_link"] = remnawave_link
            await session.execute(update(Key).where(Key.tg_id == tg_id, Key.email == email).values(**update_data))
        else:
            new_key = Key(
                tg_id=tg_id,
                client_id=client_id,
                email=email,
                created_at=created_at,
                expiry_time=expiry_timestamp,
                key=public_link if public_link else None,
                remnawave_link=remnawave_link,
                server_id=selected_country,
                tariff_id=tariff_id,
                selected_device_limit=int(selected_device_limit) if selected_device_limit is not None else None,
                selected_traffic_limit=int(selected_traffic_gb) if selected_traffic_gb is not None else None,
                selected_price_rub=int(price_to_charge) if price_to_charge is not None else None,
            )
            session.add(new_key)
            if is_trial:
                trial_status = await get_trial(session, tg_id)
                if trial_status in [0, -1]:
                    await update_trial(session, tg_id, 1)
            if not is_trial and price_to_charge:
                await update_balance(session, tg_id, -int(price_to_charge))

        await session.commit()

    except Exception as e:
        logger.error(f"[Key Finalize] Ошибка при создании ключа для пользователя {tg_id}: {e}")
        await callback_query.message.answer("❌ Произошла ошибка при создании подписки. Попробуйте снова.")
        return

    builder = InlineKeyboardBuilder()
    is_full_remnawave = await is_full_remnawave_cluster(cluster_name, session)
    is_vless = bool(public_link and public_link.lower().startswith("vless://")) or bool(need_vless_key)
    final_link = public_link or remnawave_link
    webapp_url = (
        final_link
        if isinstance(final_link, str) and final_link.strip().lower().startswith(("http://", "https://"))
        else None
    )

    use_webapp = bool(MODES_CONFIG.get("REMNAWAVE_WEBAPP_ENABLED", REMNAWAVE_WEBAPP))
    open_in_browser = bool(MODES_CONFIG.get("REMNAWAVE_WEBAPP_OPEN_IN_BROWSER", REMNAWAVE_WEBAPP_OPEN_IN_BROWSER))
    if use_webapp and webapp_url:
        use_webapp = await process_remnawave_webapp_override(
            remnawave_webapp=use_webapp,
            final_link=final_link,
            session=session,
        )

    tv_button_enabled = bool(BUTTONS_CONFIG.get("ANDROID_TV_BUTTON_ENABLE"))

    if panel_type == "remnawave" or is_full_remnawave:
        if is_vless:
            builder.row(InlineKeyboardButton(text=ROUTER_BUTTON, callback_data=f"connect_router|{key_name}"))
        else:
            if use_webapp and webapp_url:
                if open_in_browser:
                    builder.row(InlineKeyboardButton(text=CONNECT_DEVICE, url=webapp_url))
                else:
                    builder.row(InlineKeyboardButton(text=CONNECT_DEVICE, web_app=WebAppInfo(url=webapp_url)))
                if tv_button_enabled:
                    builder.row(InlineKeyboardButton(text=TV_BUTTON, callback_data=f"connect_tv|{email}"))
            else:
                builder.row(InlineKeyboardButton(text=CONNECT_DEVICE, callback_data=f"connect_device|{key_name}"))
    else:
        builder.row(InlineKeyboardButton(text=CONNECT_DEVICE, callback_data=f"connect_device|{key_name}"))

    builder.row(InlineKeyboardButton(text=MY_SUB, callback_data=f"view_key|{key_name}"))
    builder.row(InlineKeyboardButton(text=SUPPORT, url=SUPPORT_CHAT_URL))
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    if await process_intercept_key_creation_message(
        chat_id=tg_id,
        session=session,
        target_message=callback_query,
    ):
        return

    hook_commands = await process_key_creation_complete(
        chat_id=tg_id,
        admin=False,
        session=session,
        email=email,
        key_name=key_name,
    )
    if hook_commands:
        builder = insert_hook_buttons(builder, hook_commands)

    key_record = await get_key_details(session, key_name)
    final_link_for_message = final_link or (key_record.get("link") if key_record else None) or "Ссылка не найдена"
    message_text = await build_key_created_message(
        session=session,
        key_record=key_record,
        final_link=final_link_for_message,
        selected_device_limit=selected_device_limit,
        selected_traffic_gb=selected_traffic_gb,
    )

    await edit_or_send_message(
        target_message=callback_query.message,
        text=message_text,
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

from aiogram.fsm.storage.memory import MemoryStorage

from core.rate_limit import check_and_increment
from core.redis_cache import cache_incr_checked
from database.gifts import get_gift_usage
from database.servers import get_servers
from services.cluster_access import resolve_tariff_servers
from services.errors import ServiceError

from ._common import *
from ._common import router
from .finalize import check_server_availability, finalize_key_creation


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
    skip_balance_charge: bool | None = None,
):
    target_message = None
    safe_to_edit = False

    if state and plan:
        await state.update_data(tariff_id=plan)

    if state and (
        skip_balance_charge is not None
        or any(value is not None for value in (selected_device_limit, selected_traffic_gb, selected_price_rub))
    ):
        data = await state.get_data()
        if selected_device_limit is not None:
            data["config_selected_device_limit"] = selected_device_limit
        if selected_traffic_gb is not None:
            data["config_selected_traffic_gb"] = selected_traffic_gb
        if selected_price_rub is not None:
            data["config_selected_price_rub"] = selected_price_rub
        if skip_balance_charge is not None:
            data["skip_balance_charge"] = skip_balance_charge
        await state.set_data(data)

    if isinstance(message_or_query, CallbackQuery) and message_or_query.message:
        target_message = message_or_query.message
        safe_to_edit = True
    elif isinstance(message_or_query, Message):
        target_message = message_or_query
        safe_to_edit = True

    tg_notify = await notify_telegram_chat_id(session, tg_id)

    data = await state.get_data() if state else {}
    if state:
        await state.update_data(country_purchase=None)

    forced_cluster = await process_cluster_override(
        tg_id=tg_notify,
        state_data=data,
        session=session,
        plan=plan,
    )
    if forced_cluster:
        least_loaded_cluster = forced_cluster
    else:
        try:
            least_loaded_cluster = await get_least_loaded_cluster(
                session, tariff_id=plan or data.get("tariff_id"), is_trial=bool(data.get("is_trial"))
            )
        except (ValueError, ServiceError) as e:
            text = str(e)
            if safe_to_edit:
                await edit_or_send_message(target_message=target_message, text=text, reply_markup=None)
            elif tg_notify is not None:
                await bot.send_message(chat_id=tg_notify, text=text)
            return

    tariff: dict[str, Any] | None = None
    plan = plan or data.get("tariff_id")
    if plan:
        tariff = await get_tariff_by_id(session, int(plan))

    grouped_servers = await get_servers(session, include_enabled=True)
    binding_scope = [server for cluster_servers in grouped_servers.values() for server in cluster_servers]
    servers = resolve_tariff_servers(
        grouped_servers.get(least_loaded_cluster, []),
        tariff,
        binding_scope=binding_scope,
        is_trial=bool(data.get("is_trial")),
    )

    if not servers:
        text = "❌ Нет доступных серверов в выбранном кластере."
        if safe_to_edit:
            await edit_or_send_message(target_message=target_message, text=text, reply_markup=None)
        elif tg_notify is not None:
            await bot.send_message(chat_id=tg_notify, text=text)
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
        elif tg_notify is not None:
            await bot.send_message(chat_id=tg_notify, text=text)
        return

    builder = InlineKeyboardBuilder()
    ts = int(expiry_time.timestamp())
    request_id = None
    if not old_key_name:
        if not state or not plan:
            return
        request_id = uuid.uuid4().hex[:10]
        checkout = {
            name: data.get(name)
            for name in (
                "is_trial",
                "config_selected_device_limit",
                "selected_device_limit",
                "config_selected_traffic_gb",
                "selected_traffic_limit_gb",
                "config_selected_price_rub",
                "selected_price_rub",
            )
        }
        checkout["tariff_id"] = int(plan)
        checkout["skip_balance_charge"] = bool(skip_balance_charge)
        checkout["selected_price_rub"] = (
            selected_price_rub
            if selected_price_rub is not None
            else data.get("selected_price_rub")
            if data.get("selected_price_rub") is not None
            else tariff.get("price_rub")
        )
        await state.update_data(
            country_purchase={
                "request_id": request_id,
                "telegram_id": tg_notify,
                "expiry_time": ts,
                "valid_until": None
                if checkout["skip_balance_charge"]
                else int(datetime.now(moscow_tz).timestamp()) + 900,
                "tariff_id": int(plan),
                "servers": available_servers,
                "checkout": checkout,
                "gift_receipt": data.get("country_gift_receipt") if checkout["skip_balance_charge"] else None,
            }
        )
        await state.update_data(country_gift_receipt=None)

    for i in range(0, len(available_servers), 2):
        row_buttons = []
        for server_name in available_servers[i : i + 2]:
            if old_key_name:
                callback_data = f"select_country|{server_name}|{ts}|{old_key_name}"
            else:
                if plan:
                    callback_data = f"select_country|{server_name}|{request_id}||{plan}"
                else:
                    callback_data = f"select_country|{server_name}|{request_id}"
            row_buttons.append(InlineKeyboardButton(text=server_name, callback_data=callback_data))
        builder.row(*row_buttons)

    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    if safe_to_edit:
        await edit_or_send_message(
            target_message=target_message,
            text=SELECT_COUNTRY_MSG,
            reply_markup=builder.as_markup(),
        )
    elif tg_notify is not None:
        await bot.send_message(
            chat_id=tg_notify,
            text=SELECT_COUNTRY_MSG,
            reply_markup=builder.as_markup(),
        )


@router.callback_query(F.data.startswith("select_country|"))
async def handle_country_selection(callback_query: CallbackQuery, session: Any, state: FSMContext):
    data = callback_query.data.split("|")
    if len(data) < 3:
        await callback_query.message.answer("❌ Некорректные данные. Попробуйте снова.")
        return

    selected_country = data[1]
    old_key_name = data[3] if len(data) > 3 and data[3] else None
    try:
        tariff_id = int(data[4]) if len(data) > 4 and data[4] else None
    except (ValueError, IndexError):
        await callback_query.message.answer("❌ Некорректные данные. Попробуйте снова.")
        return

    tg_id = TelegramId(callback_query.from_user.id)

    fsm_data = await state.get_data()
    purchase = fsm_data.get("country_purchase")
    if not old_key_name:
        if (
            not isinstance(purchase, dict)
            or purchase.get("request_id") != data[2]
            or purchase.get("telegram_id") != callback_query.from_user.id
            or purchase.get("tariff_id") != tariff_id
            or selected_country not in purchase.get("servers", [])
            or (
                purchase.get("valid_until") is not None
                and purchase["valid_until"] < int(datetime.now(moscow_tz).timestamp())
            )
        ):
            await callback_query.message.answer("❌ Некорректные данные. Попробуйте снова.")
            return
    if fsm_data.get("creating_key"):
        try:
            await callback_query.answer("⏳ Уже обрабатываю…")
        except Exception:
            pass
        return

    if old_key_name:
        expiry_time = datetime.now(moscow_tz)
        await state.update_data(creating_key=True)
    else:
        receipt = purchase.get("gift_receipt")
        if receipt:
            owner = await resolve_user_optional(session, tg_id)
            usage = (
                await get_gift_usage(session, receipt["gift_id"], owner.id)
                if owner is not None and owner.id == receipt.get("user_id")
                else None
            )
            if (
                usage is None
                or usage.used_at is None
                or usage.used_at.isoformat(timespec="microseconds") != receipt.get("used_at")
            ):
                await callback_query.message.answer("❌ Некорректные данные. Попробуйте снова.")
                return
        claim_key = f"country_purchase:{int(tg_id)}:{data[2]}"
        if isinstance(state.storage, MemoryStorage):
            already_used = check_and_increment(claim_key, 1, 900) > 1
        else:
            count, redis_ok = await cache_incr_checked(claim_key, 900)
            already_used = not redis_ok or count > 1
        if already_used:
            await callback_query.message.answer("❌ Некорректные данные. Попробуйте снова.")
            return
        expiry_time = datetime.fromtimestamp(purchase["expiry_time"], tz=moscow_tz)
        await state.update_data(**purchase["checkout"], creating_key=True, country_purchase=None)

    try:
        await callback_query.answer("Обрабатываю…")
        if callback_query.message:
            await callback_query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    try:
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

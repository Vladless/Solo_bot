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
    try:
        ts = int(data[2])
    except ValueError:
        await callback_query.message.answer("❌ Некорректное время истечения. Попробуйте снова.")
        return

    old_key_name = data[3] if len(data) > 3 and data[3] else None
    try:
        tariff_id = int(data[4]) if len(data) > 4 and data[4] else None
    except (ValueError, IndexError):
        tariff_id = None

    tg_id = TelegramId(callback_query.from_user.id)

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

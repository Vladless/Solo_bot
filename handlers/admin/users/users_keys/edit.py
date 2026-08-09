from database.subscription_events import get_recent_renewals
from services.subscription_keys import resolve_remnawave_server_ref

from ...panel.headers import card, menu_text, quote, section
from ._common import *  # noqa: F401,F403


_RENEWAL_SOURCE_LABELS = {"bot": "бот", "web": "сайт", "webapp": "WebApp", "admin": "админ", "balance": "баланс"}


async def _build_renewals_block(session, client_id) -> str:
    renewals = await get_recent_renewals(session, client_id, limit=5)
    if not renewals:
        return ""
    tariff_names: dict[int, str] = {}
    lines = []
    for ev in renewals:
        when = (
            ev["created_at"].replace(tzinfo=timezone.utc).astimezone(MOSCOW_TZ).strftime("%d.%m.%Y %H:%M")
            if ev.get("created_at")
            else "—"
        )
        parts = [when]
        tid = ev.get("tariff_id")
        if tid is not None:
            if tid not in tariff_names:
                t = await get_tariff_by_id(session, tid)
                tariff_names[tid] = (t.get("name") if t else None) or f"#{tid}"
            parts.append(tariff_names[tid])
        if ev.get("duration_days"):
            parts.append(f"+{ev['duration_days']} дн.")
        if ev.get("expiry_time"):
            parts.append(
                "до " + datetime.fromtimestamp(int(ev["expiry_time"]) / 1000, tz=MOSCOW_TZ).strftime("%d.%m.%Y")
            )
        src = ev.get("source") or ""
        parts.append(_RENEWAL_SOURCE_LABELS.get(src, src) if src else "—")
        lines.append(" · ".join(parts))
    return "\n".join(lines)


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_key_edit"),
    IsAdminFilter(),
)
async def handle_key_edit(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback | AdminUserKeyEditorCallback,
    session: AsyncSession,
    update: bool = False,
):
    key_ref = callback_data.data
    key_obj = await resolve_callback_key(session, callback_data.tg_id, key_ref)

    if not key_obj:
        await callback_query.message.edit_text(
            text=menu_text("Подписка", "❌ Подписка не найдена.", markup=build_editor_kb(callback_data.tg_id)),
            reply_markup=build_editor_kb(callback_data.tg_id),
        )
        return

    email = key_obj.email
    key_details = await get_key_details(session, email)
    is_frozen = bool(key_details.get("is_frozen")) if key_details else bool(getattr(key_obj, "is_frozen", False))

    key_value = key_obj.key or key_obj.remnawave_link or "—"

    if key_obj.created_at:
        created_at_dt = datetime.fromtimestamp(int(key_obj.created_at) / 1000, tz=MOSCOW_TZ)
        created_at = created_at_dt.strftime("%d.%m.%y")
    else:
        created_at = "—"

    if is_frozen:
        frozen_left_ms = int((key_details or {}).get("expiry_time") or 0)
        total_minutes = max(frozen_left_ms // 60000, 0)
        days, rem_minutes = divmod(total_minutes, 24 * 60)
        hours, minutes = divmod(rem_minutes, 60)
        frozen_parts: list[str] = []
        if days:
            frozen_parts.append(f"{days} дн.")
        if hours:
            frozen_parts.append(f"{hours} ч.")
        if minutes or not frozen_parts:
            frozen_parts.append(f"{minutes} мин.")
        expiry_label = "Осталось:"
        expiry_date = " ".join(frozen_parts)
    elif key_obj.expiry_time:
        expiry_dt = datetime.fromtimestamp(int(key_obj.expiry_time) / 1000, tz=MOSCOW_TZ)
        expiry_label = "Истекает:"
        expiry_date = expiry_dt.strftime("%d.%m.%y %H:%M")
    else:
        expiry_label = "Истекает:"
        expiry_date = "—"

    tariff_name = "—"
    subgroup_title = "—"
    group_code = "—"
    base_devices = None
    base_traffic = None
    is_configurable = False
    if key_obj.tariff_id:
        tariff = await get_tariff_by_id(session, key_obj.tariff_id)
        if tariff:
            tariff_name = tariff.get("name", "—")
            subgroup_title = tariff.get("subgroup_title") or "—"
            group_code = tariff.get("group_code") or "—"
            base_devices = tariff.get("device_limit")
            base_traffic = tariff.get("traffic_limit")
            is_configurable = bool(tariff.get("configurable"))

    devices_line = ""
    traffic_line = ""
    if is_configurable:
        sel_dev, cur_dev = key_obj.selected_device_limit, key_obj.current_device_limit
        if sel_dev is not None or cur_dev is not None:
            base_dev = sel_dev if sel_dev is not None else (base_devices if base_devices is not None else cur_dev)
            extra = (
                f" + {cur_dev - base_dev} (докуплено)"
                if (base_dev is not None and cur_dev is not None and cur_dev > base_dev)
                else ""
            )
            devices_line = f"Устройства: {base_dev}{extra}"

        sel_traf, cur_traf = key_obj.selected_traffic_limit, key_obj.current_traffic_limit
        if sel_traf is not None or cur_traf is not None:
            base_traf = sel_traf if sel_traf is not None else (base_traffic if base_traffic is not None else cur_traf)
            extra = (
                f" + {cur_traf - base_traf} ГБ (докуплено)"
                if (base_traf is not None and cur_traf is not None and cur_traf > base_traf)
                else ""
            )
            traffic_line = f"Трафик: {base_traf} ГБ{extra}"

    renewals_block = await _build_renewals_block(session, key_obj.client_id)

    term = [f"Создан: {created_at}", f"{expiry_label} {expiry_date}"]
    if is_frozen:
        term.insert(0, "⛔ Отключена")

    plan = [f"Тариф: {tariff_name}", f"Группа: {group_code}"]
    if subgroup_title != "—":
        plan.append(f"Подгруппа: {subgroup_title}")
    plan += [line for line in (devices_line, traffic_line) if line]

    blocks = [
        section("🔗 Ссылка", f"<code>{key_value}</code>"),
        section("⏳ Срок", *term),
        section("📦 Тариф", *plan),
        section("🗺 Размещение", f"Кластер: {key_obj.server_id or '—'}", f"Клиент: {key_obj.tg_id or '—'}"),
    ]
    if renewals_block:
        blocks.append(section("🔄 Продления", *renewals_block.split("\n")))

    text = menu_text("Подписка", key_obj.alias or email, card(*blocks))

    if not update or not getattr(callback_data, "edit", False):
        kb_key_details = dict(key_obj.__dict__)
        kb_key_details["is_frozen"] = is_frozen
        show_subscription_keys = bool(await resolve_remnawave_server_ref(session, key_obj.server_id or ""))
        kb_markup = build_key_edit_kb(
            kb_key_details,
            email,
            is_configurable=is_configurable,
            key_ref=str(key_ref),
            show_subscription_keys=show_subscription_keys,
        )
        kb_builder = InlineKeyboardBuilder.from_markup(kb_markup)
        hook_buttons = await process_admin_key_edit_menu(
            email=email,
            session=session,
            client_id=key_obj.client_id,
            tg_id=key_obj.tg_id,
        )
        kb_builder = insert_hook_buttons(kb_builder, hook_buttons)
        try:
            await callback_query.message.edit_text(
                text=text,
                reply_markup=kb_builder.as_markup(),
            )
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise
    else:
        try:
            await callback_query.message.edit_text(
                text=text,
                reply_markup=await build_users_key_expiry_kb(
                    session,
                    callback_data.tg_id,
                    email,
                    key_ref=str(key_ref),
                ),
            )
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_expiry_edit"),
    IsAdminFilter(),
)
async def handle_change_expiry(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    tg_id = callback_data.tg_id
    key_ref = str(callback_data.data)
    key_obj = await resolve_callback_key(session, tg_id, key_ref)
    if not key_obj:
        await callback_query.message.edit_text(
            text=menu_text("Подписка", "❌ Подписка не найдена.", markup=build_editor_kb(tg_id)),
            reply_markup=build_editor_kb(tg_id),
        )
        return
    email = key_obj.email

    await callback_query.message.edit_reply_markup(
        reply_markup=await build_users_key_expiry_kb(session, tg_id, email, key_ref=key_ref)
    )


@router.callback_query(
    AdminUserKeyEditorCallback.filter(F.action == "add"),
    IsAdminFilter(),
)
async def handle_expiry_add(
    callback_query: CallbackQuery,
    callback_data: AdminUserKeyEditorCallback,
    state: FSMContext,
    session: AsyncSession,
):
    tg_id = callback_data.tg_id
    key_ref = str(callback_data.data)
    key_obj = await resolve_callback_key(session, tg_id, key_ref)
    if not key_obj:
        await callback_query.message.edit_text(
            text=menu_text("Подписка", "❌ Подписка не найдена.", markup=build_editor_kb(tg_id)),
            reply_markup=build_editor_kb(tg_id),
        )
        return
    email = key_obj.email
    days = callback_data.month

    key_details = await get_key_details(session, email)

    if not key_details:
        await callback_query.message.edit_text(
            text=menu_text("Подписка", "❌ Подписка не найдена.", markup=build_editor_kb(tg_id)),
            reply_markup=build_editor_kb(tg_id),
        )
        return

    if days:
        await change_expiry_time(key_details["expiry_time"] + days * 24 * 3600 * 1000, email, session)
        await handle_key_edit(callback_query, callback_data, session, True)
        return

    await state.update_data(tg_id=tg_id, email=email, key_ref=key_ref, op_type="add")
    await state.set_state(UserEditorState.waiting_for_expiry_time)

    await callback_query.message.edit_text(
        text=menu_text(
            "Подписка",
            "✍️ На сколько дней продлить?",
            markup=build_users_key_show_kb(tg_id, key_ref),
        ),
        reply_markup=build_users_key_show_kb(tg_id, key_ref),
    )


@router.callback_query(
    AdminUserKeyEditorCallback.filter(F.action == "take"),
    IsAdminFilter(),
)
async def handle_expiry_take(
    callback_query: CallbackQuery,
    callback_data: AdminUserKeyEditorCallback,
    state: FSMContext,
    session: AsyncSession,
):
    tg_id = callback_data.tg_id
    key_ref = str(callback_data.data)
    key_obj = await resolve_callback_key(session, tg_id, key_ref)
    if not key_obj:
        await callback_query.message.edit_text(
            text=menu_text("Подписка", "❌ Подписка не найдена.", markup=build_editor_kb(tg_id)),
            reply_markup=build_editor_kb(tg_id),
        )
        return
    email = key_obj.email

    await state.update_data(tg_id=tg_id, email=email, key_ref=key_ref, op_type="take")
    await state.set_state(UserEditorState.waiting_for_expiry_time)

    await callback_query.message.edit_text(
        text=menu_text(
            "Подписка",
            "✍️ На сколько дней сократить?",
            markup=build_users_key_show_kb(tg_id, key_ref),
        ),
        reply_markup=build_users_key_show_kb(tg_id, key_ref),
    )


@router.callback_query(
    AdminUserKeyEditorCallback.filter(F.action == "set"),
    IsAdminFilter(),
)
async def handle_expiry_set(
    callback_query: CallbackQuery,
    callback_data: AdminUserKeyEditorCallback,
    state: FSMContext,
    session: AsyncSession,
):
    tg_id = callback_data.tg_id
    key_ref = str(callback_data.data)
    key_obj = await resolve_callback_key(session, tg_id, key_ref)
    if not key_obj:
        await callback_query.message.edit_text(
            text=menu_text("Подписка", "❌ Подписка не найдена.", markup=build_editor_kb(tg_id)),
            reply_markup=build_editor_kb(tg_id),
        )
        return
    email = key_obj.email

    key_details = await get_key_details(session, email)

    if not key_details:
        await callback_query.message.edit_text(
            text=menu_text("Подписка", "❌ Подписка не найдена.", markup=build_editor_kb(tg_id)),
            reply_markup=build_editor_kb(tg_id),
        )
        return

    await state.update_data(tg_id=tg_id, email=email, key_ref=key_ref, op_type="set")
    await state.set_state(UserEditorState.waiting_for_expiry_time)

    text = menu_text(
        "Подписка",
        "✍️ Введите новое время действия ключа:",
        quote("📌 Формат: <b>год-месяц-день час:минута</b>"),
        quote(
            f"📄 Текущая дата: {datetime.fromtimestamp(key_details['expiry_time'] / 1000, tz=MOSCOW_TZ).strftime('%Y-%m-%d %H:%M')} (МСК)"
        ),
    )

    await callback_query.message.edit_text(
        text=text,
        reply_markup=build_users_key_show_kb(tg_id, key_ref),
    )


@router.message(UserEditorState.waiting_for_expiry_time, IsAdminFilter())
async def handle_expiry_time_input(message: Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    tg_id = data.get("tg_id")
    email = data.get("email")
    key_ref = data.get("key_ref")
    op_type = data.get("op_type")

    if op_type != "set" and (not message.text.isdigit() or int(message.text) < 0):
        await message.answer(
            text=menu_text("Подписка", "❌ Нужно число дней."),
            reply_markup=build_users_key_show_kb(tg_id, key_ref) if key_ref else build_editor_kb(tg_id),
        )
        return

    key_details = await get_key_details(session, email)

    if not key_details:
        await message.answer(
            text=menu_text("Подписка", "❌ Подписка не найдена.", markup=build_editor_kb(tg_id)),
            reply_markup=build_editor_kb(tg_id),
        )
        return

    try:
        current_expiry_time = datetime.fromtimestamp(
            key_details["expiry_time"] / 1000,
            tz=MOSCOW_TZ,
        )

        if op_type == "add":
            days = int(message.text)
            new_expiry_time = current_expiry_time + timedelta(days=days)
            text = menu_text("Подписка", f"✅ Ко времени действия ключа добавлено <b>{days} дн.</b>")
        elif op_type == "take":
            days = int(message.text)
            new_expiry_time = current_expiry_time - timedelta(days=days)
            text = menu_text("Подписка", f"✅ Из времени действия ключа вычтено <b>{days} дн.</b>")
        else:
            new_expiry_time = datetime.strptime(message.text, "%Y-%m-%d %H:%M")
            new_expiry_time = MOSCOW_TZ.localize(new_expiry_time)
            text = menu_text("Подписка", f"✅ Время действия ключа изменено на <b>{message.text} (МСК)</b>")

        new_expiry_timestamp = int(new_expiry_time.timestamp() * 1000)
        await change_expiry_time(new_expiry_timestamp, email, session)
    except ValueError:
        text = menu_text(
            "Срок действия", "Некорректный формат даты.", quote("Нужно так: <code>ГГГГ-ММ-ДД ЧЧ:ММ</code>")
        )
    except Exception as e:
        text = menu_text("Срок действия", "Не удалось изменить.", quote(f"<code>{e}</code>"))

    await message.answer(
        text=text,
        reply_markup=build_users_key_show_kb(tg_id, key_ref) if key_ref else build_editor_kb(tg_id),
    )


async def change_expiry_time(expiry_time: int, email: str, session: AsyncSession) -> Exception | None:
    key_obj = await get_key_by_email(session, email)
    if not key_obj:
        return ValueError(f"User with email {email} was not found")

    client_id = key_obj.client_id
    tariff_id = key_obj.tariff_id
    server_id = key_obj.server_id
    key_device_limit = key_obj.current_device_limit
    key_traffic_limit = key_obj.current_traffic_limit
    if server_id is None:
        return ValueError(f"Key with client_id {client_id} was not found")

    traffic_limit = 0
    device_limit = None
    key_subgroup = None
    if tariff_id:
        tariff = await get_tariff_by_id(session, tariff_id)
        if tariff:
            traffic_limit = int(tariff.get("traffic_limit") or 0)
            raw_device_limit = tariff.get("device_limit")
            device_limit = int(raw_device_limit) if raw_device_limit is not None else 0
            key_subgroup = tariff.get("subgroup_title")

    if key_device_limit is not None:
        device_limit = key_device_limit
    if key_traffic_limit is not None:
        traffic_limit = key_traffic_limit

    servers = await get_servers(session=session)

    if server_id in servers:
        target_cluster = server_id
    else:
        target_cluster = None
        for cluster_name, cluster_servers in servers.items():
            if any(s.get("server_name") == server_id for s in cluster_servers):
                target_cluster = cluster_name
                break

        if not target_cluster:
            return ValueError(f"No suitable cluster found for server {server_id}")

    await release_session_early(session)

    await renew_key_in_cluster(
        cluster_id=target_cluster,
        email=email,
        client_id=client_id,
        new_expiry_time=expiry_time,
        total_gb=traffic_limit,
        session=session,
        hwid_device_limit=device_limit,
        reset_traffic=False,
        target_subgroup=key_subgroup,
        old_subgroup=key_subgroup,
        plan=tariff_id,
    )

    await update_key_expiry(session, client_id, expiry_time)
    return None

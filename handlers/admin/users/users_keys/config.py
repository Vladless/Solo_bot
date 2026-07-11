"""Key config editor (base/addon devices + traffic limits)."""

from ._common import *  # noqa: F401,F403
from .edit import handle_key_edit


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_edit_config"),
    IsAdminFilter(),
)
async def handle_edit_config_start(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    state: FSMContext,
    session: AsyncSession,
):
    key_ref = str(callback_data.data)
    tg_id = callback_data.tg_id

    key_obj = await resolve_callback_key(session, tg_id, key_ref)

    if not key_obj:
        await callback_query.message.edit_text("❌ Ключ не найден.", reply_markup=build_editor_kb(tg_id))
        return

    email = key_obj.email

    if not key_obj.tariff_id:
        await callback_query.message.edit_text(
            "❌ У ключа не назначен тариф.",
            reply_markup=build_key_edit_kb(key_obj.__dict__, email),
        )
        return

    tariff = await get_tariff_by_id(session, key_obj.tariff_id)
    if not tariff or not tariff.get("configurable"):
        await callback_query.message.edit_text(
            "❌ Тариф не поддерживает конфигурацию.",
            reply_markup=build_key_edit_kb(key_obj.__dict__, email),
        )
        return

    base_devices = key_obj.selected_device_limit or tariff.get("device_limit") or 1
    current_devices = key_obj.current_device_limit or base_devices
    extra_devices = max(0, current_devices - base_devices)

    base_traffic = key_obj.selected_traffic_limit
    current_traffic = key_obj.current_traffic_limit
    extra_traffic = max(0, (current_traffic or 0) - (base_traffic or 0)) if current_traffic and base_traffic else 0

    await state.set_state(UserEditorState.config_menu)
    await state.update_data(
        email=email,
        key_ref=key_ref,
        tg_id=tg_id,
        tariff_id=key_obj.tariff_id,
        cfg_base_devices=base_devices,
        cfg_extra_devices=extra_devices,
        cfg_base_traffic=base_traffic,
        cfg_extra_traffic=extra_traffic,
    )

    await render_config_menu(callback_query, state, session)


async def render_config_menu(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    email = data.get("email")
    key_ref = data.get("key_ref")
    tg_id = data.get("tg_id")
    tariff_id = data.get("tariff_id")

    tariff = await get_tariff_by_id(session, tariff_id)
    if not tariff:
        await callback_query.message.edit_text("❌ Тариф не найден.")
        await state.clear()
        return

    base_devices = data.get("cfg_base_devices") or 1
    extra_devices = data.get("cfg_extra_devices") or 0
    base_traffic = data.get("cfg_base_traffic")
    extra_traffic = data.get("cfg_extra_traffic") or 0

    traffic_to_show = base_traffic
    if traffic_to_show is None and email:
        key_obj = await get_key_by_email(session, email)
        if key_obj:
            traffic_to_show = key_obj.selected_traffic_limit or key_obj.current_traffic_limit
    if traffic_to_show is None and tariff:
        raw = tariff.get("traffic_limit")
        if raw is not None:
            try:
                val = int(raw)
                if val > 0:
                    traffic_to_show = val
            except (TypeError, ValueError):
                pass

    text = (
        f"<b>⚙️ Конфигурация ключа</b>\n\n"
        f"🔑 <b>Ключ:</b> <code>{email}</code>\n"
        f"📦 <b>Тариф:</b> {tariff.get('name')}\n\n"
    )

    extra_dev_str = f" + {extra_devices} (докуплено)" if extra_devices > 0 else ""
    text += f"📱 <b>Устройства:</b> {base_devices}{extra_dev_str}\n"

    if traffic_to_show:
        extra_traf_str = f" + {extra_traffic} ГБ (докуплено)" if extra_traffic > 0 else ""
        text += f"📊 <b>Трафик:</b> {traffic_to_show} ГБ{extra_traf_str}\n"
    else:
        text += "📊 <b>Трафик:</b> безлимит\n"

    text += "\n<i>Выберите что редактировать:</i>"

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📦 Тариф (база)", callback_data="cfg_edit_base"),
        InlineKeyboardButton(text="➕ Докупка", callback_data="cfg_edit_addon"),
    )
    builder.row(InlineKeyboardButton(text="💾 Сохранить", callback_data="cfg_save"))
    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminUserEditorCallback(action="users_key_edit", data=key_ref, tg_id=tg_id).pack(),
        )
    )

    await state.set_state(UserEditorState.config_menu)
    await callback_query.message.edit_text(text=text, reply_markup=builder.as_markup())


@router.callback_query(F.data == "cfg_edit_base", UserEditorState.config_menu, IsAdminFilter())
async def handle_cfg_edit_base(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    tariff = await get_tariff_by_id(session, data.get("tariff_id"))
    device_options = tariff.get("device_options") or [] if tariff else []
    traffic_options = tariff.get("traffic_options_gb") or [] if tariff else []

    builder = InlineKeyboardBuilder()
    if device_options:
        builder.row(InlineKeyboardButton(text="📱 Устройства", callback_data="cfg_base_devices"))
    if traffic_options:
        builder.row(InlineKeyboardButton(text="📊 Трафик", callback_data="cfg_base_traffic"))
    builder.row(InlineKeyboardButton(text=BACK, callback_data="cfg_back_menu"))

    await callback_query.message.edit_text(
        "<b>📦 Редактирование базы тарифа</b>\n\nВыберите параметр:",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data == "cfg_edit_addon", UserEditorState.config_menu, IsAdminFilter())
async def handle_cfg_edit_addon(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    tariff = await get_tariff_by_id(session, data.get("tariff_id"))
    device_options = tariff.get("device_options") or [] if tariff else []
    traffic_options = tariff.get("traffic_options_gb") or [] if tariff else []

    builder = InlineKeyboardBuilder()
    if device_options:
        builder.row(InlineKeyboardButton(text="📱 Устройства", callback_data="cfg_addon_devices"))
    if traffic_options:
        builder.row(InlineKeyboardButton(text="📊 Трафик", callback_data="cfg_addon_traffic"))
    builder.row(InlineKeyboardButton(text=BACK, callback_data="cfg_back_menu"))

    await callback_query.message.edit_text(
        "<b>➕ Редактирование докупки</b>\n\nВыберите параметр:",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data == "cfg_back_menu", UserEditorState.config_menu, IsAdminFilter())
async def handle_cfg_back_menu(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    await render_config_menu(callback_query, state, session)


@router.callback_query(F.data == "cfg_base_devices", UserEditorState.config_menu, IsAdminFilter())
async def handle_cfg_base_devices(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    tariff = await get_tariff_by_id(session, data.get("tariff_id"))
    device_options = tariff.get("device_options") or [] if tariff else []
    base_devices = data.get("cfg_base_devices") or 1

    builder = InlineKeyboardBuilder()
    for opt in sorted(device_options):
        mark = " ✅" if int(opt) == int(base_devices) else ""
        builder.button(text=f"{opt} устр.{mark}", callback_data=f"cfg_set_base_dev:{opt}")
    builder.adjust(3)
    builder.row(InlineKeyboardButton(text=BACK, callback_data="cfg_back_menu"))

    await state.set_state(UserEditorState.config_select_base)
    await state.update_data(cfg_param="devices")
    await callback_query.message.edit_text(
        "<b>📱 Выберите базу устройств:</b>",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data == "cfg_base_traffic", UserEditorState.config_menu, IsAdminFilter())
async def handle_cfg_base_traffic(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    tariff = await get_tariff_by_id(session, data.get("tariff_id"))
    traffic_options = tariff.get("traffic_options_gb") or [] if tariff else []
    base_traffic = data.get("cfg_base_traffic")

    builder = InlineKeyboardBuilder()
    for opt in sorted(traffic_options):
        is_sel = (base_traffic is None and opt == 0) or (base_traffic is not None and int(opt) == int(base_traffic))
        mark = " ✅" if is_sel else ""
        label = "безлимит" if opt == 0 else f"{opt} ГБ"
        builder.button(text=f"{label}{mark}", callback_data=f"cfg_set_base_traf:{opt}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text=BACK, callback_data="cfg_back_menu"))

    await state.set_state(UserEditorState.config_select_base)
    await state.update_data(cfg_param="traffic")
    await callback_query.message.edit_text(
        "<b>📊 Выберите базу трафика:</b>",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("cfg_set_base_dev:"), UserEditorState.config_select_base, IsAdminFilter())
async def handle_cfg_set_base_dev(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    base_devices = int(callback_query.data.split(":")[1])
    await state.update_data(cfg_base_devices=base_devices)
    await callback_query.answer(f"✅ База устройств: {base_devices}")
    await state.set_state(UserEditorState.config_menu)
    await render_config_menu(callback_query, state, session)


@router.callback_query(F.data.startswith("cfg_set_base_traf:"), UserEditorState.config_select_base, IsAdminFilter())
async def handle_cfg_set_base_traf(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    traffic_gb = int(callback_query.data.split(":")[1])
    await state.update_data(cfg_base_traffic=traffic_gb if traffic_gb > 0 else None)
    label = "безлимит" if traffic_gb == 0 else f"{traffic_gb} ГБ"
    await callback_query.answer(f"✅ База трафика: {label}")
    await state.set_state(UserEditorState.config_menu)
    await render_config_menu(callback_query, state, session)


@router.callback_query(F.data == "cfg_addon_devices", UserEditorState.config_menu, IsAdminFilter())
async def handle_cfg_addon_devices(callback_query: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    extra_devices = data.get("cfg_extra_devices") or 0

    await state.set_state(UserEditorState.config_input_addon)
    await state.update_data(cfg_param="devices")

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Отмена", callback_data="cfg_cancel_input"))

    await callback_query.message.edit_text(
        f"<b>📱 Докупка устройств</b>\n\n"
        f"Текущее значение: <b>{extra_devices}</b>\n\n"
        f"Введите новое количество докупленных устройств (число):",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data == "cfg_addon_traffic", UserEditorState.config_menu, IsAdminFilter())
async def handle_cfg_addon_traffic(callback_query: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    extra_traffic = data.get("cfg_extra_traffic") or 0

    await state.set_state(UserEditorState.config_input_addon)
    await state.update_data(cfg_param="traffic")

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Отмена", callback_data="cfg_cancel_input"))

    await callback_query.message.edit_text(
        f"<b>📊 Докупка трафика</b>\n\n"
        f"Текущее значение: <b>{extra_traffic} ГБ</b>\n\n"
        f"Введите новое количество докупленного трафика в ГБ (число):",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data == "cfg_cancel_input", UserEditorState.config_input_addon, IsAdminFilter())
async def handle_cfg_cancel_input(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.set_state(UserEditorState.config_menu)
    await render_config_menu(callback_query, state, session)


@router.message(UserEditorState.config_input_addon, IsAdminFilter())
async def handle_cfg_input_addon(message: Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    param = data.get("cfg_param")
    email = data.get("email")
    key_ref = data.get("key_ref")
    tg_id = data.get("tg_id")
    tariff_id = data.get("tariff_id")

    if not message.text or not message.text.isdigit():
        await message.answer("❌ Введите корректное число.")
        return

    value = int(message.text)
    if value < 0:
        await message.answer("❌ Значение не может быть отрицательным.")
        return

    if param == "devices":
        await state.update_data(cfg_extra_devices=value)
    else:
        await state.update_data(cfg_extra_traffic=value)

    await state.set_state(UserEditorState.config_menu)

    data = await state.get_data()
    tariff = await get_tariff_by_id(session, tariff_id)

    base_devices = data.get("cfg_base_devices") or 1
    extra_devices = data.get("cfg_extra_devices") or 0
    base_traffic = data.get("cfg_base_traffic")
    extra_traffic = data.get("cfg_extra_traffic") or 0

    text = (
        f"<b>⚙️ Конфигурация ключа</b>\n\n"
        f"🔑 <b>Ключ:</b> <code>{email}</code>\n"
        f"📦 <b>Тариф:</b> {tariff.get('name') if tariff else '—'}\n\n"
    )

    extra_dev_str = f" + {extra_devices} (докуплено)" if extra_devices > 0 else ""
    text += f"📱 <b>Устройства:</b> {base_devices}{extra_dev_str}\n"

    if base_traffic:
        extra_traf_str = f" + {extra_traffic} ГБ (докуплено)" if extra_traffic > 0 else ""
        text += f"📊 <b>Трафик:</b> {base_traffic} ГБ{extra_traf_str}\n"
    else:
        text += "📊 <b>Трафик:</b> безлимит\n"

    text += "\n<i>Выберите что редактировать:</i>"

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📦 Тариф (база)", callback_data="cfg_edit_base"),
        InlineKeyboardButton(text="➕ Докупка", callback_data="cfg_edit_addon"),
    )
    builder.row(InlineKeyboardButton(text="💾 Сохранить", callback_data="cfg_save"))
    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminUserEditorCallback(action="users_key_edit", data=key_ref, tg_id=tg_id).pack(),
        )
    )

    await message.answer(text=text, reply_markup=builder.as_markup())


@router.callback_query(F.data == "cfg_save", UserEditorState.config_menu, IsAdminFilter(), flags={"popup": True})
async def handle_cfg_save(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    email = data.get("email")
    tg_id = data.get("tg_id")
    tariff_id = data.get("tariff_id")

    base_devices = data.get("cfg_base_devices") or 1
    extra_devices = data.get("cfg_extra_devices") or 0
    total_devices = base_devices + extra_devices

    base_traffic = data.get("cfg_base_traffic")
    extra_traffic = data.get("cfg_extra_traffic") or 0
    total_traffic = (base_traffic + extra_traffic) if base_traffic else None

    tariff = await get_tariff_by_id(session, tariff_id)
    selected_price = None
    if tariff:
        base_price = tariff.get("price_rub") or 0

        device_step = tariff.get("device_step_rub") or 0
        tariff_base_devices = tariff.get("device_limit") or 1
        extra_base_devices = max(0, base_devices - tariff_base_devices)
        devices_extra_price = extra_base_devices * device_step

        traffic_step = tariff.get("traffic_step_rub") or 0
        tariff_base_traffic = tariff.get("traffic_limit") or 0
        extra_base_traffic = max(0, (base_traffic or 0) - tariff_base_traffic) if base_traffic else 0
        traffic_extra_price = extra_base_traffic * traffic_step

        selected_price = base_price + devices_extra_price + traffic_extra_price

    key_obj = await get_key_by_email(session, email)

    if not key_obj:
        await callback_query.message.edit_text("❌ Ключ не найден.", reply_markup=build_editor_kb(tg_id))
        await state.clear()
        return

    try:
        await release_session_early(session)
        await renew_key_in_cluster(
            cluster_id=key_obj.server_id,
            email=email,
            client_id=key_obj.client_id,
            new_expiry_time=key_obj.expiry_time,
            total_gb=total_traffic or 0,
            session=session,
            hwid_device_limit=total_devices,
            reset_traffic=False,
            plan=tariff_id,
        )

        await save_admin_key_config(
            session,
            email=email,
            base_devices=base_devices,
            total_devices=total_devices,
            base_traffic=base_traffic,
            total_traffic=total_traffic,
            selected_price=selected_price,
        )

        await state.clear()
        await callback_query.answer("✅ Конфигурация сохранена", show_alert=True)

        callback_data_back = AdminUserEditorCallback(action="users_key_edit", data=email, tg_id=tg_id)
        await handle_key_edit(
            callback_query=callback_query,
            callback_data=callback_data_back,
            session=session,
            update=False,
        )

    except Exception as e:
        logger.error(f"[EditConfig] Ошибка при сохранении конфигурации: {e}")
        await callback_query.message.edit_text(
            "❌ Не удалось сохранить конфигурацию. Попробуйте позже.",
            reply_markup=build_editor_kb(tg_id),
        )
        await state.clear()


@router.callback_query(F.data == "cfg_back_menu", IsAdminFilter())
async def handle_cfg_back_menu_any(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.set_state(UserEditorState.config_menu)
    await render_config_menu(callback_query, state, session)

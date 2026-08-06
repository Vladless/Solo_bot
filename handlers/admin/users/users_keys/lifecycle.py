from ._common import *  # noqa: F401,F403
from .edit import handle_key_edit


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_reissue_menu"),
    IsAdminFilter(),
)
async def handle_reissue_menu(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    tg_id = callback_data.tg_id
    key_ref = str(callback_data.data)
    key_obj = await resolve_callback_key(session, tg_id, key_ref)
    if not key_obj:
        await callback_query.message.edit_text("🚫 Ключ не найден.", reply_markup=build_editor_kb(tg_id))
        return

    text = (
        "<b>🔄 Перевыпуск подписки</b>\n\n"
        "<b>📦 Полный перевыпуск</b>\n"
        "<i>Пересоздаёт подписку на сервере с возможностью выбора кластера. "
        "Используйте для переноса на другой сервер или обновления данных.</i>\n\n"
        "<b>🔗 Сменить ссылку</b>\n"
        "<i>Генерирует новую ссылку подписки. Старая ссылка перестанет работать. "
        "Все данные подписки сохранятся.</i>"
    )

    await callback_query.message.edit_text(
        text=text,
        reply_markup=build_reissue_menu_kb(key_ref, tg_id),
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_update_key"),
    IsAdminFilter(),
)
async def handle_update_key(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    tg_id = callback_data.tg_id
    key_ref = str(callback_data.data)
    key_obj = await resolve_callback_key(session, tg_id, key_ref)
    if not key_obj:
        await callback_query.message.edit_text("🚫 Ключ не найден.", reply_markup=build_editor_kb(tg_id))
        return
    email = key_obj.email

    await callback_query.message.edit_text(
        text=f"📡 Выберите кластер, на котором пересоздать ключ <b>{email}</b>:",
        reply_markup=await build_cluster_selection_kb(
            session,
            tg_id,
            key_ref,
            action="confirm_admin_key_reissue",
        ),
    )


@router.callback_query(F.data.startswith("confirm_admin_key_reissue|"), IsAdminFilter())
async def confirm_admin_key_reissue(callback_query: CallbackQuery, session: AsyncSession, state: FSMContext):
    _, tg_id, key_ref, cluster_id = callback_query.data.split("|")
    tg_id = int(tg_id)
    key_obj = await resolve_callback_key(session, tg_id, key_ref)
    if not key_obj:
        await callback_query.message.edit_text("🚫 Ключ не найден.", reply_markup=build_editor_kb(tg_id))
        return
    email = key_obj.email

    try:
        servers = await get_servers(session)
        cluster_servers = servers.get(cluster_id, [])

        tariffs = await get_tariffs_for_cluster(session, cluster_id)
        if not tariffs:
            builder = InlineKeyboardBuilder()
            builder.row(
                InlineKeyboardButton(
                    text="🔗 Привязать тариф",
                    callback_data=AdminPanelCallback(action="clusters").pack(),
                )
            )
            builder.row(
                InlineKeyboardButton(
                    text=BACK,
                    callback_data=AdminUserEditorCallback(
                        action="users_key_edit",
                        tg_id=tg_id,
                        data=key_ref,
                    ).pack(),
                )
            )
            await callback_query.message.edit_text(
                f"🚫 <b>Невозможно пересоздать подписку</b>\n\n"
                f"📊 <b>Информация о кластере:</b>\n<blockquote>"
                f"🌐 <b>Кластер:</b> <code>{cluster_id}</code>\n"
                f"⚠️ <b>Статус:</b> Нет привязанного тарифа\n</blockquote>"
                f"💡 <b>Привяжите тариф к кластеру</b>",
                reply_markup=builder.as_markup(),
            )
            return

        use_country_selection = bool(MODES_CONFIG.get("COUNTRY_SELECTION_ENABLED", USE_COUNTRY_SELECTION))

        if use_country_selection:
            unique_countries = {srv["server_name"] for srv in cluster_servers}
            await state.update_data(tg_id=tg_id, email=email, key_ref=key_ref, cluster_id=cluster_id)
            builder = InlineKeyboardBuilder()
            for country in sorted(unique_countries):
                builder.button(
                    text=country,
                    callback_data=f"admin_reissue_country|{tg_id}|{key_ref}|{country}",
                )
            builder.row(
                InlineKeyboardButton(
                    text=BACK,
                    callback_data=AdminUserEditorCallback(
                        action="users_key_edit",
                        tg_id=tg_id,
                        data=key_ref,
                    ).pack(),
                )
            )
            await callback_query.message.edit_text(
                "🌍 Выберите сервер (страну) для пересоздания подписки:",
                reply_markup=builder.as_markup(),
            )
            return

        key_link = await get_key_by_email(session, email)
        remnawave_link = key_link.remnawave_link if key_link else None

        await update_subscription(
            tg_id,
            email,
            session,
            cluster_override=cluster_id,
            remnawave_link=remnawave_link,
        )

        await handle_key_edit(
            callback_query,
            AdminUserEditorCallback(tg_id=tg_id, data=key_ref, action="view_key"),
            session,
            True,
        )
    except Exception as e:
        logger.error(f"Ошибка при перевыпуске ключа {email}: {e}")
        await callback_query.message.answer(f"❗ Ошибка: {e}")


@router.callback_query(F.data.startswith("admin_reissue_country|"), IsAdminFilter())
async def admin_reissue_country(callback_query: CallbackQuery, session: AsyncSession, state: FSMContext):
    _, tg_id, key_ref, country = callback_query.data.split("|")
    tg_id = int(tg_id)
    key_obj = await resolve_callback_key(session, tg_id, key_ref)
    if not key_obj:
        await callback_query.message.edit_text("🚫 Ключ не найден.", reply_markup=build_editor_kb(tg_id))
        return
    email = key_obj.email

    try:
        data = await state.get_data()
        cluster_id = data.get("cluster_id")

        if cluster_id:
            tariffs = await get_tariffs_for_cluster(session, cluster_id)
            if not tariffs:
                builder = InlineKeyboardBuilder()
                builder.row(
                    InlineKeyboardButton(
                        text="🔗 Привязать тариф",
                        callback_data=AdminPanelCallback(action="clusters").pack(),
                    )
                )
                builder.row(
                    InlineKeyboardButton(
                        text=BACK,
                        callback_data=AdminUserEditorCallback(
                            action="users_key_edit",
                            tg_id=tg_id,
                            data=key_ref,
                        ).pack(),
                    )
                )
                await callback_query.message.edit_text(
                    f"🚫 <b>Невозможно пересоздать подписку</b>\n\n"
                    f"📊 <b>Информация о кластере:</b>\n<blockquote>"
                    f"🌐 <b>Кластер:</b> <code>{cluster_id}</code>\n"
                    f"⚠️ <b>Статус:</b> Нет привязанного тарифа\n</blockquote>"
                    f"💡 <b>Привяжите тариф к кластеру</b>",
                    reply_markup=builder.as_markup(),
                )
                return

        key_link = await get_key_by_email(session, email)
        remnawave_link = key_link.remnawave_link if key_link else None

        await update_subscription(
            tg_id=tg_id,
            email=email,
            session=session,
            country_override=country,
            remnawave_link=remnawave_link,
        )

        await handle_key_edit(
            callback_query,
            AdminUserEditorCallback(tg_id=tg_id, data=key_ref, action="view_key"),
            session,
            True,
        )
    except Exception as e:
        logger.error(f"Ошибка при перевыпуске ключа для страны {country}: {e}")
        await callback_query.message.answer(f"❗ Ошибка: {e}")


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_recreate_key"),
    IsAdminFilter(),
)
async def handle_recreate_key_start(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    tg_id = callback_data.tg_id
    key_ref = str(callback_data.data)
    key_obj = await resolve_callback_key(session, tg_id, key_ref)

    if not key_obj:
        await callback_query.message.edit_text(
            text="🚫 Ключ не найден.",
            reply_markup=build_editor_kb(tg_id),
        )
        return

    tariff_name = "—"
    if key_obj.tariff_id:
        tariff = await get_tariff_by_id(session, key_obj.tariff_id)
        if tariff:
            tariff_name = tariff.get("name", "—")

    text = (
        "<b>🔁 Пересоздание ссылки подписки</b>\n\n"
        f"📦 <b>Тариф:</b> {tariff_name}\n\n"
        "⚠️ <b>Будет сгенерирована новая ссылка подписки.</b>\n"
        "Старая ссылка перестанет работать.\n\n"
        "✅ <i>Все данные подписки сохранятся.</i>"
    )

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Пересоздать",
            callback_data=f"confirm_recreate|{tg_id}|{key_ref}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminUserEditorCallback(action="users_key_edit", tg_id=tg_id, data=key_ref).pack(),
        )
    )

    await callback_query.message.edit_text(text=text, reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("confirm_recreate|"), IsAdminFilter())
async def handle_recreate_key_confirm(
    callback_query: CallbackQuery,
    session: AsyncSession,
):
    _, tg_id, key_ref = callback_query.data.split("|")
    tg_id = int(tg_id)

    try:
        key_obj = await resolve_callback_key(session, tg_id, key_ref)

        if not key_obj:
            await callback_query.message.edit_text(
                text="🚫 Ключ не найден.",
                reply_markup=build_editor_kb(tg_id),
            )
            return

        old_email = key_obj.email

        await callback_query.message.edit_text("⏳ Пересоздание ссылки подписки...")

        client_id = key_obj.client_id
        cluster_id = key_obj.server_id
        old_link = key_obj.remnawave_link or key_obj.key

        servers = await get_servers(session)
        cluster = servers.get(cluster_id)

        if not cluster:
            for _, server_list in servers.items():
                for server_info in server_list:
                    if server_info.get("server_name", "").lower() == cluster_id.lower():
                        cluster = [server_info]
                        break
                if cluster:
                    break

        if not cluster:
            await callback_query.message.edit_text(
                text=f"❗ Кластер {cluster_id} не найден.",
                reply_markup=build_editor_kb(tg_id),
            )
            return

        remnawave_servers = [s for s in cluster if s.get("panel_type", "3x-ui").lower() == "remnawave"]
        threexui_servers = [s for s in cluster if s.get("panel_type", "3x-ui").lower() != "remnawave"]

        if remnawave_servers:
            api_url = remnawave_servers[0].get("api_url")
            if not api_url:
                await callback_query.message.edit_text(
                    text="❗ У Remnawave сервера не задан api_url.",
                    reply_markup=build_editor_kb(tg_id),
                )
                return

            api = RemnawaveAPI(api_url)
            try:
                if not REMNAWAVE_TOKEN_LOGIN_ENABLED:
                    await api.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD)

                user_data = await api.revoke_user_subscription(client_id, username=old_email)
            finally:
                await api.aclose()

            if not user_data:
                await callback_query.message.edit_text(
                    text="❗ Не удалось выполнить revoke. Проверьте логи.",
                    reply_markup=build_editor_kb(tg_id),
                )
                return

            new_link = user_data.get("subscriptionUrl")

            if not new_link:
                await callback_query.message.edit_text(
                    text="❗ Revoke выполнен, но новая ссылка не получена.",
                    reply_markup=build_editor_kb(tg_id),
                )
                return

            await update_key_subscription_links(session, old_email, new_link)

        elif threexui_servers:
            from database.keys import update_key_email_and_link
            from panels._3xui import change_client_email, get_xui_instance
            from settings.config import PUBLIC_LINK, SUPERNODE

            new_email = await generate_random_email(session=session)
            changed_any = False
            for server in threexui_servers:
                api_url = server.get("api_url")
                inbound_id = server.get("inbound_id")
                server_name = server.get("server_name", "unknown")
                if not api_url or not inbound_id:
                    continue
                old_unique = f"{old_email}_{server_name.lower()}" if SUPERNODE else old_email
                new_unique = f"{new_email}_{server_name.lower()}" if SUPERNODE else new_email
                try:
                    xui = await get_xui_instance(api_url)
                    if await change_client_email(xui, int(inbound_id), old_unique, new_unique, new_email, client_id):
                        changed_any = True
                except Exception as e:
                    logger.error(f"[3x-ui revoke] сервер {server_name}: {e}")

            if not changed_any:
                await callback_query.message.edit_text(
                    text="❗ Не удалось сменить ссылку ни на одном 3x-ui сервере. Проверьте логи.",
                    reply_markup=build_editor_kb(tg_id),
                )
                return

            new_link = f"{PUBLIC_LINK}{new_email}/{tg_id}"
            await update_key_email_and_link(session, old_email, new_email, new_link, client_id)

        else:
            await callback_query.message.edit_text(
                text="❗ В кластере нет серверов Remnawave или 3x-ui.",
                reply_markup=build_editor_kb(tg_id),
            )
            return

        try:
            user_text = (
                "🔄 <b>Ваша подписка была перевыпущена</b>\n\n"
                f"🔗 <b>Новая ссылка подписки:</b>\n<code>{new_link}</code>\n\n"
                "<i>Старая ссылка больше не работает.</i>"
            )
            user_kb = InlineKeyboardBuilder()
            user_kb.row(
                InlineKeyboardButton(
                    text="📱 Мои подписки",
                    callback_data="view_keys",
                )
            )
            user_kb.row(
                InlineKeyboardButton(
                    text="👤 Личный кабинет",
                    callback_data="profile",
                )
            )

            await callback_query.bot.send_message(
                chat_id=tg_id,
                text=user_text,
                reply_markup=user_kb.as_markup(),
            )
            notification_sent = True
        except Exception as e:
            logger.warning(f"Не удалось отправить уведомление клиенту {tg_id}: {e}")
            notification_sent = False

        text = (
            "✅ <b>Ссылка подписки пересоздана</b>\n\n"
            f"🔗 <b>Старая ссылка:</b>\n<code>{old_link}</code>\n\n"
            f"🔗 <b>Новая ссылка:</b>\n<code>{new_link}</code>\n\n"
        )
        if notification_sent:
            text += "📨 <i>Клиент уведомлён о новой ссылке.</i>"
        else:
            text += "⚠️ <i>Не удалось уведомить клиента.</i>"

        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(
                text=BACK,
                callback_data=AdminUserEditorCallback(
                    action="users_key_edit",
                    tg_id=tg_id,
                    data=key_ref,
                ).pack(),
            )
        )

        await callback_query.message.edit_text(
            text=text,
            reply_markup=builder.as_markup(),
        )

    except Exception as e:
        logger.error(f"Ошибка при revoke ключа {old_email}: {e}")
        await callback_query.message.edit_text(
            text=f"❗ Ошибка при пересоздании: {e}",
            reply_markup=build_editor_kb(tg_id),
        )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_delete_key"),
    IsAdminFilter(),
)
async def handle_delete_key(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    state: FSMContext,
    session: AsyncSession,
):
    key_obj = await resolve_callback_key(session, callback_data.tg_id, callback_data.data)
    if not key_obj:
        await callback_query.message.edit_text(
            text="🚫 Ключ не найден!",
            reply_markup=build_editor_kb(callback_data.tg_id),
        )
        return

    email = key_obj.email
    client_id = key_obj.client_id

    if client_id is None:
        await callback_query.message.edit_text(
            text="🚫 Ключ не найден!",
            reply_markup=build_editor_kb(callback_data.tg_id),
        )
        return

    await state.set_state(UserEditorState.confirm_delete_key)
    await state.update_data(
        delete_key_email=email,
        delete_key_tg_id=int(callback_data.tg_id),
        delete_key_client_id=client_id,
    )

    await callback_query.message.edit_text(
        text="❓ Вы уверены, что хотите удалить ключ?",
        reply_markup=build_key_delete_kb(callback_data.tg_id),
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_delete_key_confirm"),
    UserEditorState.confirm_delete_key,
    IsAdminFilter(),
    flags={"popup": True},
)
async def handle_delete_key_confirm(
    callback_query: types.CallbackQuery,
    callback_data: AdminUserEditorCallback,
    state: FSMContext,
    session: AsyncSession,
):
    data = await state.get_data()
    email = data.get("delete_key_email")
    expected_tg_id = data.get("delete_key_tg_id")
    client_id = data.get("delete_key_client_id")
    await state.clear()

    if not email or int(expected_tg_id or 0) != int(callback_data.tg_id):
        await callback_query.answer("Данные устарели", show_alert=True)
        return

    if not client_id:
        key_obj = await get_key_by_email(session, email, int(callback_data.tg_id))
        client_id = key_obj.client_id if key_obj else None

    kb = build_editor_kb(callback_data.tg_id)

    if client_id:
        clusters = await get_servers(session=session)
        await release_session_early(session)

        async def delete_key_from_servers():
            tasks = []
            for cluster_name, cluster_servers in clusters.items():
                for _ in cluster_servers:
                    tasks.append(delete_key_from_cluster(cluster_name, email, client_id, session))
            await asyncio.gather(*tasks, return_exceptions=True)

        await delete_key_from_servers()
        await delete_key(session, client_id)

        await callback_query.message.edit_text(text="✅ Ключ успешно удален.", reply_markup=kb)
    else:
        await callback_query.message.edit_text(
            text="🚫 Ключ не найден или уже удален.",
            reply_markup=kb,
        )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_delete_user"),
    IsAdminFilter(),
)
async def handle_delete_user(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
):
    tg_id = callback_data.tg_id
    await callback_query.message.edit_text(
        text=f"❗️ Вы уверены, что хотите удалить пользователя с ID {tg_id}?",
        reply_markup=build_user_delete_kb(tg_id),
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_delete_user_confirm"),
    IsAdminFilter(),
)
async def handle_delete_user_confirm(
    callback_query: types.CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    tg_id = callback_data.tg_id

    key_records = [(row.email, row.client_id) for row in await get_keys(session, tg_id)]
    await release_session_early(session)

    async def delete_keys_from_servers():
        try:
            tasks = []
            servers = await get_servers(session=session)
            for email, client_id in key_records:
                for cluster_id, _cluster in servers.items():
                    tasks.append(delete_key_from_cluster(cluster_id, email, client_id, session))
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            logger.error(f"Ошибка при удалении ключей с серверов для пользователя {tg_id}: {e}")

    await delete_keys_from_servers()

    try:
        await delete_user_data(session, tg_id)
        await callback_query.message.edit_text(
            text=f"🗑️ Пользователь с ID {tg_id} был удален.",
            reply_markup=build_admin_back_kb(),
        )
    except Exception as e:
        logger.error(f"Ошибка при удалении данных из базы данных для пользователя {tg_id}: {e}")
        await callback_query.message.edit_text(
            text=f"❌ Произошла ошибка при удалении пользователя с ID {tg_id}. Попробуйте снова.",
            reply_markup=build_admin_back_kb(),
        )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_create_key"),
    IsAdminFilter(),
)
async def handle_create_key_start(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    state: FSMContext,
    session: AsyncSession,
):
    tg_id = callback_data.tg_id
    await state.update_data(tg_id=tg_id)

    use_country_selection = bool(MODES_CONFIG.get("COUNTRY_SELECTION_ENABLED", USE_COUNTRY_SELECTION))

    if use_country_selection:
        await state.set_state(UserEditorState.selecting_country)

        countries = await get_server_names(session)

        if not countries:
            await callback_query.message.edit_text(
                "❌ Нет доступных стран для создания ключа.",
                reply_markup=build_editor_kb(tg_id),
            )
            return

        builder = InlineKeyboardBuilder()
        for country in countries:
            builder.button(text=country, callback_data=country)
        builder.adjust(1)
        builder.row(build_admin_back_btn())

        await callback_query.message.edit_text(
            "🌍 <b>Выберите страну для создания ключа:</b>",
            reply_markup=builder.as_markup(),
        )
        return

    await state.set_state(UserEditorState.selecting_cluster)

    servers = await get_servers(session=session)
    cluster_names = list(servers.keys())

    if not cluster_names:
        await callback_query.message.edit_text(
            "❌ Нет доступных кластеров для создания ключа.",
            reply_markup=build_editor_kb(tg_id),
        )
        return

    builder = InlineKeyboardBuilder()
    for cluster in cluster_names:
        builder.button(text=f"🌐 {cluster}", callback_data=cluster)
    builder.adjust(2)
    builder.row(build_admin_back_btn())

    await callback_query.message.edit_text(
        "🌐 <b>Выберите кластер для создания ключа:</b>",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(UserEditorState.selecting_country, IsAdminFilter())
async def handle_create_key_country(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    country = callback_query.data
    await state.update_data(country=country)
    await state.set_state(UserEditorState.selecting_duration)

    builder = InlineKeyboardBuilder()

    cluster_info = await check_server_name_by_cluster(session, country)

    if not cluster_info:
        await callback_query.message.edit_text("❌ Сервер не найден.")
        return

    cluster_name = cluster_info["cluster_name"]
    await state.update_data(cluster_name=cluster_name)

    tariffs = await get_tariffs_for_cluster(session, cluster_name)

    for tariff in tariffs:
        if tariff["duration_days"] < 1:
            continue
        builder.button(
            text=f"{tariff['name']} — {tariff['price_rub']}₽",
            callback_data=f"tariff_{tariff['id']}",
        )

    builder.adjust(1)
    builder.row(build_admin_back_btn())

    await callback_query.message.edit_text(
        text=f"🕒 <b>Выберите срок действия ключа для страны <code>{country}</code>:</b>",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(UserEditorState.selecting_cluster, IsAdminFilter())
async def handle_create_key_cluster(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    cluster_name = callback_query.data

    data = await state.get_data()
    tg_id = data.get("tg_id")

    if not tg_id:
        await callback_query.message.edit_text("❌ Ошибка: tg_id клиента не найден.")
        return

    await state.update_data(cluster_name=cluster_name)
    await state.set_state(UserEditorState.selecting_duration)

    tariffs = await get_tariffs_for_cluster(session, cluster_name)

    builder = InlineKeyboardBuilder()
    for tariff in tariffs:
        if tariff["duration_days"] < 1:
            continue
        builder.button(
            text=f"{tariff['name']} — {tariff['price_rub']}₽",
            callback_data=f"tariff_{tariff['id']}",
        )

    builder.adjust(1)
    builder.row(build_admin_back_btn())

    await callback_query.message.edit_text(
        text=f"🕒 <b>Выберите срок действия ключа для кластера <code>{cluster_name}</code>:</b>",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(UserEditorState.selecting_duration, IsAdminFilter())
async def handle_create_key_duration(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    tg_id = data.get("tg_id", callback_query.from_user.id)

    use_country_selection = bool(MODES_CONFIG.get("COUNTRY_SELECTION_ENABLED", USE_COUNTRY_SELECTION))

    try:
        if not callback_query.data.startswith("tariff_"):
            raise ValueError("Некорректный callback_data")
        tariff_id = int(callback_query.data.replace("tariff_", ""))

        tariff = await get_tariff_by_id(session, tariff_id)
        if not tariff:
            raise ValueError("Тариф не найден.")

        duration_days = tariff["duration_days"]
        client_id = str(uuid.uuid4())
        email = await generate_random_email(session=session)
        expiry = datetime.now(tz=timezone.utc) + timedelta(days=duration_days)
        expiry_ms = int(expiry.timestamp() * 1000)

        if use_country_selection and "country" in data:
            country = data["country"]
            await create_key_on_cluster(
                country,
                tg_id,
                client_id,
                email,
                expiry_ms,
                plan=tariff_id,
                session=session,
            )

            await state.clear()
            await callback_query.message.edit_text(
                f"✅ Ключ успешно создан для страны <b>{country}</b> на {duration_days} дней.",
                reply_markup=build_editor_kb(tg_id),
            )
        elif "cluster_name" in data:
            cluster_name = data["cluster_name"]
            await create_key_on_cluster(
                cluster_name,
                tg_id,
                client_id,
                email,
                expiry_ms,
                plan=tariff_id,
                session=session,
            )

            await state.clear()
            await callback_query.message.edit_text(
                f"✅ Ключ успешно создан в кластере <b>{cluster_name}</b> на {duration_days} дней.",
                reply_markup=build_editor_kb(tg_id),
            )
        else:
            await callback_query.message.edit_text("❌ Не удалось определить источник — страна или кластер.")
    except Exception as e:
        logger.error(f"[CreateKey] Ошибка при создании ключа: {e}")
        await callback_query.message.edit_text(
            "❌ Не удалось создать ключ. Попробуйте позже.",
            reply_markup=build_editor_kb(tg_id),
        )

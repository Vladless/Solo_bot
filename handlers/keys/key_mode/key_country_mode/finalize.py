from ._common import *  # noqa: F401,F403


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

    owner = await resolve_user_optional(session, tg_id)
    if owner is None:
        await callback_query.message.answer("❌ Пользователь не найден.")
        return
    uid = owner.id

    expiry_time = expiry_time.astimezone(moscow_tz)

    old_key_details: dict[str, Any] | None = None
    if old_key_name:
        key_obj = await resolve_key(session, tg_id, old_key_name)
        old_key_name = key_obj.email if key_obj else old_key_name
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
    skip_balance_charge = bool(data.get("skip_balance_charge", False))

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
        int(selected_traffic_gb)
    else:
        int(traffic_limit_bytes / GB) if traffic_limit_bytes else 0

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
                                update(Key).where(Key.user_id == uid, Key.email == email).values(key=None)
                            )
                        elif old_server_info.panel_type.lower() == "remnawave":
                            remna_del = RemnawaveAPI(old_server_info.api_url)
                            if await remna_del.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
                                await remna_del.delete_user(client_id, username=email)
                                await session.execute(
                                    update(Key)
                                    .where(Key.user_id == uid, Key.email == email)
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
                "activeInternalSquads": [server_info.inbound_id],
                "uuid": client_id,
            }
            try:
                from database.access.resolution import panel_identity_fields

                _panel_tg, _panel_email = await panel_identity_fields(session, tg_id)
                if _panel_tg is not None:
                    user_data["telegramId"] = _panel_tg
                if _panel_email:
                    user_data["email"] = _panel_email
            except Exception as e:
                logger.debug(f"[Remnawave] поля владельца не резолвлены: {e}")
            if traffic_limit_bytes:
                user_data["trafficLimitBytes"] = traffic_limit_bytes
            if device_limit:
                user_data["hwidDeviceLimit"] = device_limit

            result = await remna.create_user(user_data)
            if not result:
                raise ValueError("❌ Ошибка при создании пользователя в Remnawave")

            client_id = result.get("vlessUuid") or result.get("uuid") or client_id

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
                    update(Key).where(Key.user_id == uid, Key.email == email).values(client_id=client_id)
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
            await session.execute(update(Key).where(Key.user_id == uid, Key.email == email).values(**update_data))
        else:
            new_key = Key(
                user_id=uid,
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
            if not is_trial and price_to_charge and not skip_balance_charge:
                debited = await update_balance(session, tg_id, -int(price_to_charge))
                if debited is None:
                    raise InsufficientFundsError("Недостаточно средств на балансе")

            if state:
                await state.update_data(skip_balance_charge=False)

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
            builder.row(
                InlineKeyboardButton(
                    text=ROUTER_BUTTON, callback_data=build_key_callback("connect_router", client_id, key_name)
                )
            )
        else:
            if use_webapp and webapp_url:
                if open_in_browser:
                    builder.row(InlineKeyboardButton(text=CONNECT_DEVICE, url=webapp_url))
                else:
                    builder.row(InlineKeyboardButton(text=CONNECT_DEVICE, web_app=WebAppInfo(url=webapp_url)))
                if tv_button_enabled:
                    builder.row(
                        InlineKeyboardButton(
                            text=TV_BUTTON, callback_data=build_key_callback("connect_tv", client_id, key_name)
                        )
                    )
            else:
                builder.row(
                    InlineKeyboardButton(
                        text=CONNECT_DEVICE,
                        callback_data=build_key_callback("connect_device", client_id, key_name),
                    )
                )
    else:
        builder.row(
            InlineKeyboardButton(
                text=CONNECT_DEVICE,
                callback_data=build_key_callback("connect_device", client_id, key_name),
            )
        )

    builder.row(InlineKeyboardButton(text=MY_SUB, callback_data=build_key_callback("view_key", client_id, key_name)))
    support_btn = await build_support_button()
    if support_btn:
        builder.row(support_btn)
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
    """Делегирует в services.clusters.check_server_availability()."""
    from services.clusters import check_server_availability as _svc_check

    result = await _svc_check(server_info, session)
    return result.available


async def _legacy_check_server_availability(server_info: dict, session: AsyncSession) -> bool:
    """Legacy — оставлено для reference."""
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

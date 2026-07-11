from ._common import *  # noqa: F401,F403
from ._common import router  # noqa: F401


@router.callback_query(F.data.startswith("change_location|"), flags={"popup": True})
async def change_location_callback(callback_query: CallbackQuery, session: Any):
    try:
        data = callback_query.data.split("|")
        if len(data) < 2:
            await callback_query.answer("❌ Некорректные данные", show_alert=True)
            return

        old_key_ref = data[1]
        key_obj = await resolve_key(session, callback_query.from_user.id, old_key_ref)
        old_key_name = key_obj.email if key_obj else old_key_ref
        record = await get_key_details(session, old_key_name)
        if not record:
            await callback_query.answer("❌ Ключ не найден", show_alert=True)
            return
        if record.get("tg_id") != callback_query.from_user.id:
            await callback_query.answer("Доступ запрещён.", show_alert=True)
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
                builder.row(
                    InlineKeyboardButton(
                        text=BACK,
                        callback_data=build_key_callback("view_key", record.get("client_id"), old_key_name),
                    )
                )
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

        if not available_servers:
            builder = InlineKeyboardBuilder()
            builder.row(
                InlineKeyboardButton(
                    text=BACK,
                    callback_data=build_key_callback("view_key", record.get("client_id"), old_key_name),
                )
            )
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
                callback_data = f"select_country|{country}|{ts}|{old_key_ref}"
                row_buttons.append(InlineKeyboardButton(text=country, callback_data=callback_data))
            builder.row(*row_buttons)

        builder.row(
            InlineKeyboardButton(
                text=BACK,
                callback_data=build_key_callback("view_key", record.get("client_id"), old_key_name),
            )
        )

        await edit_or_send_message(
            target_message=callback_query.message,
            text="🌍 Пожалуйста, выберите новую локацию для вашей подписки:",
            reply_markup=builder.as_markup(),
            media_path=None,
        )
    except Exception as e:
        logger.error(f"Ошибка при смене локации для пользователя {callback_query.from_user.id}: {e}")
        await callback_query.answer("❌ Ошибка смены локации. Попробуйте снова.", show_alert=True)

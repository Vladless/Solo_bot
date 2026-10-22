from html import escape

from database.access.resolution import UserId
from settings.texts import TRAFFIC_RESET_FAILED, TRAFFIC_RESET_PARTIAL_TEXT

from ...panel.headers import menu_text, quote, section
from ._common import *
from .edit import handle_key_edit


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_traffic"),
    IsAdminFilter(),
)
async def handle_user_traffic(
    callback_query: types.CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    user_id = callback_data.user_id
    key_obj = await resolve_callback_key(session, user_id, callback_data.data)
    if not key_obj:
        await callback_query.message.edit_text(
            menu_text("Подписка", "❌ Ключ не найден."),
            reply_markup=build_editor_kb(user_id),
        )
        return
    email = key_obj.email

    await callback_query.message.edit_text(
        menu_text("Подписка", "⏳ Получаем данные о трафике, пожалуйста, подождите...")
    )

    traffic_data = await get_user_traffic(session, user_id, email)

    if traffic_data["status"] == "error":
        await callback_query.message.edit_text(
            menu_text("Трафик подписки", traffic_data["message"]),
            reply_markup=build_editor_kb(user_id, True),
        )
        return

    total_traffic = 0
    lines = []

    for server, traffic in traffic_data["traffic"].items():
        if isinstance(traffic, str):
            lines.append(f"❌ {server}: {traffic}")
        else:
            lines.append(f"🌍 {server}: <b>{traffic} ГБ</b>")
            total_traffic += traffic

    result_text = menu_text(
        "Трафик подписки",
        f"<code>{email}</code>",
        quote("\n".join(lines)),
        quote(f"Всего: <b>{total_traffic:.2f} ГБ</b>"),
    )

    await callback_query.message.edit_text(
        result_text,
        reply_markup=build_editor_kb(user_id, True),
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_reset_traffic"),
    IsAdminFilter(),
)
async def handle_reset_traffic(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    user_id = callback_data.user_id
    key_obj = await resolve_callback_key(session, user_id, callback_data.data)
    if not key_obj:
        await callback_query.message.edit_text(
            menu_text("Подписка", "❌ Ключ не найден в базе данных."),
            reply_markup=build_editor_kb(user_id),
        )
        return

    email = key_obj.email
    cluster_id = key_obj.server_id

    try:
        result = await reset_traffic_in_cluster(cluster_id, email, session)
        text = f"✅ Трафик для ключа <b>{email}</b> успешно сброшен."
        if isinstance(result, dict) and result.get("failed"):
            text = TRAFFIC_RESET_PARTIAL_TEXT.format(
                successful_servers=escape(", ".join(result.get("succeeded") or [])),
                failed_servers=escape(", ".join(result["failed"])),
            )
        await callback_query.message.edit_text(
            menu_text("Подписка", text),
            reply_markup=build_editor_kb(user_id),
        )
    except Exception as e:
        logger.error(f"Ошибка при сбросе трафика: {e}")
        await callback_query.message.edit_text(
            menu_text("Подписка", TRAFFIC_RESET_FAILED),
            reply_markup=build_editor_kb(user_id),
        )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_freeze"),
    IsAdminFilter(),
)
async def handle_admin_freeze_subscription(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    user_id = callback_data.user_id
    key_obj = await resolve_callback_key(session, user_id, callback_data.data)
    if not key_obj:
        await callback_query.message.edit_text(
            text=menu_text("Подписка", "❌ Подписка не найдена."),
            reply_markup=build_editor_kb(user_id),
        )
        return
    email = key_obj.email

    try:
        record = await get_key_details(session, email)
        if not record or record.get("user_id") != key_obj.user_id or record.get("client_id") != key_obj.client_id:
            await callback_query.message.edit_text(
                text=menu_text("Подписка", "❌ Подписка не найдена."),
                reply_markup=build_editor_kb(user_id),
            )
            return

        client_id = record["client_id"]
        cluster_id = record["server_id"]

        result = await toggle_client_on_cluster(cluster_id, email, client_id, enable=False, session=session)
        if result["status"] != "success":
            text_error = menu_text(
                "Подписка",
                "❌ Отключить подписку не удалось.",
                section("⚠️ Причина", str(result.get("error") or result.get("results"))),
            )
            await callback_query.message.edit_text(
                text_error,
                reply_markup=build_editor_kb(user_id, True),
            )
            return

        now_ms = int(time.time() * 1000)
        time_left = record["expiry_time"] - now_ms
        if time_left < 0:
            time_left = 0

        await mark_key_as_frozen(session, UserId(record["user_id"]), client_id, time_left)
        session.expire_all()

        await callback_query.answer(menu_text("Подписка", "✅ Подписка отключена"))

        await handle_key_edit(
            callback_query=callback_query,
            callback_data=callback_data,
            session=session,
            update=False,
        )
    except Exception as e:
        await handle_error(user_id, callback_query, f"Ошибка при отключении подписки: {e}")


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_unfreeze"),
    IsAdminFilter(),
)
async def handle_admin_unfreeze_subscription(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    user_id = callback_data.user_id
    key_obj = await resolve_callback_key(session, user_id, callback_data.data)
    if not key_obj:
        await callback_query.message.edit_text(
            text=menu_text("Подписка", "❌ Подписка не найдена."),
            reply_markup=build_editor_kb(user_id),
        )
        return
    email = key_obj.email

    try:
        record = await get_key_details(session, email)
        if not record or record.get("user_id") != key_obj.user_id or record.get("client_id") != key_obj.client_id:
            await callback_query.message.edit_text(
                text=menu_text("Подписка", "❌ Подписка не найдена."),
                reply_markup=build_editor_kb(user_id),
            )
            return

        client_id = record["client_id"]
        cluster_id = record["server_id"]

        result = await toggle_client_on_cluster(cluster_id, email, client_id, enable=True, session=session)
        if result["status"] != "success":
            text_error = menu_text(
                "Подписка",
                "❌ Включить подписку не удалось.",
                section("⚠️ Причина", str(result.get("error") or result.get("results"))),
            )
            await callback_query.message.edit_text(
                text_error,
                reply_markup=build_editor_kb(user_id, True),
            )
            return

        tariff = await get_tariff_by_id(session, record["tariff_id"]) if record.get("tariff_id") else None
        if not tariff:
            total_gb = 0
            hwid_limit = 0
        else:
            total_gb = int(tariff.get("traffic_limit") or 0)
            hwid_limit = int(tariff.get("device_limit") or 0)

        if record.get("current_traffic_limit") is not None:
            total_gb = record["current_traffic_limit"]
        if record.get("current_device_limit") is not None:
            hwid_limit = record["current_device_limit"]

        now_ms = int(time.time() * 1000)
        leftover = record["expiry_time"]
        if leftover < 0:
            leftover = 0
        new_expiry_time = leftover if leftover > now_ms else now_ms + leftover

        renewed = await renew_key_in_cluster(
            cluster_id=cluster_id,
            email=email,
            client_id=client_id,
            new_expiry_time=new_expiry_time,
            total_gb=total_gb,
            session=session,
            hwid_device_limit=hwid_limit,
            reset_traffic=False,
            plan=record.get("tariff_id"),
        )

        if not renewed:
            raise RuntimeError("Не удалось восстановить срок подписки на панели")

        await mark_key_as_unfrozen(session, UserId(record["user_id"]), client_id, new_expiry_time)
        session.expire_all()

        await callback_query.answer(menu_text("Подписка", "✅ Подписка включена"))

        await handle_key_edit(
            callback_query=callback_query,
            callback_data=callback_data,
            session=session,
            update=False,
        )
    except Exception as e:
        await handle_error(user_id, callback_query, f"Ошибка при включении подписки: {e}")

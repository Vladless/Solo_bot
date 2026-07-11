import html

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from filters.admin import IsAdminFilter
from panels.remnawave_runtime import (
    invalidate_remnawave_profile,
    resolve_remnawave_api_url,
    with_remnawave_api,
)
from services.users_utils import resolve_admin_key

from .keyboard import AdminUserEditorCallback, build_editor_kb, build_hwid_menu_kb


router = Router()

DEVICES_PER_PAGE = 3


def _format_device_block(idx: int, device: dict) -> str:
    hwid = html.escape(str(device.get("hwid") or "—"))
    model = html.escape(str(device.get("deviceModel") or "—"))
    platform = html.escape(str(device.get("platform") or "—"))
    os_version = html.escape(str(device.get("osVersion") or "—"))
    user_agent = html.escape(str(device.get("userAgent") or "—"))
    created_raw = str(device.get("createdAt") or "")[:19].replace("T", " ")
    updated_raw = str(device.get("updatedAt") or "")[:19].replace("T", " ")
    created = html.escape(created_raw or "—")
    updated = html.escape(updated_raw or "—")
    return (
        f"<blockquote expandable><b>#{idx} · {model}</b>\n"
        f"📟 <code>{hwid}</code>\n"
        f"🧠 {platform} · {os_version}\n"
        f"🌐 <i>{user_agent}</i>\n"
        f"🕓 Создано: {created}\n"
        f"🔄 Обновлено: {updated}</blockquote>"
    )


async def _render_admin_devices(
    callback_query: CallbackQuery,
    session: AsyncSession,
    key_ref: str,
    tg_id: int,
    page: int,
) -> None:
    key_obj = await resolve_admin_key(session, tg_id, key_ref)
    if not key_obj:
        await callback_query.message.edit_text("🚫 Не удалось найти ключ.", reply_markup=build_editor_kb(tg_id))
        return
    client_id = key_obj.client_id

    remna_api_url = await resolve_remnawave_api_url(session, "", fallback_any=True)
    if not remna_api_url:
        await callback_query.message.edit_text(
            "🚫 Нет доступного сервера Remnawave.",
            reply_markup=build_editor_kb(tg_id),
        )
        return

    async def _fetch(api):
        user_info = await api.get_user_by_uuid(client_id)
        devices = await api.get_user_hwid_devices(client_id)
        return user_info, devices

    result = await with_remnawave_api(session, "", _fetch, fallback_any=True, timeout_sec=8.0)
    if result is None:
        await callback_query.message.edit_text("❌ Ошибка авторизации в Remnawave.")
        return

    user_info, devices = result
    devices = devices or []

    status_emoji = "⚪️"
    status_text = "Не найден"
    online_at_str = "—"
    first_connected_str = "—"
    last_node_uuid = "—"

    if user_info:
        is_online = bool(user_info.get("isOnline"))
        status_emoji = "🟢" if is_online else "⚪️"
        status_text = "Онлайн" if is_online else "Офлайн"

        online_at = user_info.get("onlineAt")
        if online_at:
            online_at_str = online_at[:19].replace("T", " ")

        first_connected_at = user_info.get("firstConnectedAt")
        if first_connected_at:
            first_connected_str = first_connected_at[:19].replace("T", " ")

        last_node_uuid_val = user_info.get("lastConnectedNodeUuid")
        if last_node_uuid_val:
            last_node_uuid = last_node_uuid_val

    total = len(devices)
    header = (
        "💻 <b>HWID устройства</b>\n\n"
        f"{status_emoji} <b>Статус:</b> {status_text}\n"
        f"└ 🕓 <b>Онлайн был:</b> {html.escape(online_at_str)}\n"
        f"└ 🚀 <b>Первое подключение:</b> {html.escape(first_connected_str)}\n"
        f"└ 🛰 <b>Нода:</b> <code>{html.escape(last_node_uuid)}</code>\n\n"
    )

    if total == 0:
        text = header + "🔌 Нет привязанных устройств."
        await callback_query.message.edit_text(
            text,
            reply_markup=build_hwid_menu_kb(key_ref, tg_id, page=0, total_pages=0, devices_on_page=0),
        )
        return

    total_pages = (total + DEVICES_PER_PAGE - 1) // DEVICES_PER_PAGE
    page = max(0, min(page, total_pages - 1))
    start = page * DEVICES_PER_PAGE
    page_devices = devices[start : start + DEVICES_PER_PAGE]

    body = "\n".join(_format_device_block(start + i + 1, dev) for i, dev in enumerate(page_devices))
    text = header + f"🔗 Привязано устройств: <b>{total}</b>\n\n" + body

    await callback_query.message.edit_text(
        text,
        reply_markup=build_hwid_menu_kb(
            key_ref,
            tg_id,
            page=page,
            total_pages=total_pages,
            devices_on_page=len(page_devices),
            devices_per_page=DEVICES_PER_PAGE,
        ),
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_hwid_menu"),
    IsAdminFilter(),
)
async def handle_hwid_menu(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    await _render_admin_devices(callback_query, session, str(callback_data.data), callback_data.tg_id, 0)


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_hwid_page"),
    IsAdminFilter(),
)
async def handle_hwid_page(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    parts = str(callback_data.data or "").split("|")
    key_ref = parts[0]
    try:
        page = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        page = 0
    await _render_admin_devices(callback_query, session, key_ref, callback_data.tg_id, page)


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_hwid_unbind"),
    IsAdminFilter(),
    flags={"popup": True},
)
async def handle_hwid_unbind(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    parts = str(callback_data.data or "").split("|")
    key_ref = parts[0]
    try:
        page = int(parts[1]) if len(parts) > 1 else 0
        idx = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        page = 0
        idx = 0

    tg_id = callback_data.tg_id
    key_obj = await resolve_admin_key(session, tg_id, key_ref)
    if not key_obj:
        await callback_query.answer("🚫 Не удалось найти ключ.", show_alert=True)
        return
    client_id = key_obj.client_id

    async def _delete(api):
        devices = await api.get_user_hwid_devices(client_id) or []
        target_idx = page * DEVICES_PER_PAGE + idx
        if target_idx >= len(devices):
            return None
        target_hwid = devices[target_idx].get("hwid")
        if not target_hwid:
            return False
        return await api.delete_user_hwid_device(client_id, target_hwid)

    result = await with_remnawave_api(session, "", _delete, fallback_any=True, timeout_sec=10.0)
    if result is None:
        await callback_query.answer("❌ Устройство не найдено.", show_alert=True)
    elif result is False:
        await callback_query.answer("❌ Не удалось отвязать устройство.", show_alert=True)
    else:
        await invalidate_remnawave_profile(session, "", str(client_id), fallback_any=True)
        await callback_query.answer("✅ Устройство отвязано.")

    await _render_admin_devices(callback_query, session, key_ref, tg_id, page)

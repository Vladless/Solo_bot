from typing import Any

from aiogram import F, Router, types
from aiogram.types import CallbackQuery

from database import delete_server, get_servers
from filters.admin import IsAdminFilter

from ..panel.keyboard import build_admin_back_kb
from .keyboard import (
    AdminServerCallback,
    build_delete_server_kb,
    build_manage_server_kb,
)


router = Router()


@router.callback_query(AdminServerCallback.filter(F.action == "manage"), IsAdminFilter())
async def handle_server_manage(callback_query: CallbackQuery, callback_data: AdminServerCallback):
    server_name = callback_data.data
    servers = await get_servers()

    cluster_name, server = next(
        ((c, s) for c, cs in servers.items() for s in cs if s["server_name"] == server_name), (None, None)
    )

    if server:
        api_url = server["api_url"]
        subscription_url = server["subscription_url"]
        inbound_id = server["inbound_id"]

        text = (
            f"<b>🔧 Информация о сервере {server_name}:</b>\n\n"
            f"<b>📡 API URL:</b> {api_url}\n"
            f"<b>🌐 Subscription URL:</b> {subscription_url}\n"
            f"<b>🔑 Inbound ID:</b> {inbound_id}"
        )

        await callback_query.message.edit_text(
            text=text,
            reply_markup=build_manage_server_kb(server_name, cluster_name),
        )
    else:
        await callback_query.message.edit_text(text="❌ Сервер не найден.")


@router.callback_query(AdminServerCallback.filter(F.action == "delete"), IsAdminFilter())
async def handle_server_delete(callback_query: CallbackQuery, callback_data: AdminServerCallback):
    server_name = callback_data.data

    await callback_query.message.edit_text(
        text=f"🗑️ Вы уверены, что хотите удалить сервер {server_name}?",
        reply_markup=build_delete_server_kb(server_name),
    )


@router.callback_query(AdminServerCallback.filter(F.action == "delete_confirm"), IsAdminFilter())
async def handle_server_delete_confirm(
    callback_query: types.CallbackQuery, callback_data: AdminServerCallback, session: Any
):
    server_name = callback_data.data

    await delete_server(server_name, session)

    await callback_query.message.edit_text(
        text=f"🗑️ Сервер {server_name} успешно удален.", reply_markup=build_admin_back_kb("clusters")
    )

from typing import Any

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from handlers.buttons import BACK

from database import delete_server, get_servers
from filters.admin import IsAdminFilter

from ..panel.keyboard import build_admin_back_kb
from .keyboard import (
    AdminServerCallback,
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
async def process_callback_delete_server(
    callback_query: CallbackQuery, callback_data: AdminServerCallback, state: FSMContext, session: Any
):
    from ..clusters.clusters_handler import AdminClusterStates

    server_name = callback_data.data

    servers = await get_servers(session)
    cluster_name = None
    for c_name, server_list in servers.items():
        for server in server_list:
            if server["server_name"] == server_name:
                cluster_name = c_name
                break
        if cluster_name:
            break

    if not cluster_name:
        await callback_query.message.edit_text(
            text=f"❌ Не удалось найти кластер для сервера '{server_name}'.",
            reply_markup=build_admin_back_kb("clusters"),
        )
        return

    keys_count = await session.fetchval(
        "SELECT COUNT(*) FROM keys WHERE server_id = $1",
        server_name
    )

    if keys_count > 0:
        await state.update_data(server_name=server_name, cluster_name=cluster_name)

        all_servers = await session.fetch(
            """
            SELECT server_name, (SELECT COUNT(*) FROM keys WHERE server_id = servers.server_name) as key_count
            FROM servers
            WHERE server_name != $1
            """,
            server_name
        )

        if all_servers:
            builder = InlineKeyboardBuilder()
            for server in all_servers:
                builder.row(
                    InlineKeyboardButton(
                        text=f"{server['server_name']} ({server['key_count']})",
                        callback_data=f"transfer_to_server|{server['server_name']}|{server_name}"
                    )
                )
            builder.row(
                InlineKeyboardButton(
                    text=BACK,
                    callback_data=AdminServerCallback(action="manage", data=server_name).pack()
                )
            )

            await callback_query.message.edit_text(
                text=f"⚠️ На сервере '{server_name}' есть {keys_count} ключей. Выберите сервер для переноса ключей:",
                reply_markup=builder.as_markup(),
            )
            await state.set_state(AdminClusterStates.waiting_for_server_transfer)
            return

    remaining_servers = await session.fetchval(
        "SELECT COUNT(*) FROM servers WHERE cluster_name = $1 AND server_name != $2",
        cluster_name,
        server_name
    )

    if remaining_servers == 0:
        other_clusters = await session.fetch(
            "SELECT DISTINCT cluster_name FROM servers WHERE cluster_name != $1",
            cluster_name
        )

        if other_clusters:
            cluster_keys_count = await session.fetchval(
                "SELECT COUNT(*) FROM keys WHERE server_id = $1",
                cluster_name
            )

            if cluster_keys_count > 0:
                await state.update_data(server_name=server_name, cluster_name=cluster_name)

                all_clusters = await session.fetch(
                    """
                    SELECT cluster_name, (SELECT COUNT(*) FROM keys WHERE server_id = servers.cluster_name) as key_count
                    FROM servers
                    WHERE cluster_name != $1
                    GROUP BY cluster_name
                    """,
                    cluster_name
                )

                builder = InlineKeyboardBuilder()
                for cluster in all_clusters:
                    builder.row(
                        InlineKeyboardButton(
                            text=f"{cluster['cluster_name']} ({cluster['key_count']})",
                            callback_data=f"transfer_to_cluster|{cluster['cluster_name']}|{cluster_name}|{server_name}"
                        )
                    )
                builder.row(
                    InlineKeyboardButton(
                        text=BACK,
                        callback_data=AdminServerCallback(action="manage", data=server_name).pack()
                    )
                )

                await callback_query.message.edit_text(
                    text=f"⚠️ Это последний сервер в кластере '{cluster_name}'. На кластере есть {cluster_keys_count} ключей. Выберите кластер для переноса ключей:",
                    reply_markup=builder.as_markup(),
                )
                await state.set_state(AdminClusterStates.waiting_for_cluster_transfer)
                return

        await session.execute(
            "DELETE FROM servers WHERE cluster_name = $1 AND server_name = $2",
            cluster_name,
            server_name
        )
        await callback_query.message.edit_text(
            text=f"✅ Сервер '{server_name}' удален. Кластер '{cluster_name}' также удален, так как в нем не осталось серверов.",
            reply_markup=build_admin_back_kb("clusters"),
        )
    else:
        await session.execute(
            "DELETE FROM servers WHERE cluster_name = $1 AND server_name = $2",
            cluster_name,
            server_name
        )
        await callback_query.message.edit_text(
            text=f"✅ Сервер '{server_name}' удален.",
            reply_markup=build_admin_back_kb("clusters"),
        )

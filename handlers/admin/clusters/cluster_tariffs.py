from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy import and_, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_servers
from database.models import Server, ServerSpecialgroup, ServerSubgroup, Tariff
from database.servers import has_legacy_subgroup_bindings
from filters.admin import IsAdminFilter
from handlers.utils import ALLOWED_GROUP_CODES
from logger import logger

from .base import router
from .keyboard import (
    AdminClusterCallback,
    build_attach_tariff_kb,
    build_legacy_reset_kb,
    build_manage_cluster_kb,
    build_select_group_servers_kb,
    build_select_subgroup_servers_kb,
    build_tariff_group_selection_for_servers_kb,
    build_tariff_group_selection_kb,
    build_tariff_selection_kb,
)


@router.callback_query(AdminClusterCallback.filter(F.action == "set_tariff"), IsAdminFilter())
async def show_tariff_group_selection(
    callback: CallbackQuery, callback_data: AdminClusterCallback, session: AsyncSession
):
    cluster_name = callback_data.data
    result = await session.execute(
        select(Tariff.id, Tariff.group_code).where(Tariff.group_code.isnot(None)).distinct(Tariff.group_code)
    )
    rows = result.mappings().all()
    groups = [(r["id"], r["group_code"]) for r in rows]

    if not groups:
        await callback.message.edit_text("❌ Нет доступных тарифных групп.")
        return

    await callback.message.edit_text(
        f"<b>💸 Выберите тарифную группу для кластера <code>{cluster_name}</code>:</b>",
        reply_markup=build_tariff_group_selection_kb(cluster_name, groups),
    )


@router.callback_query(AdminClusterCallback.filter(F.action == "apply_tariff_group"), IsAdminFilter())
async def apply_tariff_group(callback: CallbackQuery, callback_data: AdminClusterCallback, session: AsyncSession):
    try:
        cluster_name, group_id = callback_data.data.split("|", 1)
        group_id = int(group_id)

        result = await session.execute(select(Tariff.group_code).where(Tariff.id == group_id))
        row = result.mappings().first()

        if not row:
            await callback.message.edit_text("❌ Тарифная группа не найдена.")
            return

        group_code = row["group_code"]

        await session.execute(update(Server).where(Server.cluster_name == cluster_name).values(tariff_group=group_code))

        servers = await get_servers(session=session, include_enabled=True)
        cluster_servers = servers.get(cluster_name, [])

        await callback.message.edit_text(
            f"✅ Для кластера <code>{cluster_name}</code> установлена тарифная группа: <b>{group_code}</b>",
            reply_markup=build_manage_cluster_kb(cluster_servers, cluster_name),
        )

    except Exception as e:
        logger.error(f"Ошибка при применении тарифной группы: {e}")
        await callback.message.edit_text("❌ Произошла ошибка при установке тарифной группы.")


@router.callback_query(AdminClusterCallback.filter(F.action == "set_subgroup"), IsAdminFilter())
async def show_servers_for_tariffs(
    callback: CallbackQuery, callback_data: AdminClusterCallback, session: AsyncSession, state: FSMContext
):
    cluster_name = callback_data.data
    servers = await get_servers(session=session, include_enabled=True)
    cluster_servers = servers.get(cluster_name, [])

    server_ids = [s.get("server_id") for s in cluster_servers if s.get("server_id")]
    if server_ids and await has_legacy_subgroup_bindings(session, server_ids):
        await callback.message.edit_text(
            f"<b>⚠️ Обнаружены привязки старого формата</b>\n\n"
            f"Кластер <code>{cluster_name}</code> содержит привязки по названиям подгрупп.\n"
            f"Для использования новой системы необходимо сбросить текущие привязки.\n\n"
            f"<i>После сброса вы сможете привязать тарифы по ID.</i>",
            reply_markup=build_legacy_reset_kb(cluster_name),
        )
        return

    data = await state.get_data()
    selected = set(data.get(f"subgrp_sel:{cluster_name}", []))
    await callback.message.edit_text(
        f"<b>📋 Выберите серверы для привязки тарифов</b>\n<i>Кластер: {cluster_name}</i>",
        reply_markup=build_select_subgroup_servers_kb(cluster_name, cluster_servers, selected),
    )


@router.callback_query(
    AdminClusterCallback.filter(F.action == "toggle_server_subgroup"), IsAdminFilter(), flags={"popup": True}
)
async def toggle_server_for_tariffs(
    callback: CallbackQuery, callback_data: AdminClusterCallback, session: AsyncSession, state: FSMContext
):
    cluster_name, idx_str = callback_data.data.split("|", 1)
    i = int(idx_str)
    servers = await get_servers(session=session, include_enabled=True)
    cluster_servers = servers.get(cluster_name, [])
    names = []
    for s in cluster_servers:
        if isinstance(s, str):
            names.append(s)
        elif isinstance(s, dict):
            names.append(s.get("server_name") or s.get("name") or str(s))
        else:
            names.append(getattr(s, "server_name", None) or getattr(s, "name", None) or str(s))
    if i < 0 or i >= len(names):
        await callback.answer("Сервер не найден", show_alert=True)
        return
    server_name = names[i]
    key = f"subgrp_sel:{cluster_name}"
    data = await state.get_data()
    selected = set(data.get(key, []))
    if server_name in selected:
        selected.remove(server_name)
    else:
        selected.add(server_name)
    await state.update_data({key: list(selected)})
    await callback.message.edit_text(
        f"<b>📋 Выберите серверы для привязки тарифов</b>\n<i>Кластер: {cluster_name}</i>",
        reply_markup=build_select_subgroup_servers_kb(cluster_name, cluster_servers, selected),
    )


@router.callback_query(AdminClusterCallback.filter(F.action == "reset_subgroup_selection"), IsAdminFilter())
async def reset_tariff_selection(
    callback: CallbackQuery, callback_data: AdminClusterCallback, session: AsyncSession, state: FSMContext
):
    cluster_name = callback_data.data
    servers = await get_servers(session=session, include_enabled=True)
    cluster_servers = servers.get(cluster_name, [])
    await state.update_data({
        f"subgrp_sel:{cluster_name}": [],
        f"tariff_sel:{cluster_name}": [],
    })
    await callback.message.edit_text(
        f"<b>📋 Выберите серверы для привязки тарифов</b>\n<i>Кластер: {cluster_name}</i>",
        reply_markup=build_select_subgroup_servers_kb(cluster_name, cluster_servers, set()),
    )


@router.callback_query(
    AdminClusterCallback.filter(F.action == "choose_subgroup"), IsAdminFilter(), flags={"popup": True}
)
async def choose_tariffs(
    callback: CallbackQuery, callback_data: AdminClusterCallback, session: AsyncSession, state: FSMContext
):
    cluster_name = callback_data.data
    key = f"subgrp_sel:{cluster_name}"
    data = await state.get_data()
    selected_servers = set(data.get(key, []))
    if not selected_servers:
        await callback.answer("Сначала выберите хотя бы один сервер", show_alert=True)
        return

    res = await session.execute(select(Server.tariff_group).where(Server.cluster_name == cluster_name).distinct())
    group_codes = [r[0] for r in res.fetchall() if r[0]]
    if not group_codes:
        await callback.answer("Сначала установите тарифную группу для этого кластера", show_alert=True)
        return

    group_code = group_codes[0]

    result = await session.execute(
        select(Tariff)
        .where(Tariff.group_code == group_code, Tariff.is_active.is_(True))
        .order_by(Tariff.subgroup_title.nulls_last(), Tariff.sort_order, Tariff.id)
    )
    tariffs = result.scalars().all()

    if not tariffs:
        await callback.message.edit_text("❌ Для этой группы нет доступных тарифов.")
        return

    servers_q = await session.execute(select(Server.id).where(Server.server_name.in_(selected_servers)))
    server_ids = [row[0] for row in servers_q.fetchall()]

    current_bindings_q = await session.execute(
        select(ServerSubgroup.subgroup_title)
        .where(ServerSubgroup.server_id.in_(server_ids))
        .where(ServerSubgroup.subgroup_title.regexp_match(r"^\d+$"))
    )
    current_tariff_ids = {int(row[0]) for row in current_bindings_q.fetchall()}

    await state.update_data({f"tariff_sel:{cluster_name}": list(current_tariff_ids)})

    await callback.message.edit_text(
        f"<b>📋 Выберите тарифы для {len(selected_servers)} сервер(а/ов)</b>\n<i>Кластер: {cluster_name}</i>",
        reply_markup=build_tariff_selection_kb(cluster_name, tariffs, current_tariff_ids),
    )


@router.callback_query(AdminClusterCallback.filter(F.action == "toggle_tariff"), IsAdminFilter())
async def toggle_tariff_selection(
    callback: CallbackQuery, callback_data: AdminClusterCallback, session: AsyncSession, state: FSMContext
):
    cluster_name, tariff_id_str = callback_data.data.split("|", 1)
    tariff_id = int(tariff_id_str)

    key = f"tariff_sel:{cluster_name}"
    data = await state.get_data()
    selected_tariffs = set(data.get(key, []))

    if tariff_id in selected_tariffs:
        selected_tariffs.remove(tariff_id)
    else:
        selected_tariffs.add(tariff_id)

    await state.update_data({key: list(selected_tariffs)})

    res = await session.execute(select(Server.tariff_group).where(Server.cluster_name == cluster_name).distinct())
    group_codes = [r[0] for r in res.fetchall() if r[0]]
    if not group_codes:
        return

    result = await session.execute(
        select(Tariff)
        .where(Tariff.group_code == group_codes[0], Tariff.is_active.is_(True))
        .order_by(Tariff.subgroup_title.nulls_last(), Tariff.sort_order, Tariff.id)
    )
    tariffs = result.scalars().all()

    selected_servers = set(data.get(f"subgrp_sel:{cluster_name}", []))

    await callback.message.edit_text(
        f"<b>📋 Выберите тарифы для {len(selected_servers)} сервер(а/ов)</b>\n<i>Кластер: {cluster_name}</i>",
        reply_markup=build_tariff_selection_kb(cluster_name, tariffs, selected_tariffs),
    )


@router.callback_query(AdminClusterCallback.filter(F.action == "apply_tariffs"), IsAdminFilter(), flags={"popup": True})
async def apply_tariffs(
    callback: CallbackQuery, callback_data: AdminClusterCallback, session: AsyncSession, state: FSMContext
):
    try:
        cluster_name = callback_data.data
        data = await state.get_data()

        selected_servers = set(data.get(f"subgrp_sel:{cluster_name}", []))
        selected_tariffs = set(data.get(f"tariff_sel:{cluster_name}", []))

        if not selected_servers:
            await callback.answer("Не выбраны серверы", show_alert=True)
            return

        servers_q = await session.execute(
            select(Server.id, Server.server_name, Server.tariff_group).where(Server.server_name.in_(selected_servers))
        )
        servers_data = servers_q.fetchall()
        server_ids = [row[0] for row in servers_data]
        group_code = servers_data[0][2] if servers_data else "standard"

        if not server_ids:
            await callback.answer("Серверы не найдены", show_alert=True)
            return

        selected_tariff_strs = {str(tid) for tid in selected_tariffs}

        await session.execute(
            delete(ServerSubgroup)
            .where(ServerSubgroup.server_id.in_(server_ids))
            .where(ServerSubgroup.subgroup_title.regexp_match(r"^\d+$"))
            .where(ServerSubgroup.subgroup_title.notin_(selected_tariff_strs))
        )

        for tariff_id in selected_tariffs:
            tariff_id_str = str(tariff_id)

            existing_q = await session.execute(
                select(ServerSubgroup.server_id)
                .where(ServerSubgroup.server_id.in_(server_ids))
                .where(ServerSubgroup.subgroup_title == tariff_id_str)
            )
            already = {r[0] for r in existing_q.fetchall()}
            to_insert = [sid for sid in server_ids if sid not in already]

            if to_insert:
                session.add_all([
                    ServerSubgroup(server_id=sid, group_code=group_code, subgroup_title=tariff_id_str)
                    for sid in to_insert
                ])

        await state.update_data({
            f"subgrp_sel:{cluster_name}": [],
            f"tariff_sel:{cluster_name}": [],
        })

        servers = await get_servers(session, include_enabled=True)
        cluster_servers = servers.get(cluster_name, [])

        all_tariff_ids = set()
        for s in cluster_servers:
            all_tariff_ids.update(s.get("tariff_ids") or [])

        tariffs_cache = {}
        if all_tariff_ids:
            result = await session.execute(select(Tariff).where(Tariff.id.in_(all_tariff_ids)))
            for t in result.scalars().all():
                tariffs_cache[t.id] = {
                    "id": t.id,
                    "name": t.name,
                    "subgroup_title": t.subgroup_title,
                    "group_code": t.group_code,
                }

        text = render_attach_tariff_menu_text(cluster_name, cluster_servers, tariffs_cache)
        await callback.message.edit_text(
            text=text,
            reply_markup=build_attach_tariff_kb(cluster_name),
            disable_web_page_preview=True,
        )

    except Exception as e:
        logger.error(f"Ошибка при применении тарифов: {e}")
        await callback.message.edit_text("❌ Произошла ошибка при назначении тарифов.")


@router.callback_query(
    AdminClusterCallback.filter(F.action == "reset_cluster_subgroups"), IsAdminFilter(), flags={"popup": True}
)
async def reset_cluster_subgroups(callback: CallbackQuery, callback_data: AdminClusterCallback, session: AsyncSession):
    try:
        cluster_name = callback_data.data

        res = await session.execute(select(Server.id).where(Server.cluster_name == cluster_name))
        server_ids = [row[0] for row in res.fetchall()]
        if not server_ids:
            await callback.answer("В кластере нет серверов", show_alert=True)
            return

        await session.execute(delete(ServerSubgroup).where(ServerSubgroup.server_id.in_(server_ids)))

        servers = await get_servers(session=session, include_enabled=True)
        cluster_servers = servers.get(cluster_name, [])

        await callback.message.edit_text(
            f"✅ Все подгруппы тарифов сброшены для кластера <b>{cluster_name}</b>.",
            reply_markup=build_manage_cluster_kb(cluster_servers, cluster_name),
        )
    except Exception as e:
        logger.error(f"Ошибка при сбросе подгрупп для кластера {cluster_name}: {e}")
        await callback.message.edit_text("❌ Не удалось сбросить подгруппы.")


def render_attach_tariff_menu_text(
    cluster_name: str, cluster_servers: list[dict], tariffs_cache: dict[int, dict] | None = None
) -> str:
    tariff_map: dict[int, list[str]] = {}
    legacy_map: dict[str, list[str]] = {}

    for s in cluster_servers:
        server_name = s["server_name"]

        for tid in s.get("tariff_ids") or []:
            tariff_map.setdefault(tid, []).append(server_name)

        for sg in s.get("tariff_subgroups") or []:
            legacy_map.setdefault(sg, []).append(server_name)

    allowed = tuple(ALLOWED_GROUP_CODES)
    spec_map: dict[str, list[str]] = {k: [] for k in allowed}
    for s in cluster_servers:
        for g in s.get("special_groups") or []:
            if g in spec_map:
                spec_map[g].append(s["server_name"])

    lines = [f"<b>🧩 Привязки тарифов • {cluster_name}</b>"]

    lines.append("\n<b>📋 Тарифы:</b>")
    if tariff_map and tariffs_cache:
        grouped: dict[str | None, list[tuple[int, str, list[str]]]] = {}
        for tid, servers in tariff_map.items():
            tariff = tariffs_cache.get(tid, {})
            subgroup = tariff.get("subgroup_title")
            name = tariff.get("name", f"ID:{tid}")
            grouped.setdefault(subgroup, []).append((tid, name, servers))

        tariff_lines = []
        subgroups_sorted = sorted(grouped.keys(), key=lambda x: (x is None, x or ""))

        for subgroup in subgroups_sorted:
            tariffs_list = grouped[subgroup]
            if subgroup:
                tariff_lines.append(f"<b>{subgroup}</b>")
                for tid, name, servers in sorted(tariffs_list, key=lambda x: x[1]):
                    servers_str = ", ".join(sorted(set(servers)))
                    tariff_lines.append(f"  └ {name}: {servers_str}")
            else:
                for tid, name, servers in sorted(tariffs_list, key=lambda x: x[1]):
                    servers_str = ", ".join(sorted(set(servers)))
                    tariff_lines.append(f"• {name}: {servers_str}")

        lines.append("<blockquote>" + "\n".join(tariff_lines) + "</blockquote>")
    elif tariff_map:
        tariff_lines = []
        for tid, servers in sorted(tariff_map.items()):
            servers_str = ", ".join(sorted(set(servers)))
            tariff_lines.append(f"• ID:{tid}: {servers_str}")
        lines.append("<blockquote>" + "\n".join(tariff_lines) + "</blockquote>")
    else:
        lines.append("<blockquote>— нет привязок</blockquote>")

    if legacy_map:
        lines.append("\n<b>⚠️ Старые привязки (по названию):</b>")
        legacy_lines = []
        for k in sorted(legacy_map):
            servers_list = ", ".join(sorted(set(legacy_map[k])))
            legacy_lines.append(f"• <b>{k}</b>: {servers_list}")
        lines.append("<blockquote>" + "\n".join(legacy_lines) + "</blockquote>")
        lines.append("<i>Рекомендуется сбросить и настроить заново</i>")

    lines.append("\n<b>🎁 Спецгруппы:</b>")
    has_spec = any(spec_map[k] for k in allowed)
    if has_spec:
        spec_lines = []
        for k in allowed:
            vals = sorted(set(spec_map[k]))
            spec_lines.append(f"• <b>{k}</b>: {', '.join(vals) if vals else '—'}")
        lines.append("<blockquote>" + "\n".join(spec_lines) + "</blockquote>")
    else:
        lines.append("<blockquote>— нет привязок</blockquote>")

    return "\n".join(lines)


@router.callback_query(AdminClusterCallback.filter(F.action == "attach_tariff_menu"), IsAdminFilter())
async def handle_attach_tariff_menu(callback: CallbackQuery, session: AsyncSession):
    packed = AdminClusterCallback.unpack(callback.data)
    cluster_name = packed.data

    servers = await get_servers(session, include_enabled=True)
    cluster_servers = servers.get(cluster_name, [])

    all_tariff_ids = set()
    for s in cluster_servers:
        all_tariff_ids.update(s.get("tariff_ids") or [])

    tariffs_cache = {}
    if all_tariff_ids:
        result = await session.execute(select(Tariff).where(Tariff.id.in_(all_tariff_ids)))
        for t in result.scalars().all():
            tariffs_cache[t.id] = {
                "id": t.id,
                "name": t.name,
                "subgroup_title": t.subgroup_title,
                "group_code": t.group_code,
            }

    text = render_attach_tariff_menu_text(cluster_name, cluster_servers, tariffs_cache)
    await callback.message.edit_text(
        text=text,
        reply_markup=build_attach_tariff_kb(cluster_name),
        disable_web_page_preview=True,
    )


@router.callback_query(AdminClusterCallback.filter(F.action == "set_group"), IsAdminFilter())
async def show_servers_for_group(
    callback: CallbackQuery, callback_data: AdminClusterCallback, session: AsyncSession, state: FSMContext
):
    cluster_name = callback_data.data
    servers = await get_servers(session=session, include_enabled=True)
    cluster_servers = servers.get(cluster_name, [])
    data = await state.get_data()
    selected = set(data.get(f"grp_sel:{cluster_name}", []))
    await callback.message.edit_text(
        f"<b>🗂 Выберите серверы в кластере <code>{cluster_name}</code> для назначения тарифной группы:</b>",
        reply_markup=build_select_group_servers_kb(cluster_name, cluster_servers, selected),
    )


@router.callback_query(
    AdminClusterCallback.filter(F.action == "toggle_server_group"), IsAdminFilter(), flags={"popup": True}
)
async def toggle_server_for_group(
    callback: CallbackQuery, callback_data: AdminClusterCallback, session: AsyncSession, state: FSMContext
):
    cluster_name, idx_str = callback_data.data.split("|", 1)
    i = int(idx_str)
    servers = await get_servers(session=session, include_enabled=True)
    cluster_servers = servers.get(cluster_name, [])
    names = []
    for s in cluster_servers:
        if isinstance(s, str):
            names.append(s)
        elif isinstance(s, dict):
            names.append(s.get("server_name") or s.get("name") or str(s))
        else:
            names.append(getattr(s, "server_name", None) or getattr(s, "name", None) or str(s))
    if i < 0 or i >= len(names):
        await callback.answer("Сервер не найден", show_alert=True)
        return
    server_name = names[i]
    key = f"grp_sel:{cluster_name}"
    data = await state.get_data()
    selected = set(data.get(key, []))
    if server_name in selected:
        selected.remove(server_name)
    else:
        selected.add(server_name)
    await state.update_data({key: list(selected)})
    await callback.message.edit_text(
        f"<b>🗂 Выберите серверы в кластере <code>{cluster_name}</code> для назначения тарифной группы:</b>",
        reply_markup=build_select_group_servers_kb(cluster_name, cluster_servers, selected),
    )


@router.callback_query(AdminClusterCallback.filter(F.action == "reset_group_selection"), IsAdminFilter())
async def reset_group_selection(
    callback: CallbackQuery, callback_data: AdminClusterCallback, session: AsyncSession, state: FSMContext
):
    cluster_name = callback_data.data
    servers = await get_servers(session=session, include_enabled=True)
    cluster_servers = servers.get(cluster_name, [])
    await state.update_data({f"grp_sel:{cluster_name}": []})
    await callback.message.edit_text(
        f"<b>🗂 Выберите серверы в кластере <code>{cluster_name}</code> для назначения тарифной группы:</b>",
        reply_markup=build_select_group_servers_kb(cluster_name, cluster_servers, set()),
    )


@router.callback_query(AdminClusterCallback.filter(F.action == "choose_group"), IsAdminFilter(), flags={"popup": True})
async def choose_group(
    callback: CallbackQuery, callback_data: AdminClusterCallback, session: AsyncSession, state: FSMContext
):
    cluster_name = callback_data.data
    key = f"grp_sel:{cluster_name}"
    data = await state.get_data()
    selected = set(data.get(key, []))
    if not selected:
        await callback.answer("Сначала выберите хотя бы один сервер", show_alert=True)
        return
    groups = [(i, code) for i, code in enumerate(ALLOWED_GROUP_CODES)]
    await callback.message.edit_text(
        f"<b>📚 Выберите группу для {len(selected)} сервер(а/ов) кластера <code>{cluster_name}</code>:</b>",
        reply_markup=build_tariff_group_selection_for_servers_kb(cluster_name, groups),
    )


@router.callback_query(
    AdminClusterCallback.filter(F.action == "apply_group_to_servers"), IsAdminFilter(), flags={"popup": True}
)
async def apply_group_to_servers(
    callback: CallbackQuery, callback_data: AdminClusterCallback, session: AsyncSession, state: FSMContext
):
    try:
        cluster_name, idx_str = callback_data.data.split("|", 1)
        i = int(idx_str)
        groups = ALLOWED_GROUP_CODES
        if i < 0 or i >= len(groups):
            await callback.answer("Группа не найдена", show_alert=True)
            return
        group_code = groups[i]

        key = f"grp_sel:{cluster_name}"
        data = await state.get_data()
        selected = set(data.get(key, []))
        if not selected:
            await callback.message.edit_text("❌ Не выбраны серверы для назначения группы.")
            return

        rows = await session.execute(select(Server.id, Server.server_name).where(Server.server_name.in_(selected)))
        id_by_name = {name: sid for sid, name in rows.fetchall()}
        server_ids = [id_by_name[n] for n in selected if n in id_by_name]
        if not server_ids:
            await callback.answer("Серверы не найдены", show_alert=True)
            return

        exist_rows = await session.execute(
            select(ServerSpecialgroup.server_id).where(
                and_(ServerSpecialgroup.server_id.in_(server_ids), ServerSpecialgroup.group_code == group_code)
            )
        )
        already = {r[0] for r in exist_rows.fetchall()}
        to_insert = [sid for sid in server_ids if sid not in already]

        if to_insert:
            session.add_all([ServerSpecialgroup(server_id=sid, group_code=group_code) for sid in to_insert])

        logger.debug(f"[apply_group_to_servers] group={group_code} server_ids={server_ids}")

        await state.update_data({key: []})

        servers = await get_servers(session, include_enabled=True)
        cluster_servers = servers.get(cluster_name, [])
        text = render_attach_tariff_menu_text(cluster_name, cluster_servers)
        await callback.message.edit_text(
            text=text,
            reply_markup=build_attach_tariff_kb(cluster_name),
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error(f"Ошибка при назначении группы тарифов: {e}")
        await callback.message.edit_text("❌ Произошла ошибка при назначении группы.")


@router.callback_query(
    AdminClusterCallback.filter(F.action == "reset_cluster_groups"), IsAdminFilter(), flags={"popup": True}
)
async def reset_cluster_groups(callback: CallbackQuery, callback_data: AdminClusterCallback, session: AsyncSession):
    try:
        cluster_name = callback_data.data
        res = await session.execute(select(Server.id).where(Server.cluster_name == cluster_name))
        server_ids = [row[0] for row in res.fetchall()]
        if not server_ids:
            await callback.answer("В кластере нет серверов", show_alert=True)
            return
        await session.execute(delete(ServerSpecialgroup).where(ServerSpecialgroup.server_id.in_(server_ids)))
        servers = await get_servers(session=session, include_enabled=True)
        cluster_servers = servers.get(cluster_name, [])
        await callback.message.edit_text(
            f"✅ Все привязки групп сброшены для кластера <b>{cluster_name}</b>.",
            reply_markup=build_manage_cluster_kb(cluster_servers, cluster_name),
        )
    except Exception as e:
        logger.error(f"Ошибка при сбросе групп для кластера {cluster_name}: {e}")
        await callback.message.edit_text("❌ Не удалось сбросить привязки групп.")

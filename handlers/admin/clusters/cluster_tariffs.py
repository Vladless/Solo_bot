from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_servers
from database.models import Server, ServerSpecialgroup, ServerSubgroup, Tariff
from filters.admin import IsAdminFilter
from handlers.utils import ALLOWED_GROUP_CODES
from logger import logger

from .base import router
from .keyboard import (
    AdminClusterCallback,
    build_attach_tariff_kb,
    build_manage_cluster_kb,
    build_select_group_servers_kb,
    build_select_subgroup_servers_kb,
    build_tariff_group_selection_for_servers_kb,
    build_tariff_group_selection_kb,
    build_tariff_subgroup_selection_kb,
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

        await session.execute(
            update(Server)
            .where(Server.cluster_name == cluster_name)
            .values(tariff_group=group_code)
        )
        await session.commit()

        servers = await get_servers(session=session, include_enabled=True)
        cluster_servers = servers.get(cluster_name, [])

        await callback.message.edit_text(
            f"✅ Для кластера <code>{cluster_name}</code> установлена тарифная группа: <b>{group_code}</b>",
            reply_markup=build_manage_cluster_kb(cluster_servers, cluster_name),
        )

    except Exception as e:
        logger.error(f"Ошибка при применении тарифной группы: {e}")
        await callback.message.edit_text("❌ Произошла ошибка при установке тарифной группы.")


@router.callback_query(AdminClusterCallback.filter(F.action == "set_subgroup"))
async def show_servers_for_subgroup(
    callback: CallbackQuery, callback_data: AdminClusterCallback, session: AsyncSession, state: FSMContext
):
    cluster_name = callback_data.data
    servers = await get_servers(session=session, include_enabled=True)
    cluster_servers = servers.get(cluster_name, [])
    data = await state.get_data()
    selected = set(data.get(f"subgrp_sel:{cluster_name}", []))
    await callback.message.edit_text(
        f"<b>🗂 Выберите серверы в кластере <code>{cluster_name}</code> для назначения подгруппы тарифов:</b>",
        reply_markup=build_select_subgroup_servers_kb(cluster_name, cluster_servers, selected),
    )


@router.callback_query(AdminClusterCallback.filter(F.action == "toggle_server_subgroup"))
async def toggle_server_for_subgroup(
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
        f"<b>🗂 Выберите серверы в кластере <code>{cluster_name}</code> для назначения подгруппы тарифов:</b>",
        reply_markup=build_select_subgroup_servers_kb(cluster_name, cluster_servers, selected),
    )


@router.callback_query(AdminClusterCallback.filter(F.action == "reset_subgroup_selection"))
async def reset_subgroup_selection(
    callback: CallbackQuery, callback_data: AdminClusterCallback, session: AsyncSession, state: FSMContext
):
    cluster_name = callback_data.data
    servers = await get_servers(session=session, include_enabled=True)
    cluster_servers = servers.get(cluster_name, [])
    await state.update_data({f"subgrp_sel:{cluster_name}": []})
    await callback.message.edit_text(
        f"<b>🗂 Выберите серверы в кластере <code>{cluster_name}</code> для назначения подгруппы тарифов:</b>",
        reply_markup=build_select_subgroup_servers_kb(cluster_name, cluster_servers, set()),
    )


@router.callback_query(AdminClusterCallback.filter(F.action == "choose_subgroup"))
async def choose_subgroup(
    callback: CallbackQuery, callback_data: AdminClusterCallback, session: AsyncSession, state: FSMContext
):
    cluster_name = callback_data.data
    key = f"subgrp_sel:{cluster_name}"
    data = await state.get_data()
    selected = set(data.get(key, []))
    if not selected:
        await callback.answer("Сначала выберите хотя бы один сервер", show_alert=True)
        return

    res = await session.execute(select(Server.tariff_group).where(Server.cluster_name == cluster_name).distinct())
    group_codes = [r[0] for r in res.fetchall() if r[0]]
    if not group_codes:
        await callback.answer("Сначала установите тарифную группу для этого кластера", show_alert=True)
        return

    group_code = group_codes[0]

    res2 = await session.execute(
        select(func.distinct(Tariff.subgroup_title))
        .where(Tariff.group_code == group_code)
        .where(Tariff.subgroup_title.isnot(None))
        .order_by(Tariff.subgroup_title.asc())
    )
    subgroups = [r[0] for r in res2.fetchall()]
    if not subgroups:
        await callback.message.edit_text("❌ Для этой группы нет доступных подгрупп.")
        return

    await callback.message.edit_text(
        f"<b>📚 Выберите подгруппу для {len(selected)} сервер(а/ов) кластера <code>{cluster_name}</code>:</b>",
        reply_markup=build_tariff_subgroup_selection_kb(cluster_name, subgroups),
    )


@router.callback_query(AdminClusterCallback.filter(F.action == "apply_tariff_subgroup"))
async def apply_tariff_subgroup(
    callback: CallbackQuery, callback_data: AdminClusterCallback, session: AsyncSession, state: FSMContext
):
    try:
        cluster_name, idx_str = callback_data.data.split("|", 1)
        i = int(idx_str)

        res = await session.execute(select(Server.tariff_group).where(Server.cluster_name == cluster_name).distinct())
        group_codes = [r[0] for r in res.fetchall() if r[0]]
        if not group_codes:
            await callback.answer("Не найдена тарифная группа кластера", show_alert=True)
            return
        group_code = group_codes[0]

        res2 = await session.execute(
            select(func.distinct(Tariff.subgroup_title))
            .where(Tariff.group_code == group_code)
            .where(Tariff.subgroup_title.isnot(None))
            .order_by(Tariff.subgroup_title.asc())
        )
        subgroups = [r[0] for r in res2.fetchall()]
        if i < 0 or i >= len(subgroups):
            await callback.answer("Подгруппа не найдена", show_alert=True)
            return
        subgroup_title = subgroups[i]

        key = f"subgrp_sel:{cluster_name}"
        data = await state.get_data()
        selected = set(data.get(key, []))
        if not selected:
            await callback.message.edit_text("❌ Не выбраны серверы для назначения подгруппы.")
            return

        servers_q = await session.execute(select(Server.id, Server.server_name).where(Server.server_name.in_(selected)))
        id_by_name = {name: sid for sid, name in servers_q.fetchall()}
        missing_ids = [id_by_name[n] for n in selected if n in id_by_name]
        if not missing_ids:
            await callback.answer("Серверы не найдены", show_alert=True)
            return

        existing_q = await session.execute(
            select(ServerSubgroup.server_id)
            .where(ServerSubgroup.server_id.in_(missing_ids))
            .where(ServerSubgroup.subgroup_title == subgroup_title)
        )
        already = {r[0] for r in existing_q.fetchall()}
        to_insert = [sid for sid in missing_ids if sid not in already]

        if to_insert:
            session.add_all([
                ServerSubgroup(server_id=sid, group_code=group_code, subgroup_title=subgroup_title) for sid in to_insert
            ])
            await session.commit()

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
        logger.error(f"Ошибка при применении подгруппы тарифов: {e}")
        await callback.message.edit_text("❌ Произошла ошибка при назначении подгруппы.")


@router.callback_query(AdminClusterCallback.filter(F.action == "reset_cluster_subgroups"))
async def reset_cluster_subgroups(callback: CallbackQuery, callback_data: AdminClusterCallback, session: AsyncSession):
    try:
        cluster_name = callback_data.data

        res = await session.execute(select(Server.id).where(Server.cluster_name == cluster_name))
        server_ids = [row[0] for row in res.fetchall()]
        if not server_ids:
            await callback.answer("В кластере нет серверов", show_alert=True)
            return

        await session.execute(delete(ServerSubgroup).where(ServerSubgroup.server_id.in_(server_ids)))
        await session.commit()

        servers = await get_servers(session=session, include_enabled=True)
        cluster_servers = servers.get(cluster_name, [])

        await callback.message.edit_text(
            f"✅ Все подгруппы тарифов сброшены для кластера <b>{cluster_name}</b>.",
            reply_markup=build_manage_cluster_kb(cluster_servers, cluster_name),
        )
    except Exception as e:
        logger.error(f"Ошибка при сбросе подгрупп для кластера {cluster_name}: {e}")
        await callback.message.edit_text("❌ Не удалось сбросить подгруппы.")


def render_attach_tariff_menu_text(cluster_name: str, cluster_servers: list[dict]) -> str:
    sub_map: dict[str, list[str]] = {}
    for s in cluster_servers:
        for sg in s.get("tariff_subgroups") or []:
            sub_map.setdefault(sg, []).append(s["server_name"])

    allowed = tuple(ALLOWED_GROUP_CODES)
    spec_map: dict[str, list[str]] = {k: [] for k in allowed}
    for s in cluster_servers:
        for g in s.get("special_groups") or []:
            if g in spec_map:
                spec_map[g].append(s["server_name"])

    lines = [f"<b>🧩 Привязки тарифов • {cluster_name}</b>"]

    lines.append("<b>Подгруппы:</b>")
    if sub_map:
        subs_lines = []
        for k in sorted(sub_map):
            servers_list = ", ".join(sorted(set(sub_map[k])))
            subs_lines.append(f"• <b>{k}</b>: {servers_list}")
        lines.append("<blockquote>\n" + "\n".join(subs_lines) + "\n</blockquote>")
    else:
        lines.append("<blockquote>— нет привязок</blockquote>")

    lines.append("<b>Спецгруппы:</b>")
    has_spec = any(spec_map[k] for k in allowed)
    if has_spec:
        spec_lines = []
        for k in allowed:
            vals = sorted(set(spec_map[k]))
            spec_lines.append(f"• <b>{k}</b>: {', '.join(vals) if vals else '—'}")
        lines.append("<blockquote>\n" + "\n".join(spec_lines) + "\n</blockquote>")
    else:
        lines.append("<blockquote>— нет привязок</blockquote>")

    return "\n".join(lines)


@router.callback_query(AdminClusterCallback.filter(F.action == "attach_tariff_menu"), IsAdminFilter())
async def handle_attach_tariff_menu(callback: CallbackQuery, session: AsyncSession):
    packed = AdminClusterCallback.unpack(callback.data)
    cluster_name = packed.data

    servers = await get_servers(session, include_enabled=True)
    cluster_servers = servers.get(cluster_name, [])

    text = render_attach_tariff_menu_text(cluster_name, cluster_servers)
    await callback.message.edit_text(
        text=text,
        reply_markup=build_attach_tariff_kb(cluster_name),
        disable_web_page_preview=True,
    )


@router.callback_query(AdminClusterCallback.filter(F.action == "set_group"))
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


@router.callback_query(AdminClusterCallback.filter(F.action == "toggle_server_group"))
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


@router.callback_query(AdminClusterCallback.filter(F.action == "reset_group_selection"))
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


@router.callback_query(AdminClusterCallback.filter(F.action == "choose_group"))
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


@router.callback_query(AdminClusterCallback.filter(F.action == "apply_group_to_servers"))
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
            await session.commit()

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


@router.callback_query(AdminClusterCallback.filter(F.action == "reset_cluster_groups"))
async def reset_cluster_groups(callback: CallbackQuery, callback_data: AdminClusterCallback, session: AsyncSession):
    try:
        cluster_name = callback_data.data
        res = await session.execute(select(Server.id).where(Server.cluster_name == cluster_name))
        server_ids = [row[0] for row in res.fetchall()]
        if not server_ids:
            await callback.answer("В кластере нет серверов", show_alert=True)
            return
        await session.execute(delete(ServerSpecialgroup).where(ServerSpecialgroup.server_id.in_(server_ids)))
        await session.commit()
        servers = await get_servers(session=session, include_enabled=True)
        cluster_servers = servers.get(cluster_name, [])
        await callback.message.edit_text(
            f"✅ Все привязки групп сброшены для кластера <b>{cluster_name}</b>.",
            reply_markup=build_manage_cluster_kb(cluster_servers, cluster_name),
        )
    except Exception as e:
        logger.error(f"Ошибка при сбросе групп для кластера {cluster_name}: {e}")
        await callback.message.edit_text("❌ Не удалось сбросить привязки групп.")

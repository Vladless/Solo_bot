from html import escape as html_escape
from typing import Any
from uuid import uuid4

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from core.settings.remnawave_config import (
    REMNAWAVE_CONFIG,
    get_host_rotation_allowed,
    get_load_monitor_groups,
    get_node_health_allowed,
    is_host_auto_disable_enabled,
    is_load_monitor_enabled,
    update_remnawave_config,
)
from database import async_session_maker, get_servers
from logger import logger
from panels import remnawave as remnawave_panel
from settings.config import REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD, REMNAWAVE_TOKEN_LOGIN_ENABLED

from ..panel.headers import card, menu_text, quote, section
from ..panel.keyboard import AdminPanelCallback
from .keyboard import (
    LOAD_MONITOR_SNAPSHOT_NODES_PER_PAGE,
    REMNAWAVE_HOSTS_PER_PAGE,
    build_remnawave_load_group_kb,
    build_remnawave_load_groups_kb,
    build_remnawave_load_nodes_kb,
    build_remnawave_load_snapshot_kb,
    build_settings_remnawave_health_nodes_kb,
    build_settings_remnawave_hosts_kb,
    build_settings_remnawave_kb,
    build_settings_remnawave_load_kb,
    build_settings_remnawave_node_kb,
    build_settings_remnawave_rotation_kb,
)


router = Router(name="admin_settings_remnawave")


class RemnawaveSettingsState(StatesGroup):
    waiting_for_node_interval = State()
    waiting_for_rotation_interval = State()
    waiting_for_load_interval = State()
    waiting_for_load_confirmations = State()
    waiting_for_load_group_name = State()
    waiting_for_load_group_limit = State()
    waiting_for_load_group_tag = State()


def _node_health_enabled() -> bool:
    return bool(REMNAWAVE_CONFIG.get("NODE_HEALTH_ENABLED", False))


def _auto_disable_enabled() -> bool:
    return is_host_auto_disable_enabled()


def _host_rotation_enabled() -> bool:
    return bool(REMNAWAVE_CONFIG.get("HOST_ROTATION_ENABLED", False))


def _node_interval() -> int:
    return int(REMNAWAVE_CONFIG.get("NODE_HEALTH_INTERVAL_MIN") or 5)


def _rotation_interval() -> int:
    return int(REMNAWAVE_CONFIG.get("HOST_ROTATION_INTERVAL_MIN") or 60)


def _load_monitor_interval() -> int:
    return int(REMNAWAVE_CONFIG.get("LOAD_MONITOR_INTERVAL_MIN") or 5)


def _load_monitor_overload_confirmations() -> int:
    try:
        return min(100, max(1, int(REMNAWAVE_CONFIG.get("LOAD_MONITOR_OVERLOAD_CONFIRMATIONS") or 2)))
    except (TypeError, ValueError):
        return 2


def _load_groups_copy() -> list[dict[str, Any]]:
    groups = get_load_monitor_groups()
    return [{**group, "node_keys": [str(key) for key in (group.get("node_keys") or []) if key]} for group in groups]


async def _save_load_groups(groups: list[dict[str, Any]]) -> None:
    new_cfg = dict(REMNAWAVE_CONFIG)
    new_cfg["LOAD_MONITOR_GROUPS"] = groups
    async with async_session_maker() as session:
        await update_remnawave_config(session, new_cfg)


def _load_group_by_index(groups: list[dict[str, Any]], page: int) -> tuple[int, dict[str, Any]] | None:
    index = int(page or 1) - 1
    if index < 0 or index >= len(groups):
        return None
    return index, groups[index]


def _root_text() -> str:
    node_state = "✅ Включён" if _node_health_enabled() else "❌ Выключен"
    rot_state = "✅ Включена" if _host_rotation_enabled() else "❌ Выключена"
    load_state = "✅ Включён" if is_load_monitor_enabled() else "❌ Выключен"
    allowed_count = len(get_host_rotation_allowed())
    return menu_text(
        "Remnawave",
        "Фоновые задачи по API панели.",
        card(
            section("🩺 Проверка нод", f"Статус: {node_state}", f"Интервал: {_node_interval()} мин"),
            section(
                "🔀 Ротация хостов",
                f"Статус: {rot_state}",
                f"Интервал: {_rotation_interval()} мин",
                f"Хостов: {allowed_count}",
            ),
            section(
                "📈 Мониторинг загрузки",
                f"Статус: {load_state}",
                f"Интервал: {_load_monitor_interval()} мин",
                f"Групп: {len(get_load_monitor_groups())}",
            ),
        ),
    )


def _node_text() -> str:
    state = "✅ Включена" if _node_health_enabled() else "❌ Выключена"
    auto_state = "✅ Включено" if _auto_disable_enabled() else "❌ Выключено"
    selected_count = len(get_node_health_allowed())
    return menu_text(
        "Проверка нод",
        "Бот следит, какие ноды отвалились.",
        section(
            "🩺 Проверка",
            f"Статус: {state}",
            f"Интервал: {_node_interval()} мин",
            f"Нод: {selected_count if selected_count else 'все'}",
            f"Авто-отключение: {auto_state}",
        ),
        "Когда нода отваливается или возвращается, админам приходит уведомление.",
        quote(
            "Нода замолчала — бот гасит её хосты в панели, чтобы новые подключения "
            "не уходили на мёртвый сервер, и возвращает их, когда нода снова в строю.",
            "Трогает только то, что выключил сам: выключенное вручную останется как есть.",
            "Если отметить конкретные ноды, бот проверит только их — так ноды "
            "авто-балансировки не будут считаться упавшими.",
        ),
    )


def _rotation_text() -> str:
    state = "✅ Включена" if _host_rotation_enabled() else "❌ Выключена"
    allowed = get_host_rotation_allowed()
    return menu_text(
        "Ротация хостов",
        "Свободные хосты поднимаются выше в подписке.",
        section("🔀 Ротация", f"Статус: {state}", f"Интервал: {_rotation_interval()} мин", f"Хостов: {len(allowed)}"),
        quote(
            "Бот считает, сколько людей онлайн на каждой ноде, и двигает наименее нагруженные хосты в начало списка.",
            "Двигаются только отмеченные хосты, остальные стоят на своих местах.",
        ),
    )


def _hosts_text(hosts: list[tuple[str, dict[str, Any]]], allowed: set[str]) -> str:
    if not hosts:
        return menu_text(
            "Хосты Remnawave",
            "Не удалось получить список хостов. Проверьте, что панель доступна, "
            "а у токена есть права на чтение <code>/hosts</code>.",
        )
    total = len(hosts)
    selected = sum(1 for _, h in hosts if str(h.get("uuid")) in allowed)
    return menu_text(
        "Хосты для ротации",
        "Нажмите на строку, чтобы включить или выключить хост.",
        section("🖧 Хосты", f"Всего: {total}", f"В ротации: {selected}"),
        quote("Отмеченные ✅ бот двигает по позициям, глядя на нагрузку их ноды."),
    )


async def _fetch_all_hosts() -> list[tuple[str, dict[str, Any]]]:
    async with async_session_maker() as session:
        servers = await get_servers(session, include_enabled=True)

    seen_panels: set[str] = set()
    result: list[tuple[str, dict[str, Any]]] = []
    for cluster in servers.values():
        for srv in cluster:
            if srv.get("panel_type") != "remnawave":
                continue
            api_url = (srv.get("api_url") or "").strip()
            if not api_url or api_url in seen_panels:
                continue
            seen_panels.add(api_url)
            api = remnawave_panel.RemnawaveAPI(api_url)
            try:
                if not REMNAWAVE_TOKEN_LOGIN_ENABLED:
                    ok = await api.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD)
                    if not ok:
                        continue
                hosts = await api.get_hosts() or []
            except Exception as exc:
                logger.warning("[Remnawave-Admin] Ошибка получения хостов с {}: {}", api_url, exc)
                continue
            finally:
                try:
                    await api.aclose()
                except Exception:
                    pass
            if not isinstance(hosts, list):
                continue
            for host in hosts:
                if host.get("uuid"):
                    result.append((api_url, host))
    return result


async def _fetch_all_nodes() -> list[tuple[str, dict[str, Any]]]:
    async with async_session_maker() as session:
        servers = await get_servers(session, include_enabled=True)

    seen_panels: set[str] = set()
    result: list[tuple[str, dict[str, Any]]] = []
    for cluster in servers.values():
        for srv in cluster:
            if srv.get("panel_type") != "remnawave":
                continue
            api_url = (srv.get("api_url") or "").strip()
            if not api_url or api_url in seen_panels:
                continue
            seen_panels.add(api_url)
            api = remnawave_panel.RemnawaveAPI(api_url)
            try:
                if not REMNAWAVE_TOKEN_LOGIN_ENABLED:
                    ok = await api.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD)
                    if not ok:
                        continue
                nodes = await api.get_all_nodes() or []
            except Exception as exc:
                logger.warning("[Remnawave-Admin] Ошибка получения нод с {}: {}", api_url, exc)
                continue
            finally:
                try:
                    await api.aclose()
                except Exception:
                    pass
            if not isinstance(nodes, list):
                continue
            for node in nodes:
                if node.get("uuid"):
                    result.append((api_url, node))
    return result


def _health_nodes_text(nodes: list[tuple[str, dict[str, Any]]], allowed: set[str]) -> str:
    if not nodes:
        return menu_text(
            "Ноды Remnawave",
            "Не удалось получить список нод. Проверьте, что панель доступна, "
            "а у токена есть права на чтение <code>/nodes</code>.",
        )
    total = len(nodes)
    selected = sum(1 for _, n in nodes if str(n.get("uuid")) in allowed)
    return menu_text(
        "Ноды для проверки",
        "Нажмите на строку, чтобы добавить ноду или убрать.",
        section("🩺 Ноды", f"Всего: {total}", f"Выбрано: {selected}"),
        quote(
            "Бот следит и гасит хосты только у отмеченных ✅ нод.",
            "Не отмечено ни одной — проверяются все. Ноды авто-балансировки просто не отмечайте, и бот их не тронет.",
        ),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_remnawave"))
async def open_remnawave_settings(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        text=_root_text(),
        reply_markup=build_settings_remnawave_kb(
            _node_health_enabled(), _host_rotation_enabled(), is_load_monitor_enabled()
        ),
    )
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_node_menu"))
async def open_node_menu(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        text=_node_text(),
        reply_markup=build_settings_remnawave_node_kb(
            _node_health_enabled(), _node_interval(), _auto_disable_enabled()
        ),
    )
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_node_toggle"), flags={"popup": True})
async def toggle_node_health(callback: CallbackQuery) -> None:
    new_cfg = dict(REMNAWAVE_CONFIG)
    new_cfg["NODE_HEALTH_ENABLED"] = not _node_health_enabled()
    async with async_session_maker() as session:
        await update_remnawave_config(session, new_cfg)
    await callback.answer(
        "✅ Проверка включена" if new_cfg["NODE_HEALTH_ENABLED"] else "❌ Проверка выключена",
        show_alert=True,
    )
    await callback.message.edit_text(
        text=_node_text(),
        reply_markup=build_settings_remnawave_node_kb(
            _node_health_enabled(), _node_interval(), _auto_disable_enabled()
        ),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_node_interval"))
async def prompt_node_interval(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_text(
        text=(
            menu_text(
                "Интервал проверки нод",
                f"Сейчас: <b>{_node_interval()} мин.</b>",
                quote("Введите новое значение в минутах (1–1440). Частые опросы нагружают панель."),
            )
        ),
    )
    await state.set_state(RemnawaveSettingsState.waiting_for_node_interval)
    await callback.answer()


@router.message(RemnawaveSettingsState.waiting_for_node_interval)
async def set_node_interval(message: Message, state: FSMContext) -> None:
    try:
        value = int((message.text or "").strip())
    except ValueError:
        await message.answer(menu_text("Remnawave", "❌ Нужно число от 1 до 1440."))
        return
    if not 1 <= value <= 1440:
        await message.answer(menu_text("Remnawave", "❌ Диапазон: 1–1440 минут."))
        return
    new_cfg = dict(REMNAWAVE_CONFIG)
    new_cfg["NODE_HEALTH_INTERVAL_MIN"] = value
    async with async_session_maker() as session:
        await update_remnawave_config(session, new_cfg)
    await state.clear()
    await message.answer(
        text=_node_text(),
        reply_markup=build_settings_remnawave_node_kb(
            _node_health_enabled(), _node_interval(), _auto_disable_enabled()
        ),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_autodisable_toggle"), flags={"popup": True})
async def toggle_auto_disable(callback: CallbackQuery) -> None:
    new_cfg = dict(REMNAWAVE_CONFIG)
    new_cfg["HOST_AUTO_DISABLE_ON_NODE_DOWN"] = not _auto_disable_enabled()
    async with async_session_maker() as session:
        await update_remnawave_config(session, new_cfg)
    await callback.answer(
        "✅ Авто-отключение хостов включено"
        if new_cfg["HOST_AUTO_DISABLE_ON_NODE_DOWN"]
        else "❌ Авто-отключение хостов выключено",
        show_alert=True,
    )
    await callback.message.edit_text(
        text=_node_text(),
        reply_markup=build_settings_remnawave_node_kb(
            _node_health_enabled(), _node_interval(), _auto_disable_enabled()
        ),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_node_sync_now"))
async def run_host_sync_now(callback: CallbackQuery) -> None:
    from services.remnawave_monitor import sync_hosts_with_node_state

    await callback.answer(menu_text("Remnawave", "Синхронизирую…"))

    try:
        summary = await sync_hosts_with_node_state()
    except Exception as exc:
        logger.error("[Remnawave-Admin] Ошибка ручной синхронизации хостов: {}", exc)
        await callback.message.answer(
            menu_text("Синхронизация", "❌ Синхронизировать не удалось.", section("⚠️ Причина", str(exc)))
        )
        return

    blocks = [section("📊 Итог", f"Выключено: {len(summary['disabled'])}", f"Включено: {len(summary['enabled'])}")]
    if summary["disabled"]:
        blocks.append(section("⛔ Выключены", *summary["disabled"]))
    if summary["enabled"]:
        blocks.append(section("✅ Включены", *summary["enabled"]))
    if summary["errors"]:
        blocks.append(section("⚠️ Ошибки", *summary["errors"]))

    await callback.message.answer(menu_text("Синхронизация", card(*blocks)))


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_rot_menu"))
async def open_rotation_menu(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        text=_rotation_text(),
        reply_markup=build_settings_remnawave_rotation_kb(_host_rotation_enabled(), _rotation_interval()),
    )
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_rot_toggle"), flags={"popup": True})
async def toggle_rotation(callback: CallbackQuery) -> None:
    new_cfg = dict(REMNAWAVE_CONFIG)
    new_cfg["HOST_ROTATION_ENABLED"] = not _host_rotation_enabled()
    async with async_session_maker() as session:
        await update_remnawave_config(session, new_cfg)
    await callback.answer(
        "✅ Ротация включена" if new_cfg["HOST_ROTATION_ENABLED"] else "❌ Ротация выключена",
        show_alert=True,
    )
    await callback.message.edit_text(
        text=_rotation_text(),
        reply_markup=build_settings_remnawave_rotation_kb(_host_rotation_enabled(), _rotation_interval()),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_rot_interval"))
async def prompt_rotation_interval(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_text(
        text=menu_text(
            "Интервал ротации",
            f"Сейчас: <b>{_rotation_interval()} мин.</b>",
            quote("Введите новое значение в минутах (5–1440)."),
        ),
    )
    await state.set_state(RemnawaveSettingsState.waiting_for_rotation_interval)
    await callback.answer()


@router.message(RemnawaveSettingsState.waiting_for_rotation_interval)
async def set_rotation_interval(message: Message, state: FSMContext) -> None:
    try:
        value = int((message.text or "").strip())
    except ValueError:
        await message.answer(menu_text("Remnawave", "❌ Нужно число от 5 до 1440."))
        return
    if not 5 <= value <= 1440:
        await message.answer(menu_text("Remnawave", "❌ Допустимый диапазон: 5–1440 минут"))
        return
    new_cfg = dict(REMNAWAVE_CONFIG)
    new_cfg["HOST_ROTATION_INTERVAL_MIN"] = value
    async with async_session_maker() as session:
        await update_remnawave_config(session, new_cfg)
    await state.clear()
    await message.answer(
        text=_rotation_text(),
        reply_markup=build_settings_remnawave_rotation_kb(_host_rotation_enabled(), _rotation_interval()),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_rot_run_now"))
async def run_rotation_now(callback: CallbackQuery) -> None:
    from services.remnawave_monitor import run_host_rotation

    await callback.answer(menu_text("Remnawave", "Запускаю ротацию…"))

    try:
        summary = await run_host_rotation()
    except Exception as exc:
        logger.error("[Remnawave-Admin] Ошибка ручной ротации: {}", exc)
        await callback.message.answer(menu_text("Ротация", "❌ Ротация не удалась.", section("⚠️ Причина", str(exc))))
        return

    blocks = [
        section(
            "📊 Итог",
            f"Хостов: {summary['allowed_count']}",
            f"Панелей: {summary['panels']}",
            f"Переставлено: {summary['moved_total']}",
        )
    ]
    if summary["details"]:
        blocks.append(section("📋 Детали", *summary["details"]))
    if summary["errors"]:
        blocks.append(section("⚠️ Ошибки", *summary["errors"]))

    await callback.message.answer(menu_text("Ротация", card(*blocks)))


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_rot_hosts"))
async def open_rotation_hosts(callback: CallbackQuery, callback_data: AdminPanelCallback) -> None:
    await callback.answer(menu_text("Remnawave", "Загружаю хосты…"))
    hosts = await _fetch_all_hosts()
    allowed = get_host_rotation_allowed()
    page = max(1, int(callback_data.page or 1))
    await callback.message.edit_text(
        text=_hosts_text(hosts, allowed),
        reply_markup=build_settings_remnawave_hosts_kb(page, hosts, allowed),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_rot_toggle_host"), flags={"popup": True})
async def toggle_host(callback: CallbackQuery, callback_data: AdminPanelCallback) -> None:
    idx = int(callback_data.page or 0)
    hosts = await _fetch_all_hosts()
    if idx < 0 or idx >= len(hosts):
        await callback.answer("Хост не найден", show_alert=True)
        return
    _, host = hosts[idx]
    host_uuid = str(host.get("uuid"))
    allowed = get_host_rotation_allowed()
    if host_uuid in allowed:
        allowed.discard(host_uuid)
        toast = menu_text("Remnawave", "▫️ Хост убран из ротации")
    else:
        allowed.add(host_uuid)
        toast = menu_text("Remnawave", "✅ Хост добавлен в ротацию")

    new_cfg = dict(REMNAWAVE_CONFIG)
    new_cfg["HOST_ROTATION_ALLOWED"] = sorted(allowed)
    async with async_session_maker() as session:
        await update_remnawave_config(session, new_cfg)

    page = max(1, idx // REMNAWAVE_HOSTS_PER_PAGE + 1)
    await callback.answer(toast)
    await callback.message.edit_text(
        text=_hosts_text(hosts, allowed),
        reply_markup=build_settings_remnawave_hosts_kb(page, hosts, allowed),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_rot_select_all"))
async def select_all_on_page(callback: CallbackQuery, callback_data: AdminPanelCallback) -> None:
    hosts = await _fetch_all_hosts()
    allowed = get_host_rotation_allowed()
    page = max(1, int(callback_data.page or 1))
    start = (page - 1) * REMNAWAVE_HOSTS_PER_PAGE
    for _, host in hosts[start : start + REMNAWAVE_HOSTS_PER_PAGE]:
        uuid = str(host.get("uuid"))
        if uuid:
            allowed.add(uuid)
    new_cfg = dict(REMNAWAVE_CONFIG)
    new_cfg["HOST_ROTATION_ALLOWED"] = sorted(allowed)
    async with async_session_maker() as session:
        await update_remnawave_config(session, new_cfg)
    await callback.answer(menu_text("Remnawave", "✅ Включены"))
    await callback.message.edit_text(
        text=_hosts_text(hosts, allowed),
        reply_markup=build_settings_remnawave_hosts_kb(page, hosts, allowed),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_rot_clear_page"))
async def clear_page(callback: CallbackQuery, callback_data: AdminPanelCallback) -> None:
    hosts = await _fetch_all_hosts()
    allowed = get_host_rotation_allowed()
    page = max(1, int(callback_data.page or 1))
    start = (page - 1) * REMNAWAVE_HOSTS_PER_PAGE
    for _, host in hosts[start : start + REMNAWAVE_HOSTS_PER_PAGE]:
        uuid = str(host.get("uuid"))
        allowed.discard(uuid)
    new_cfg = dict(REMNAWAVE_CONFIG)
    new_cfg["HOST_ROTATION_ALLOWED"] = sorted(allowed)
    async with async_session_maker() as session:
        await update_remnawave_config(session, new_cfg)
    await callback.answer(menu_text("Remnawave", "▫️ Сброшено"))
    await callback.message.edit_text(
        text=_hosts_text(hosts, allowed),
        reply_markup=build_settings_remnawave_hosts_kb(page, hosts, allowed),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_node_sel"))
async def open_health_nodes(callback: CallbackQuery, callback_data: AdminPanelCallback) -> None:
    await callback.answer(menu_text("Remnawave", "Загружаю ноды…"))
    nodes = await _fetch_all_nodes()
    allowed = get_node_health_allowed()
    page = max(1, int(callback_data.page or 1))
    await callback.message.edit_text(
        text=_health_nodes_text(nodes, allowed),
        reply_markup=build_settings_remnawave_health_nodes_kb(page, nodes, allowed),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_node_sel_toggle"), flags={"popup": True})
async def toggle_health_node(callback: CallbackQuery, callback_data: AdminPanelCallback) -> None:
    idx = int(callback_data.page or 0)
    nodes = await _fetch_all_nodes()
    if idx < 0 or idx >= len(nodes):
        await callback.answer("Нода не найдена", show_alert=True)
        return
    _, node = nodes[idx]
    node_uuid = str(node.get("uuid"))
    allowed = get_node_health_allowed()
    if node_uuid in allowed:
        allowed.discard(node_uuid)
        toast = menu_text("Remnawave", "▫️ Нода убрана из проверки")
    else:
        allowed.add(node_uuid)
        toast = menu_text("Remnawave", "✅ Нода добавлена в проверку")

    new_cfg = dict(REMNAWAVE_CONFIG)
    new_cfg["NODE_HEALTH_ALLOWED"] = sorted(allowed)
    async with async_session_maker() as session:
        await update_remnawave_config(session, new_cfg)

    page = max(1, idx // REMNAWAVE_HOSTS_PER_PAGE + 1)
    await callback.answer(toast)
    await callback.message.edit_text(
        text=_health_nodes_text(nodes, allowed),
        reply_markup=build_settings_remnawave_health_nodes_kb(page, nodes, allowed),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_node_sel_all"))
async def select_all_health_nodes_on_page(callback: CallbackQuery, callback_data: AdminPanelCallback) -> None:
    nodes = await _fetch_all_nodes()
    allowed = get_node_health_allowed()
    page = max(1, int(callback_data.page or 1))
    start = (page - 1) * REMNAWAVE_HOSTS_PER_PAGE
    for _, node in nodes[start : start + REMNAWAVE_HOSTS_PER_PAGE]:
        uuid = str(node.get("uuid"))
        if uuid:
            allowed.add(uuid)
    new_cfg = dict(REMNAWAVE_CONFIG)
    new_cfg["NODE_HEALTH_ALLOWED"] = sorted(allowed)
    async with async_session_maker() as session:
        await update_remnawave_config(session, new_cfg)
    await callback.answer(menu_text("Remnawave", "✅ Выбраны"))
    await callback.message.edit_text(
        text=_health_nodes_text(nodes, allowed),
        reply_markup=build_settings_remnawave_health_nodes_kb(page, nodes, allowed),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_node_sel_clear"))
async def clear_health_nodes_page(callback: CallbackQuery, callback_data: AdminPanelCallback) -> None:
    nodes = await _fetch_all_nodes()
    allowed = get_node_health_allowed()
    page = max(1, int(callback_data.page or 1))
    start = (page - 1) * REMNAWAVE_HOSTS_PER_PAGE
    for _, node in nodes[start : start + REMNAWAVE_HOSTS_PER_PAGE]:
        allowed.discard(str(node.get("uuid")))
    new_cfg = dict(REMNAWAVE_CONFIG)
    new_cfg["NODE_HEALTH_ALLOWED"] = sorted(allowed)
    async with async_session_maker() as session:
        await update_remnawave_config(session, new_cfg)
    await callback.answer(menu_text("Remnawave", "▫️ Сброшено"))
    await callback.message.edit_text(
        text=_health_nodes_text(nodes, allowed),
        reply_markup=build_settings_remnawave_health_nodes_kb(page, nodes, allowed),
    )


def _load_monitor_text() -> str:
    enabled = is_load_monitor_enabled()
    groups = get_load_monitor_groups()
    selected_nodes = sum(len(group.get("node_keys") or []) for group in groups)
    return menu_text(
        "Мониторинг загрузки",
        section(
            "📈 Состояние",
            f"Статус: {'✅ включён' if enabled else '❌ выключен'}",
            f"Интервал: {_load_monitor_interval()} мин",
            f"Подтверждений перегрузки: {_load_monitor_overload_confirmations()} проверок подряд",
            f"Групп: {len(groups)} · выбранных нод: {selected_nodes}",
        ),
        quote(
            "Бот читает системные метрики Remnawave: load average, число ядер, память и RX/TX. Для порога берётся load average за 1 минуту, делённый на число ядер и умноженный на 100%.",
            "Перегрузка должна повториться заданное число проверок подряд. После этого бот временно снимет тег авто-пула с хостов ноды; вернёт его, когда нагрузка опустится ниже 75% порога.",
            "Подписки продолжает выдавать Remnawave. Клиент увидит новый состав пула после обновления подписки.",
        ),
    )


def _load_groups_text(groups: list[dict[str, Any]]) -> str:
    if not groups:
        return menu_text("Группы серверов", "Групп пока нет. Создайте группу и добавьте в неё ноды Remnawave.")
    lines = []
    for group in groups:
        limit = int(group.get("max_cpu_percent") or 0)
        lines.append(
            f"• <b>{html_escape(str(group.get('name') or 'Без названия'))}</b>: "
            f"{len(group.get('node_keys') or [])} нод, "
            f"{'порог ' + str(limit) + '% load/ядро' if limit else 'только наблюдение'}"
        )
    return menu_text(
        "Группы серверов",
        "Ноды можно сгруппировать по типу или назначению. Внутри группы используется общий порог 1-минутного load average, нормированного на число ядер.",
        section("🗂 Группы", *lines),
    )


def _load_group_text(group: dict[str, Any]) -> str:
    limit = int(group.get("max_cpu_percent") or 0)
    tag = str(group.get("routing_tag") or "")
    return menu_text(
        str(group.get("name") or "Группа серверов"),
        section(
            "📈 Мониторинг",
            f"Нод выбрано: {len(group.get('node_keys') or [])}",
            f"Порог: {limit}% load/ядро" if limit else "Порог: только наблюдение",
            f"Тег авто-пула: <code>{html_escape(tag)}</code>" if tag else "Тег авто-пула: не задан",
        ),
        quote(
            "Значение 0 оставляет группу в режиме наблюдения. Чтобы бот временно исключал перегруженную ноду из авто-выбора, укажите порог и тег, который использует шаблон Remnawave.",
            "Для хостов, используемых только этой группой: LTE обычно использует LTE_ROUTING_HOST, обычный авто-пул — ROUTING_HOST.",
        ),
    )


def _get_load_group(groups: list[dict[str, Any]], group_id: str) -> tuple[int, dict[str, Any]] | None:
    for index, group in enumerate(groups):
        if str(group.get("id")) == str(group_id):
            return index, group
    return None


def _same_pool_conflict(groups: list[dict[str, Any]], current_id: str, node_key: str, tag: str) -> bool:
    if not tag:
        return False
    for group in groups:
        if str(group.get("id")) == current_id or str(group.get("routing_tag") or "") != tag:
            continue
        if node_key in {str(key) for key in (group.get("node_keys") or [])}:
            return True
    return False


async def _show_load_nodes(message: Message, state: FSMContext, page: int = 1) -> None:
    data = await state.get_data()
    group_id = str(data.get("load_group_id") or "")
    groups = _load_groups_copy()
    found = _get_load_group(groups, group_id)
    if not found:
        await message.edit_text(menu_text("Группы серверов", "Группа не найдена."))
        return
    group_index, group = found
    nodes = await _fetch_all_nodes()
    selected = {str(key) for key in group.get("node_keys") or []}
    text = menu_text(
        f"Ноды группы «{html_escape(str(group.get('name') or ''))}»",
        "Нажмите на ноду, чтобы добавить её в группу или убрать. Справа показана системная нагрузка за 1 минуту, нормированная на число ядер; онлайн отображается только для справки.",
        section("🖧 Выбрано", f"{len(selected)} нод"),
    )
    await message.edit_text(
        text=text,
        reply_markup=build_remnawave_load_nodes_kb(page, nodes, selected, group_index + 1),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_load_menu"))
async def open_load_monitor_menu(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        text=_load_monitor_text(),
        reply_markup=build_settings_remnawave_load_kb(
            is_load_monitor_enabled(), _load_monitor_interval(), _load_monitor_overload_confirmations()
        ),
    )
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_load_toggle"), flags={"popup": True})
async def toggle_load_monitor(callback: CallbackQuery) -> None:
    enabled = not is_load_monitor_enabled()
    config = dict(REMNAWAVE_CONFIG)
    config["LOAD_MONITOR_ENABLED"] = enabled
    async with async_session_maker() as session:
        await update_remnawave_config(session, config)

    restore_result = None
    if not enabled:
        from services.remnawave_load_monitor import restore_managed_load_tags

        restore_result = await restore_managed_load_tags()
    toast = "✅ Мониторинг включён" if enabled else "❌ Мониторинг выключен"
    if restore_result and restore_result.get("errors"):
        toast += f"; не удалось вернуть тегов: {len(restore_result['errors'])}"
    await callback.answer(toast, show_alert=True)
    await callback.message.edit_text(
        text=_load_monitor_text(),
        reply_markup=build_settings_remnawave_load_kb(
            is_load_monitor_enabled(), _load_monitor_interval(), _load_monitor_overload_confirmations()
        ),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_load_interval"))
async def prompt_load_monitor_interval(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_text(
        menu_text(
            "Интервал мониторинга",
            f"Сейчас: <b>{_load_monitor_interval()} мин.</b>",
            quote("Введите интервал от 1 до 1440 минут."),
        )
    )
    await state.set_state(RemnawaveSettingsState.waiting_for_load_interval)
    await callback.answer()


@router.message(RemnawaveSettingsState.waiting_for_load_interval)
async def set_load_monitor_interval(message: Message, state: FSMContext) -> None:
    try:
        value = int((message.text or "").strip())
    except ValueError:
        await message.answer(menu_text("Мониторинг нагрузки", "Введите целое число от 1 до 1440."))
        return
    if not 1 <= value <= 1440:
        await message.answer(menu_text("Мониторинг нагрузки", "Допустимый интервал: 1–1440 минут."))
        return
    config = dict(REMNAWAVE_CONFIG)
    config["LOAD_MONITOR_INTERVAL_MIN"] = value
    async with async_session_maker() as session:
        await update_remnawave_config(session, config)
    await state.clear()
    await message.answer(
        _load_monitor_text(),
        reply_markup=build_settings_remnawave_load_kb(
            is_load_monitor_enabled(), _load_monitor_interval(), _load_monitor_overload_confirmations()
        ),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_load_confirmations"))
async def prompt_load_monitor_confirmations(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_text(
        menu_text(
            "Подтверждение перегрузки",
            f"Сейчас: <b>{_load_monitor_overload_confirmations()} проверок подряд.</b>",
            quote("Отправьте число от 1 до 100. Одна проверка — один цикл мониторинга."),
        )
    )
    await state.set_state(RemnawaveSettingsState.waiting_for_load_confirmations)
    await callback.answer()


@router.message(RemnawaveSettingsState.waiting_for_load_confirmations)
async def set_load_monitor_confirmations(message: Message, state: FSMContext) -> None:
    try:
        value = int((message.text or "").strip())
    except ValueError:
        await message.answer(menu_text("Мониторинг нагрузки", "Введите целое число от 1 до 100."))
        return
    if not 1 <= value <= 100:
        await message.answer(menu_text("Мониторинг нагрузки", "Допустимое число: 1–100 проверок подряд."))
        return
    config = dict(REMNAWAVE_CONFIG)
    config["LOAD_MONITOR_OVERLOAD_CONFIRMATIONS"] = value
    async with async_session_maker() as session:
        await update_remnawave_config(session, config)
    await state.clear()
    await message.answer(
        _load_monitor_text(),
        reply_markup=build_settings_remnawave_load_kb(
            is_load_monitor_enabled(), _load_monitor_interval(), _load_monitor_overload_confirmations()
        ),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_load_groups"))
async def open_load_groups(callback: CallbackQuery) -> None:
    groups = _load_groups_copy()
    await callback.message.edit_text(
        _load_groups_text(groups),
        reply_markup=build_remnawave_load_groups_kb(groups),
    )
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_load_group_add"))
async def prompt_load_group_name(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_text(
        menu_text("Новая группа серверов", "Отправьте её название, например <b>LTE</b> или <b>Обычные</b>."),
    )
    await state.set_state(RemnawaveSettingsState.waiting_for_load_group_name)
    await callback.answer()


@router.message(RemnawaveSettingsState.waiting_for_load_group_name)
async def create_load_group(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not 1 <= len(name) <= 32:
        await message.answer(menu_text("Новая группа серверов", "Название должно быть длиной от 1 до 32 символов."))
        return
    groups = _load_groups_copy()
    if any(str(group.get("name") or "").casefold() == name.casefold() for group in groups):
        await message.answer(menu_text("Новая группа серверов", "Группа с таким названием уже есть."))
        return
    group_id = uuid4().hex[:12]
    groups.append({"id": group_id, "name": name, "node_keys": [], "max_cpu_percent": 100, "routing_tag": ""})
    await _save_load_groups(groups)
    await state.clear()
    await message.answer(
        _load_groups_text(groups),
        reply_markup=build_remnawave_load_groups_kb(groups),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_load_group"))
async def open_load_group(callback: CallbackQuery, callback_data: AdminPanelCallback, state: FSMContext) -> None:
    groups = _load_groups_copy()
    found = _load_group_by_index(groups, callback_data.page)
    if not found:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    index, group = found
    await state.update_data(load_group_id=str(group.get("id")))
    await callback.message.edit_text(
        _load_group_text(group),
        reply_markup=build_remnawave_load_group_kb(group, index + 1),
    )
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_load_nodes"))
async def open_load_group_nodes(callback: CallbackQuery, state: FSMContext, callback_data: AdminPanelCallback) -> None:
    await callback.answer(menu_text("Мониторинг нагрузки", "Загружаю ноды…"))
    await _show_load_nodes(callback.message, state, max(1, int(callback_data.page or 1)))


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_load_node_toggle"), flags={"popup": True})
async def toggle_load_group_node(
    callback: CallbackQuery,
    callback_data: AdminPanelCallback,
    state: FSMContext,
) -> None:
    from services.remnawave_load_monitor import make_node_key, restore_managed_load_tags, run_load_monitor_cycle

    data = await state.get_data()
    group_id = str(data.get("load_group_id") or "")
    groups = _load_groups_copy()
    found = _get_load_group(groups, group_id)
    nodes = await _fetch_all_nodes()
    index = int(callback_data.page or 0)
    if not found or index < 0 or index >= len(nodes):
        await callback.answer("Нода не найдена", show_alert=True)
        return
    _group_index, group = found
    api_url, node = nodes[index]
    node_key = make_node_key(api_url, str(node.get("uuid") or ""))
    selected = {str(key) for key in group.get("node_keys") or []}
    if node_key in selected:
        restored = await restore_managed_load_tags(group_id=group_id)
        selected.discard(node_key)
        toast = "▫️ Нода убрана из группы"
        if restored.get("errors"):
            toast += f"; теги хостов ещё восстанавливаются ({len(restored['errors'])})"
    else:
        tag = str(group.get("routing_tag") or "")
        if _same_pool_conflict(groups, group_id, node_key, tag):
            await callback.answer("Эта нода уже добавлена в другую группу с тем же тегом авто-пула", show_alert=True)
            return
        selected.add(node_key)
        toast = "✅ Нода добавлена в группу"
    group["node_keys"] = sorted(selected)
    await _save_load_groups(groups)
    if is_load_monitor_enabled():
        await run_load_monitor_cycle()
    await callback.answer(toast)
    await _show_load_nodes(callback.message, state, max(1, index // REMNAWAVE_HOSTS_PER_PAGE + 1))


@router.callback_query(AdminPanelCallback.filter(F.action.in_({"rw_load_nodes_all", "rw_load_nodes_clear"})))
async def bulk_toggle_load_group_nodes(
    callback: CallbackQuery,
    callback_data: AdminPanelCallback,
    state: FSMContext,
) -> None:
    from services.remnawave_load_monitor import make_node_key, restore_managed_load_tags, run_load_monitor_cycle

    data = await state.get_data()
    group_id = str(data.get("load_group_id") or "")
    groups = _load_groups_copy()
    found = _get_load_group(groups, group_id)
    nodes = await _fetch_all_nodes()
    if not found:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    _group_index, group = found
    page = max(1, int(callback_data.page or 1))
    start = (page - 1) * REMNAWAVE_HOSTS_PER_PAGE
    page_nodes = nodes[start : start + REMNAWAVE_HOSTS_PER_PAGE]
    selected = {str(key) for key in group.get("node_keys") or []}
    choose = callback_data.action == "rw_load_nodes_all"
    changed = False
    for api_url, node in page_nodes:
        node_key = make_node_key(api_url, str(node.get("uuid") or ""))
        if choose:
            if _same_pool_conflict(groups, group_id, node_key, str(group.get("routing_tag") or "")):
                continue
            if node_key not in selected:
                selected.add(node_key)
                changed = True
        elif node_key in selected:
            selected.remove(node_key)
            changed = True
    if changed:
        if not choose:
            await restore_managed_load_tags(group_id=group_id)
        group["node_keys"] = sorted(selected)
        await _save_load_groups(groups)
        if is_load_monitor_enabled():
            await run_load_monitor_cycle()
    toast = "✅ Ноды добавлены" if choose else "▫️ Ноды убраны"
    await callback.answer(toast)
    await _show_load_nodes(callback.message, state, page)


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_load_group_limit"))
async def prompt_load_group_limit(
    callback: CallbackQuery, callback_data: AdminPanelCallback, state: FSMContext
) -> None:
    groups = _load_groups_copy()
    found = _load_group_by_index(groups, callback_data.page)
    if not found:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    _index, group = found
    await state.update_data(load_group_id=str(group.get("id")))
    current = int(group.get("max_cpu_percent") or 0)
    await callback.message.edit_text(
        menu_text(
            f"Порог группы «{html_escape(str(group.get('name') or ''))}»",
            f"Текущий порог: <b>{current}%</b> load average на ядро.",
            quote(
                "Введите процент от 0 до 1000. Значение 0 отключает автоматическое исключение и оставляет только мониторинг."
            ),
        )
    )
    await state.set_state(RemnawaveSettingsState.waiting_for_load_group_limit)
    await callback.answer()


@router.message(RemnawaveSettingsState.waiting_for_load_group_limit)
async def set_load_group_limit(message: Message, state: FSMContext) -> None:
    try:
        value = int((message.text or "").strip())
    except ValueError:
        await message.answer(menu_text("Мониторинг нагрузки", "Введите целое число от 0 до 1000."))
        return
    if not 0 <= value <= 1000:
        await message.answer(menu_text("Мониторинг нагрузки", "Допустимый порог: 0–1000%."))
        return
    data = await state.get_data()
    group_id = str(data.get("load_group_id") or "")
    groups = _load_groups_copy()
    found = _get_load_group(groups, group_id)
    if not found:
        await state.clear()
        await message.answer(menu_text("Мониторинг нагрузки", "Группа не найдена."))
        return
    index, group = found
    if value == 0:
        from services.remnawave_load_monitor import restore_managed_load_tags

        restored = await restore_managed_load_tags(group_id=group_id)
        if restored.get("errors"):
            await message.answer(
                menu_text(
                    "Мониторинг нагрузки", "Не все теги удалось вернуть; настройка порога не изменена. Повторите позже."
                )
            )
            return
    group["max_cpu_percent"] = value
    await _save_load_groups(groups)
    await state.clear()
    await message.answer(
        _load_group_text(group),
        reply_markup=build_remnawave_load_group_kb(group, index + 1),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_load_group_tag"))
async def prompt_load_group_tag(callback: CallbackQuery, callback_data: AdminPanelCallback, state: FSMContext) -> None:
    groups = _load_groups_copy()
    found = _load_group_by_index(groups, callback_data.page)
    if not found:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    _index, group = found
    await state.update_data(load_group_id=str(group.get("id")))
    await callback.message.edit_text(
        menu_text(
            f"Тег авто-пула «{html_escape(str(group.get('name') or ''))}»",
            f"Сейчас: <code>{html_escape(str(group.get('routing_tag') or 'не задан'))}</code>",
            quote(
                "Отправьте тег из Remnawave, например LTE_ROUTING_HOST или ROUTING_HOST. Отправьте дефис, чтобы оставить группу только в режиме наблюдения."
            ),
        )
    )
    await state.set_state(RemnawaveSettingsState.waiting_for_load_group_tag)
    await callback.answer()


@router.message(RemnawaveSettingsState.waiting_for_load_group_tag)
async def set_load_group_tag(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    tag = "" if value == "-" else value
    if len(tag) > 64 or any(char.isspace() for char in tag):
        await message.answer(
            menu_text("Мониторинг нагрузки", "Тег должен быть не длиннее 64 символов и не содержать пробелов.")
        )
        return
    data = await state.get_data()
    group_id = str(data.get("load_group_id") or "")
    groups = _load_groups_copy()
    found = _get_load_group(groups, group_id)
    if not found:
        await state.clear()
        await message.answer(menu_text("Мониторинг нагрузки", "Группа не найдена."))
        return
    index, group = found
    if tag != str(group.get("routing_tag") or ""):
        from services.remnawave_load_monitor import restore_managed_load_tags

        restored = await restore_managed_load_tags(group_id=group_id)
        if restored.get("errors"):
            await message.answer(
                menu_text(
                    "Мониторинг нагрузки",
                    "Не все снятые теги удалось вернуть; тег авто-пула не изменён. Повторите позже.",
                )
            )
            return
    group["routing_tag"] = tag
    await _save_load_groups(groups)
    await state.clear()
    await message.answer(
        _load_group_text(group),
        reply_markup=build_remnawave_load_group_kb(group, index + 1),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_load_group_delete"), flags={"popup": True})
async def delete_load_group(callback: CallbackQuery, callback_data: AdminPanelCallback, state: FSMContext) -> None:
    groups = _load_groups_copy()
    found = _load_group_by_index(groups, callback_data.page)
    if not found:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    _index, group = found
    group_id = str(group.get("id"))
    from services.remnawave_load_monitor import restore_managed_load_tags

    restored = await restore_managed_load_tags(group_id=group_id)
    if restored.get("errors"):
        await callback.answer("Не все снятые теги удалось вернуть. Группа оставлена.", show_alert=True)
        return
    groups = [item for item in groups if str(item.get("id")) != group_id]
    await _save_load_groups(groups)
    await state.clear()
    await callback.answer("Группа удалена")
    await callback.message.edit_text(_load_groups_text(groups), reply_markup=build_remnawave_load_groups_kb(groups))


def _load_snapshot_group_index(action: str) -> int:
    prefix = "rw_load_snapshot_g"
    if not action.startswith(prefix):
        return 0
    try:
        return max(0, int(action[len(prefix) :]))
    except ValueError:
        return 0


def _load_snapshot_node_text(node: dict[str, Any], limit: int) -> str:
    name = html_escape(str(node.get("name") or "Нода"))
    if not node.get("available"):
        return f"▫️ <b>{name}</b>\nНет данных от панели."
    if not node.get("connected"):
        return f"🔴 <b>{name}</b>\nНода недоступна."

    load_percent = node.get("load_percent")
    if load_percent is None:
        return f"🟡 <b>{name}</b>\nСистемные метрики нагрузки недоступны."

    load_avg = node.get("load_avg") or []
    load_values = " / ".join(
        f"{float(load_avg[index]):.2f}" if index < len(load_avg) and load_avg[index] is not None else "—"
        for index in range(3)
    )
    status = "🟠" if limit and load_percent >= limit else "🟢"
    load_label = f"{load_percent:.0f}% на ядро"
    threshold = f"порог {limit}%" if limit else "только наблюдение"
    cores = node.get("cores") or "?"
    memory = f"{node['memory_percent']:.0f}%" if node.get("memory_percent") is not None else "—"
    rx = f"{node['rx_mbps']:.1f}" if node.get("rx_mbps") is not None else "—"
    tx = f"{node['tx_mbps']:.1f}" if node.get("tx_mbps") is not None else "—"
    online = str(node["online"]) if node.get("online") is not None else "—"
    return (
        f"{status} <b>{name}</b>\n"
        f"Load 1/5/15 мин: <code>{load_values}</code>\n"
        f"Нагрузка: {load_label} · {threshold} · ядер: {cores}\n"
        f"RAM: {memory} · RX/TX: {rx}/{tx} Мбит/с · онлайн: {online}"
    )


def _load_snapshot_text(groups: list[dict[str, Any]], group_index: int, page: int) -> tuple[str, int, int]:
    if not groups:
        return menu_text("Срез нагрузки", "Группы мониторинга не настроены."), 0, 1

    group_index = max(0, min(group_index, len(groups) - 1))
    group = groups[group_index]
    nodes = sorted(group.get("nodes") or [], key=lambda node: str(node.get("name") or "").casefold())
    total = len(nodes)
    total_pages = max(1, (total + LOAD_MONITOR_SNAPSHOT_NODES_PER_PAGE - 1) // LOAD_MONITOR_SNAPSHOT_NODES_PER_PAGE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * LOAD_MONITOR_SNAPSHOT_NODES_PER_PAGE
    page_nodes = nodes[start : start + LOAD_MONITOR_SNAPSHOT_NODES_PER_PAGE]
    if not nodes:
        body = "В этой группе ноды не выбраны."
    else:
        end = start + len(page_nodes)
        body = f"Ноды {start + 1}–{end} из {total} · порог: "
        body += f"{group.get('max_cpu_percent')}%" if group.get("max_cpu_percent") else "только наблюдение"
        body += "\n\n" + "\n\n".join(
            _load_snapshot_node_text(node, int(group.get("max_cpu_percent") or 0)) for node in page_nodes
        )
    title = f"Срез нагрузки · {html_escape(str(group.get('name') or 'Группа'))}"
    return menu_text(title, body), group_index, total_pages


@router.callback_query(AdminPanelCallback.filter(F.action.startswith("rw_load_snapshot")))
async def show_load_monitor_snapshot(callback: CallbackQuery, callback_data: AdminPanelCallback) -> None:
    from services.remnawave_load_monitor import get_load_monitor_snapshot

    await callback.answer("Собираю срез нагрузки…")
    try:
        snapshot = await get_load_monitor_snapshot()
    except Exception as exc:
        logger.error("[Remnawave-Admin] Не удалось получить срез нагрузки: {}", exc)
        await callback.message.edit_text(
            menu_text("Срез нагрузки", "Не удалось получить данные панели. Проверьте API-доступ."),
            reply_markup=build_remnawave_load_snapshot_kb([], 0, 1, 1),
        )
        return
    group_index = _load_snapshot_group_index(callback_data.action)
    page = max(1, callback_data.page or 1)
    text, group_index, total_pages = _load_snapshot_text(snapshot, group_index, page)
    reply_markup = build_remnawave_load_snapshot_kb(snapshot, group_index, page, total_pages)
    current_text = callback.message.html_text or callback.message.text or ""
    current_markup = callback.message.reply_markup
    if current_text == text and current_markup and current_markup.model_dump_json() == reply_markup.model_dump_json():
        return
    await callback.message.edit_text(
        text,
        reply_markup=reply_markup,
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_load_restore"))
async def restore_load_monitor_tags_now(callback: CallbackQuery) -> None:
    from services.remnawave_load_monitor import restore_managed_load_tags

    await callback.answer(menu_text("Мониторинг нагрузки", "Возвращаю теги…"))
    result = await restore_managed_load_tags()
    blocks = [section("↩️ Возврат тегов", f"Успешно: {result['restored']}", f"Осталось: {result['remaining']}")]
    if result.get("errors"):
        blocks.append(section("⚠️ Не удалось обработать", *[html_escape(str(item)) for item in result["errors"][:20]]))
    await callback.message.answer(menu_text("Мониторинг нагрузки", *blocks))

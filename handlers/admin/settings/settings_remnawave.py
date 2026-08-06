from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from core.settings.remnawave_config import (
    REMNAWAVE_CONFIG,
    get_host_rotation_allowed,
    get_node_health_allowed,
    is_host_auto_disable_enabled,
    update_remnawave_config,
)
from database import async_session_maker, get_servers
from logger import logger
from panels.remnawave import RemnawaveAPI
from settings.config import REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD, REMNAWAVE_TOKEN_LOGIN_ENABLED

from ..panel.headers import card, menu_text, quote, section
from ..panel.keyboard import AdminPanelCallback
from .keyboard import (
    REMNAWAVE_HOSTS_PER_PAGE,
    build_settings_remnawave_health_nodes_kb,
    build_settings_remnawave_hosts_kb,
    build_settings_remnawave_kb,
    build_settings_remnawave_node_kb,
    build_settings_remnawave_rotation_kb,
)


router = Router(name="admin_settings_remnawave")


class RemnawaveSettingsState(StatesGroup):
    waiting_for_node_interval = State()
    waiting_for_rotation_interval = State()


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


def _root_text() -> str:
    node_state = "✅ Включён" if _node_health_enabled() else "❌ Выключен"
    rot_state = "✅ Включена" if _host_rotation_enabled() else "❌ Выключена"
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
            api = RemnawaveAPI(api_url)
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
            api = RemnawaveAPI(api_url)
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
        reply_markup=build_settings_remnawave_kb(_node_health_enabled(), _host_rotation_enabled()),
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

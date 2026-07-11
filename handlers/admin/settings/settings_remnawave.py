from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD, REMNAWAVE_TOKEN_LOGIN_ENABLED
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
    return (
        "<b>🌀 Remnawave — мониторинг и оптимизация</b>\n\n"
        "Здесь собраны фоновые задачи, которые работают по API панели Remnawave.\n\n"
        f"<b>Проверка нод:</b> {node_state}\n"
        f"  └ интервал: {_node_interval()} мин.\n"
        f"<b>Ротация хостов по нагрузке:</b> {rot_state}\n"
        f"  └ интервал: {_rotation_interval()} мин.\n"
        f"  └ хостов в ротации: <b>{allowed_count}</b>"
    )


def _node_text() -> str:
    state = "✅ Включена" if _node_health_enabled() else "❌ Выключена"
    auto_state = "✅ Включено" if _auto_disable_enabled() else "❌ Выключено"
    selected_count = len(get_node_health_allowed())
    return (
        "<b>🌀 Проверка нод</b>\n\n"
        "Бот опрашивает API панели и следит, какие ноды отвалились.\n"
        "Когда нода переходит из <i>connected</i> в <i>disconnected</i> или обратно — "
        "админы получают уведомление в личку.\n\n"
        f"Статус: {state}\n"
        f"Интервал опроса: <b>{_node_interval()} мин.</b>\n\n"
        f"<b>Авто-отключение хостов:</b> {auto_state}\n"
        "Если нода перестала отвечать — бот выключает её хосты прямо в панели, "
        "чтобы новые подключения не уходили на мёртвый сервер. "
        "Когда нода снова в строю — хосты включаются обратно и заново ротируются.\n"
        "Бот трогает только те хосты, что выключил сам: то, что ты отключил вручную, "
        "останется как есть.\n\n"
        f"<b>Нод выбрано для проверки:</b> {selected_count} (0 = проверяются все)\n"
        "Если выбрать конкретные ноды — бот проверяет только их, а остальные не трогает "
        "(удобно для нод авто-балансировки, которые иначе помечались бы как упавшие).\n\n"
        "Проверка идёт на том же интервале, что и мониторинг нод выше."
    )


def _rotation_text() -> str:
    state = "✅ Включена" if _host_rotation_enabled() else "❌ Выключена"
    allowed = get_host_rotation_allowed()
    return (
        "<b>🔁 Ротация хостов по нагрузке</b>\n\n"
        "Бот считает, сколько пользователей онлайн на каждой ноде Remnawave, "
        "и переставляет наименее нагруженные хосты в начало списка подписки.\n\n"
        "Двигаются только хосты, явно отмеченные в списке ниже. "
        "Остальные сохраняют свои позиции.\n\n"
        f"Статус: {state}\n"
        f"Интервал: <b>{_rotation_interval()} мин.</b>\n"
        f"В ротации: <b>{len(allowed)}</b> хостов"
    )


def _hosts_text(hosts: list[tuple[str, dict[str, Any]]], allowed: set[str]) -> str:
    if not hosts:
        return (
            "<b>📋 Хосты Remnawave</b>\n\n"
            "Не удалось получить список хостов. Проверь, что панель доступна "
            "и API-токен имеет права на чтение <code>/hosts</code>."
        )
    total = len(hosts)
    selected = sum(1 for _, h in hosts if str(h.get("uuid")) in allowed)
    return (
        "<b>📋 Выбор хостов для ротации</b>\n\n"
        f"Всего хостов: <b>{total}</b>\n"
        f"В ротации: <b>{selected}</b>\n\n"
        "Жми по строке, чтобы переключить участие хоста в ротации. "
        "Отмеченные ✅ хосты бот будет двигать по позициям, "
        "ориентируясь на нагрузку привязанной ноды."
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
        return (
            "<b>🖧 Ноды Remnawave</b>\n\n"
            "Не удалось получить список нод. Проверь, что панель доступна "
            "и API-токен имеет права на чтение <code>/nodes</code>."
        )
    total = len(nodes)
    selected = sum(1 for _, n in nodes if str(n.get("uuid")) in allowed)
    return (
        "<b>🖧 Выбор нод для проверки</b>\n\n"
        f"Всего нод: <b>{total}</b>\n"
        f"Выбрано: <b>{selected}</b>\n\n"
        "Жми по строке, чтобы добавить ноду в проверку или убрать. "
        "Бот проверяет и авто-отключает хосты только у ✅ выбранных нод. "
        "Если не выбрано ни одной — проверяются все ноды (как сейчас). "
        "Просто не выбирай ноды авто-балансировки — и бот не будет их трогать."
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
            "<b>⏱ Интервал проверки нод</b>\n\n"
            f"Текущий: <b>{_node_interval()} мин.</b>\n\n"
            "Введите новое значение в минутах (1–1440).\n"
            "Слишком частые опросы могут нагружать панель."
        ),
    )
    await state.set_state(RemnawaveSettingsState.waiting_for_node_interval)
    await callback.answer()


@router.message(RemnawaveSettingsState.waiting_for_node_interval)
async def set_node_interval(message: Message, state: FSMContext) -> None:
    try:
        value = int((message.text or "").strip())
    except ValueError:
        await message.answer("❌ Нужно целое число от 1 до 1440")
        return
    if not 1 <= value <= 1440:
        await message.answer("❌ Допустимый диапазон: 1–1440 минут")
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

    await callback.answer("Синхронизирую…")

    try:
        summary = await sync_hosts_with_node_state()
    except Exception as exc:
        logger.error("[Remnawave-Admin] Ошибка ручной синхронизации хостов: {}", exc)
        await callback.message.answer(f"<b>❌ Ошибка синхронизации</b>\n<code>{exc}</code>")
        return

    lines: list[str] = ["<b>🔌 Синхронизация хостов с нодами</b>", ""]
    lines.append(f"Выключено: <b>{len(summary['disabled'])}</b>")
    lines.append(f"Включено: <b>{len(summary['enabled'])}</b>")
    if summary["disabled"]:
        lines.append("")
        lines.append("<b>⛔ Выключены:</b>")
        for remark in summary["disabled"]:
            lines.append(f"• {remark}")
    if summary["enabled"]:
        lines.append("")
        lines.append("<b>✅ Включены:</b>")
        for remark in summary["enabled"]:
            lines.append(f"• {remark}")
    if summary["errors"]:
        lines.append("")
        lines.append("<b>⚠ Ошибки:</b>")
        for line in summary["errors"]:
            lines.append(f"• {line}")

    await callback.message.answer("\n".join(lines))


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
        text=(
            "<b>⏱ Интервал ротации</b>\n\n"
            f"Текущий: <b>{_rotation_interval()} мин.</b>\n\n"
            "Введите новое значение в минутах (5–1440)."
        ),
    )
    await state.set_state(RemnawaveSettingsState.waiting_for_rotation_interval)
    await callback.answer()


@router.message(RemnawaveSettingsState.waiting_for_rotation_interval)
async def set_rotation_interval(message: Message, state: FSMContext) -> None:
    try:
        value = int((message.text or "").strip())
    except ValueError:
        await message.answer("❌ Нужно целое число от 5 до 1440")
        return
    if not 5 <= value <= 1440:
        await message.answer("❌ Допустимый диапазон: 5–1440 минут")
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

    await callback.answer("Запускаю ротацию…")

    try:
        summary = await run_host_rotation()
    except Exception as exc:
        logger.error("[Remnawave-Admin] Ошибка ручной ротации: {}", exc)
        await callback.message.answer(f"<b>❌ Ошибка ротации</b>\n<code>{exc}</code>")
        return

    lines: list[str] = ["<b>🔀 Ручная ротация хостов</b>", ""]
    lines.append(f"Хостов в ротации: <b>{summary['allowed_count']}</b>")
    lines.append(f"Панелей: <b>{summary['panels']}</b>")
    lines.append(f"Переставлено: <b>{summary['moved_total']}</b>")
    if summary["details"]:
        lines.append("")
        lines.append("<b>Детали:</b>")
        for line in summary["details"]:
            lines.append(f"• {line}")
    if summary["errors"]:
        lines.append("")
        lines.append("<b>⚠ Ошибки:</b>")
        for line in summary["errors"]:
            lines.append(f"• {line}")

    await callback.message.answer("\n".join(lines))


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_rot_hosts"))
async def open_rotation_hosts(callback: CallbackQuery, callback_data: AdminPanelCallback) -> None:
    await callback.answer("Загружаю хосты…")
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
        toast = "▫️ Хост убран из ротации"
    else:
        allowed.add(host_uuid)
        toast = "✅ Хост добавлен в ротацию"

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
    await callback.answer("✅ Включены")
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
    await callback.answer("▫️ Сброшено")
    await callback.message.edit_text(
        text=_hosts_text(hosts, allowed),
        reply_markup=build_settings_remnawave_hosts_kb(page, hosts, allowed),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_node_sel"))
async def open_health_nodes(callback: CallbackQuery, callback_data: AdminPanelCallback) -> None:
    await callback.answer("Загружаю ноды…")
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
        toast = "▫️ Нода убрана из проверки"
    else:
        allowed.add(node_uuid)
        toast = "✅ Нода добавлена в проверку"

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
    await callback.answer("✅ Выбраны")
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
    await callback.answer("▫️ Сброшено")
    await callback.message.edit_text(
        text=_health_nodes_text(nodes, allowed),
        reply_markup=build_settings_remnawave_health_nodes_kb(page, nodes, allowed),
    )

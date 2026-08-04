import asyncio

from html import escape as html_escape
from typing import Any

from core.settings.remnawave_config import (
    REMNAWAVE_CONFIG,
    get_host_auto_disabled,
    get_host_rotation_allowed,
    get_node_health_allowed,
    is_host_auto_disable_enabled,
    is_host_rotation_enabled,
    is_node_health_enabled,
    update_remnawave_config,
)
from core.settings.web_config import get_web_node_status_interval_min, is_web_enabled
from database import async_session_maker, get_servers
from logger import logger
from panels.remnawave import RemnawaveAPI
from settings.config import ADMIN_ID, REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD, REMNAWAVE_TOKEN_LOGIN_ENABLED


NODE_HEALTH_DEFAULT_INTERVAL_MIN = 5
HOST_ROTATION_DEFAULT_INTERVAL_MIN = 60
TICK_SLEEP_SEC = 30


async def get_client_node_statuses(session, allowed_squad_uuids: set[str] | None = None) -> list[dict]:
    """Реальные точки подключения клиента для показа в кабинете.

    Источник — хосты Remnawave (host.address:host.port), из которых собирается
    подписка, т.е. ровно тот endpoint, к которому коннектится устройство клиента.
    Снапшот строится в _node_health_tick и кэшируется в CLIENT_CONNECTION_TARGETS,
    чтобы публичный запрос не дёргал панель.

    Скрыты выключенные вручную (host.isDisabled в панели и servers.enabled=false в боте).
    Если задан allowed_squad_uuids — оставляем только хосты, чей inbound входит в эти сквады
    (т.е. серверы из тарифа конкретного юзера). None — без фильтра по сквадам (для админа).
    """
    targets = list(REMNAWAVE_CONFIG.get("CLIENT_CONNECTION_TARGETS") or [])
    if not targets:
        return []

    allowed_inbounds: set[str] | None = None
    if allowed_squad_uuids is not None:
        squad_inbounds = dict(REMNAWAVE_CONFIG.get("SQUAD_INBOUNDS") or {})
        allowed_inbounds = set()
        for squad in allowed_squad_uuids:
            for ib in squad_inbounds.get(str(squad)) or []:
                allowed_inbounds.add(str(ib))
        target_inbounds = {str(t.get("inbound_uuid") or "") for t in targets}
        logger.info(
            "[NodeStatus] squads={} squad_map={} allowed_inbounds={} target_inbounds={}",
            sorted(str(s) for s in allowed_squad_uuids),
            len(squad_inbounds),
            sorted(allowed_inbounds),
            sorted(i for i in target_inbounds if i),
        )

    servers = await get_servers(session, include_enabled=True)
    enabled_by_api: dict[str, bool] = {}
    for server_list in servers.values():
        for s in server_list:
            api = (s.get("api_url") or "").rstrip("/")
            if api:
                enabled_by_api[api] = bool(s.get("enabled"))

    out: list[dict] = []
    for tgt in targets:
        api_url = str(tgt.get("api_url") or "")
        if enabled_by_api.get(api_url.rstrip("/"), True) is False:
            continue
        if allowed_inbounds is not None and str(tgt.get("inbound_uuid") or "") not in allowed_inbounds:
            continue
        out.append({
            "uuid": str(tgt.get("uuid") or ""),
            "online": bool(tgt.get("online")),
            "name": tgt.get("name") or "",
            "load": int(tgt.get("load") or 0),
            "host": tgt.get("host") or "",
            "port": tgt.get("port"),
            "position": int(tgt.get("position") or 0),
        })
    return out


async def _collect_remnawave_panels() -> list[str]:
    async with async_session_maker() as session:
        servers = await get_servers(session, include_enabled=True)

    seen: set[str] = set()
    panels: list[str] = []
    for cluster in servers.values():
        for srv in cluster:
            if srv.get("panel_type") != "remnawave":
                continue
            api_url = (srv.get("api_url") or "").strip()
            if not api_url or api_url in seen:
                continue
            seen.add(api_url)
            panels.append(api_url)
    return panels


async def _login_api(api_url: str) -> RemnawaveAPI | None:
    api = RemnawaveAPI(api_url)
    try:
        if REMNAWAVE_TOKEN_LOGIN_ENABLED:
            return api
        ok = await api.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD)
        if not ok:
            await api.aclose()
            return None
        return api
    except Exception as exc:
        logger.warning("[Remnawave-Monitor] Логин на {} провалился: {}", api_url, exc)
        try:
            await api.aclose()
        except Exception:
            pass
        return None


def _build_servers_kb():
    from aiogram.types import InlineKeyboardButton
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    from handlers.admin.panel.keyboard import AdminPanelCallback

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🖥️ К серверам",
            callback_data=AdminPanelCallback(action="clusters").pack(),
        )
    )
    return builder.as_markup()


async def _send_to_admins(bot, text: str, reply_markup=None) -> None:
    if not ADMIN_ID:
        return
    for admin_id in ADMIN_ID:
        try:
            await bot.send_message(admin_id, text, reply_markup=reply_markup)
        except Exception as exc:
            logger.warning("[Remnawave-Monitor] Не удалось отправить уведомление {}: {}", admin_id, exc)


def _is_node_alive(node: dict[str, Any]) -> bool:
    if node.get("isDisabled"):
        return True
    return bool(node.get("isConnected"))


async def _node_health_tick(bot, alerts_enabled: bool = True) -> None:
    panels = await _collect_remnawave_panels()
    if not panels:
        return

    last_states: dict[str, dict[str, Any]] = dict(REMNAWAVE_CONFIG.get("NODE_HEALTH_LAST_STATES") or {})
    allowed = get_node_health_allowed()
    next_states: dict[str, dict[str, Any]] = {}
    next_targets: list[dict[str, Any]] = []
    next_squad_inbounds: dict[str, list[str]] = {}
    alerts: list[tuple[str, dict[str, Any], bool]] = []

    for api_url in panels:
        api = await _login_api(api_url)
        if api is None:
            continue
        hosts_raw: Any = []
        squads_raw: list[dict] = []
        try:
            nodes = await api.get_all_nodes() or []
            hosts_raw = await api.get_hosts() or []
            squads_raw = await api.get_internal_squads() or []
        finally:
            try:
                await api.aclose()
            except Exception:
                pass

        for sq in squads_raw:
            uuid = str(sq.get("uuid") or "")
            if uuid:
                next_squad_inbounds[uuid] = [str(i) for i in (sq.get("inbounds") or []) if i]

        try:
            hosts = _normalize_hosts(hosts_raw)
            if hosts:
                next_targets.extend(_build_connection_targets(api_url, nodes, hosts))
            else:
                logger.warning(
                    "[Remnawave-Monitor] {}: список хостов пуст/неизвестной формы — targets не построены", api_url
                )
        except Exception as exc:
            logger.error("[Remnawave-Monitor] {}: ошибка сборки connection targets: {}", api_url, exc)

        for node in nodes:
            uuid = node.get("uuid")
            if not uuid:
                continue
            if allowed and str(uuid) not in allowed:
                continue
            alive = _is_node_alive(node)
            state_key = f"{api_url}::{uuid}"
            next_states[state_key] = {
                "alive": alive,
                "name": node.get("name") or "",
                "address": node.get("address") or "",
                "isDisabled": bool(node.get("isDisabled")),
                "lastStatusMessage": node.get("lastStatusMessage") or "",
                "usersOnline": int(node.get("usersOnline") or 0),
            }
            previous = last_states.get(state_key)
            previously_alive = previous.get("alive", True) if previous else True
            if alive != previously_alive:
                alerts.append((state_key, next_states[state_key], alive))

    if alerts and alerts_enabled:
        lines: list[str] = ["<b>🌀 Remnawave: изменение состояния нод</b>"]
        for _, info, alive in alerts:
            name = html_escape(info.get("name") or "—")
            address = html_escape(info.get("address") or "—")
            if alive:
                lines.append(f"✅ <b>{name}</b> ({address}) — снова на связи")
            else:
                reason = info.get("lastStatusMessage") or "соединение потеряно"
                lines.append(f"⚠️ <b>{name}</b> ({address}) — {html_escape(str(reason))}")
        await _send_to_admins(bot, "\n".join(lines), reply_markup=_build_servers_kb())

    new_cfg: dict[str, Any] | None = None
    if next_states != last_states:
        new_cfg = dict(REMNAWAVE_CONFIG)
        new_cfg["NODE_HEALTH_LAST_STATES"] = next_states
    if next_targets and next_targets != list(REMNAWAVE_CONFIG.get("CLIENT_CONNECTION_TARGETS") or []):
        new_cfg = new_cfg if new_cfg is not None else dict(REMNAWAVE_CONFIG)
        new_cfg["CLIENT_CONNECTION_TARGETS"] = next_targets
    if next_squad_inbounds and next_squad_inbounds != dict(REMNAWAVE_CONFIG.get("SQUAD_INBOUNDS") or {}):
        new_cfg = new_cfg if new_cfg is not None else dict(REMNAWAVE_CONFIG)
        new_cfg["SQUAD_INBOUNDS"] = next_squad_inbounds
    if new_cfg is not None:
        async with async_session_maker() as session:
            await update_remnawave_config(session, new_cfg)


def _build_inbound_load_map(nodes: list[dict[str, Any]]) -> dict[str, int]:
    """Сумма usersOnline по нодам, на которых активен каждый inbound."""
    load: dict[str, int] = {}
    for node in nodes:
        if node.get("isDisabled") or not node.get("isConnected"):
            continue
        online = int(node.get("usersOnline") or 0)
        inbounds = (node.get("configProfile") or {}).get("activeInbounds") or []
        for inbound in inbounds:
            inbound_uuid = inbound.get("uuid")
            if not inbound_uuid:
                continue
            load[str(inbound_uuid)] = load.get(str(inbound_uuid), 0) + online
    return load


def _normalize_hosts(raw: Any) -> list[dict[str, Any]]:
    """Приводит ответ get_hosts() к списку dict — разные версии Remnawave
    отдают либо список, либо обёртку {hosts/response/items/data: [...]}."""
    if isinstance(raw, list):
        return [h for h in raw if isinstance(h, dict)]
    if isinstance(raw, dict):
        for key in ("hosts", "response", "items", "data"):
            val = raw.get(key)
            if isinstance(val, list):
                return [h for h in val if isinstance(h, dict)]
    return []


def _build_connection_targets(
    api_url: str, nodes: list[dict[str, Any]], hosts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Реальные клиентские endpoint'ы из хостов панели: host.address:host.port.

    online/load берутся по inbound хоста (жива ли обслуживающая нода и её нагрузка).
    Выключенные вручную хосты (isDisabled) пропускаются.
    """
    alive = _build_inbound_alive_map(nodes)
    load = _build_inbound_load_map(nodes)
    node_dicts = [n for n in nodes if isinstance(n, dict)]
    any_node_alive = any(_is_node_alive(n) for n in node_dicts)
    have_nodes = bool(node_dicts)
    out: list[dict[str, Any]] = []
    for host in hosts:
        if not isinstance(host, dict) or host.get("isDisabled"):
            continue
        host_uuid = host.get("uuid")
        if not host_uuid:
            continue
        ib_uuid = _host_inbound_uuid(host)
        address = (host.get("address") or "").strip()
        raw_port = host.get("port")
        try:
            port = int(raw_port) if raw_port is not None else None
        except (TypeError, ValueError):
            port = None
        try:
            position = int(host.get("viewPosition") or 0)
        except (TypeError, ValueError):
            position = 0
        if ib_uuid and ib_uuid in alive:
            online = bool(alive[ib_uuid])
        elif have_nodes:
            online = any_node_alive
        else:
            online = True
        out.append({
            "uuid": str(host_uuid),
            "api_url": api_url,
            "name": host.get("remark") or address or str(host_uuid),
            "host": address,
            "port": port,
            "position": position,
            "online": online,
            "load": int(load.get(ib_uuid, 0)) if ib_uuid else 0,
            "inbound_uuid": ib_uuid,
        })
    logger.info(
        "[Remnawave-Monitor] {}: hosts={} nodes={} alive_inbounds={} any_alive={} online={}",
        api_url,
        len(hosts),
        len(node_dicts),
        len(alive),
        any_node_alive,
        sum(1 for t in out if t["online"]),
    )
    return out


def _host_inbound_uuid(host: dict[str, Any]) -> str | None:
    inbound = host.get("inbound") or {}
    uuid = inbound.get("configProfileInboundUuid")
    return str(uuid) if uuid else None


async def run_host_rotation() -> dict[str, Any]:
    """Запускает один проход ротации. Возвращает summary для UI/логов.

    Структура:
      {
        "allowed_count": int,
        "panels": int,
        "moved_total": int,
        "details": [str, ...],
        "errors": [str, ...],
      }
    """
    result: dict[str, Any] = {
        "allowed_count": 0,
        "panels": 0,
        "moved_total": 0,
        "details": [],
        "errors": [],
    }

    allowed = get_host_rotation_allowed()
    result["allowed_count"] = len(allowed)
    if not allowed:
        result["details"].append("Нет хостов в ротации — отметь хосты в списке.")
        return result

    panels = await _collect_remnawave_panels()
    result["panels"] = len(panels)
    if not panels:
        result["details"].append("Нет доступных Remnawave-панелей.")
        return result

    for api_url in panels:
        api = await _login_api(api_url)
        if api is None:
            result["errors"].append(f"{api_url}: не удалось залогиниться")
            continue
        try:
            nodes = await api.get_all_nodes() or []
            hosts_data = await api.get_hosts() or []

            if not isinstance(hosts_data, list) or not hosts_data:
                logger.info("[Remnawave-Monitor] {}: список хостов пуст — пропуск", api_url)
                result["details"].append(f"{api_url}: список хостов пуст")
                continue

            inbound_load = _build_inbound_load_map(nodes)
            logger.info(
                "[Remnawave-Monitor] {}: нагрузка по inbound = {}",
                api_url,
                inbound_load,
            )

            hosts_sorted = sorted(hosts_data, key=lambda h: int(h.get("viewPosition") or 0))

            movable: list[dict[str, Any]] = []
            movable_positions: list[int] = []
            new_layout: list[dict[str, Any] | None] = []
            for idx, host in enumerate(hosts_sorted):
                host_uuid = host.get("uuid")
                if not host_uuid:
                    continue
                if str(host_uuid) in allowed:
                    movable.append(host)
                    movable_positions.append(idx)
                    new_layout.append(None)
                else:
                    new_layout.append(host)

            if len(movable) < 2:
                logger.info(
                    "[Remnawave-Monitor] {}: в ротации меньше 2 хостов ({}) — нечего двигать",
                    api_url,
                    len(movable),
                )
                result["details"].append(f"{api_url}: в ротации меньше 2 хостов ({len(movable)})")
                continue

            def host_load(host: dict[str, Any]) -> int:
                ib_uuid = _host_inbound_uuid(host)
                if ib_uuid and ib_uuid in inbound_load:
                    return inbound_load[ib_uuid]
                return 10**9

            movable_sorted = sorted(movable, key=host_load)

            for slot_idx, host in zip(movable_positions, movable_sorted, strict=False):
                new_layout[slot_idx] = host

            reorder_payload: list[dict[str, Any]] = []
            moves: list[str] = []
            changed = False
            for idx, host in enumerate(new_layout):
                if host is None:
                    continue
                new_view_position = idx + 1
                reorder_payload.append({"uuid": host["uuid"], "viewPosition": new_view_position})
                old_pos = int(host.get("viewPosition") or 0)
                if old_pos != new_view_position:
                    changed = True
                    if str(host.get("uuid")) in allowed:
                        ib_uuid = _host_inbound_uuid(host)
                        load_for_host = inbound_load.get(ib_uuid or "", "?")
                        remark = host.get("remark") or host.get("address") or host["uuid"]
                        moves.append(f"'{remark}' ({old_pos}→{new_view_position}, online={load_for_host})")

            if not changed:
                logger.info(
                    "[Remnawave-Monitor] {}: нагрузка не изменила порядок — пропуск",
                    api_url,
                )
                result["details"].append(f"{api_url}: порядок уже оптимален")
                continue

            ok = await api.reorder_hosts(reorder_payload)
            if ok:
                result["moved_total"] += len(moves)
                result["details"].append(f"{api_url}: переставлено {len(moves)} хостов")
                logger.info(
                    "[Remnawave-Monitor] {}: переставлено {} хостов. Изменения: {}",
                    api_url,
                    len(moves),
                    "; ".join(moves) if moves else "—",
                )
            else:
                result["errors"].append(f"{api_url}: reorder API вернул ошибку")
        except Exception as exc:
            result["errors"].append(f"{api_url}: {exc}")
            logger.error("[Remnawave-Monitor] {} ошибка ротации: {}", api_url, exc)
        finally:
            try:
                await api.aclose()
            except Exception:
                pass

    return result


async def _host_rotation_tick() -> None:
    await run_host_rotation()


def _node_serves_traffic(node: dict[str, Any]) -> bool:
    return bool(node.get("isConnected")) and not node.get("isDisabled")


def _build_inbound_alive_map(nodes: list[dict[str, Any]]) -> dict[str, bool]:
    """Для каждого inbound — есть ли хотя бы одна живая нода, которая его обслуживает."""
    allowed = get_node_health_allowed()
    alive: dict[str, bool] = {}
    for node in nodes:
        serving = _node_serves_traffic(node) or (bool(allowed) and str(node.get("uuid") or "") not in allowed)
        inbounds = (node.get("configProfile") or {}).get("activeInbounds") or []
        for inbound in inbounds:
            inbound_uuid = inbound.get("uuid")
            if not inbound_uuid:
                continue
            key = str(inbound_uuid)
            alive[key] = alive.get(key, False) or serving
    return alive


async def sync_hosts_with_node_state(bot=None) -> dict[str, Any]:
    """Выключает хосты упавших нод и включает обратно те, что мы сами выключали.

    При восстановлении хотя бы одного хоста — запускает ротацию (если она включена).
    Возвращает summary для UI/логов.
    """
    result: dict[str, Any] = {"disabled": [], "enabled": [], "errors": []}

    panels = await _collect_remnawave_panels()
    if not panels:
        result["errors"].append("Нет доступных Remnawave-панелей.")
        return result

    auto_disabled = get_host_auto_disabled()
    changed_auto = False
    recovered_any = False

    for api_url in panels:
        api = await _login_api(api_url)
        if api is None:
            result["errors"].append(f"{api_url}: не удалось залогиниться")
            continue
        try:
            nodes = await api.get_all_nodes() or []
            if not nodes:
                result["errors"].append(f"{api_url}: список нод пуст — пропуск")
                continue
            hosts_data = await api.get_hosts() or []
            if not isinstance(hosts_data, list) or not hosts_data:
                continue

            inbound_alive = _build_inbound_alive_map(nodes)

            for host in hosts_data:
                host_uuid = host.get("uuid")
                if not host_uuid:
                    continue
                host_uuid = str(host_uuid)
                ib_uuid = _host_inbound_uuid(host)
                if ib_uuid is None:
                    continue
                inbound_down = not inbound_alive.get(ib_uuid, False)
                currently_disabled = bool(host.get("isDisabled"))
                remark = host.get("remark") or host.get("address") or host_uuid

                if inbound_down:
                    if not currently_disabled:
                        ok = await api.set_host_enabled(host_uuid, False)
                        if ok:
                            auto_disabled.add(host_uuid)
                            changed_auto = True
                            result["disabled"].append(remark)
                        else:
                            result["errors"].append(f"{remark}: не удалось выключить")
                elif host_uuid in auto_disabled:
                    if currently_disabled:
                        ok = await api.set_host_enabled(host_uuid, True)
                        if not ok:
                            result["errors"].append(f"{remark}: не удалось включить")
                            continue
                        result["enabled"].append(remark)
                        recovered_any = True
                    auto_disabled.discard(host_uuid)
                    changed_auto = True
        except Exception as exc:
            result["errors"].append(f"{api_url}: {exc}")
            logger.error("[Remnawave-Monitor] {} ошибка host-sync: {}", api_url, exc)
        finally:
            try:
                await api.aclose()
            except Exception:
                pass

    if changed_auto:
        new_cfg = dict(REMNAWAVE_CONFIG)
        new_cfg["HOST_AUTO_DISABLED"] = sorted(auto_disabled)
        async with async_session_maker() as session:
            await update_remnawave_config(session, new_cfg)

    if result["disabled"] or result["enabled"]:
        logger.info(
            "[Remnawave-Monitor] Авто-синхронизация хостов: выключено={}, включено={}",
            len(result["disabled"]),
            len(result["enabled"]),
        )

    if bot is not None and (result["disabled"] or result["enabled"]):
        lines: list[str] = ["<b>🔌 Remnawave: авто-управление хостами</b>"]
        if result["disabled"]:
            lines.append("")
            lines.append("<b>⛔ Выключены (нода недоступна):</b>")
            for remark in result["disabled"]:
                lines.append(f"• {html_escape(str(remark))}")
        if result["enabled"]:
            lines.append("")
            lines.append("<b>✅ Снова включены (нода ожила):</b>")
            for remark in result["enabled"]:
                lines.append(f"• {html_escape(str(remark))}")
        await _send_to_admins(bot, "\n".join(lines), reply_markup=_build_servers_kb())

    if recovered_any and is_host_rotation_enabled():
        try:
            await run_host_rotation()
        except Exception as exc:
            result["errors"].append(f"rotation: {exc}")
            logger.error("[Remnawave-Monitor] Ошибка ротации после восстановления: {}", exc)

    return result


async def remnawave_monitor_loop(bot, _sessionmaker) -> None:
    last_node_tick = -1e9
    last_rotation_tick = 0.0
    last_sync_tick = 0.0

    loop = asyncio.get_event_loop()
    while True:
        try:
            now = loop.time()
            node_interval = (
                max(1, int(REMNAWAVE_CONFIG.get("NODE_HEALTH_INTERVAL_MIN") or NODE_HEALTH_DEFAULT_INTERVAL_MIN)) * 60
            )
            rotation_interval = (
                max(5, int(REMNAWAVE_CONFIG.get("HOST_ROTATION_INTERVAL_MIN") or HOST_ROTATION_DEFAULT_INTERVAL_MIN))
                * 60
            )

            node_health_on = is_node_health_enabled()
            tick_intervals = []
            if node_health_on:
                tick_intervals.append(node_interval)
            if is_web_enabled():
                tick_intervals.append(get_web_node_status_interval_min() * 60)
            if tick_intervals and (now - last_node_tick) >= min(tick_intervals):
                last_node_tick = now
                try:
                    await _node_health_tick(bot, alerts_enabled=node_health_on)
                except Exception as exc:
                    logger.error("[Remnawave-Monitor] Ошибка node health tick: {}", exc)

            if is_host_auto_disable_enabled() and (now - last_sync_tick) >= node_interval:
                last_sync_tick = now
                try:
                    await sync_hosts_with_node_state(bot)
                except Exception as exc:
                    logger.error("[Remnawave-Monitor] Ошибка host-sync tick: {}", exc)

            if is_host_rotation_enabled() and (now - last_rotation_tick) >= rotation_interval:
                last_rotation_tick = now
                try:
                    await _host_rotation_tick()
                except Exception as exc:
                    logger.error("[Remnawave-Monitor] Ошибка host rotation tick: {}", exc)
        except Exception as exc:
            logger.error("[Remnawave-Monitor] Внешняя ошибка: {}", exc)

        await asyncio.sleep(TICK_SLEEP_SEC)

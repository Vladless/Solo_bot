import copy
import ipaddress
import re
import time

from .checks import check_node_runtime
from .types import NODE_VERSION, InstallError, InstallSpec


def _reports(value) -> list[dict]:
    """Оставляет только безопасные сведения о состоянии нод."""
    if not isinstance(value, list):
        return []
    failures = {
        "none",
        "disabled",
        "connecting",
        "timeout",
        "connection_refused",
        "no_route",
        "tls",
        "authentication",
        "xray_configuration",
        "node_version_mismatch",
        "xray_not_running",
        "not_connected",
    }
    result = []
    for item in value[:100]:
        if not isinstance(item, dict):
            continue
        failure = item.get("failure")
        row = {"failure": failure if isinstance(failure, str) and failure in failures else "not_connected"}
        for key in ("expected_port", "actual_port"):
            port = item.get(key)
            if type(port) is int and 1 <= port <= 65535:
                row[key] = port
        for key in ("isConnected", "isDisabled", "isConnecting"):
            if type(item.get(key)) is bool:
                row[key] = item[key]
        for key in ("node_version", "xray_version"):
            version = item.get(key)
            if isinstance(version, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", version) and len(version) <= 64:
                row[key] = version
        name = item.get("name")
        if (
            isinstance(name, str)
            and 0 < len(name) <= 100
            and all(ord(char) >= 32 and ord(char) != 127 for char in name)
        ):
            row["name"] = name
        role = item.get("role")
        if isinstance(role, str) and role in {"normal", "entry", "exit", "haproxy_exit"}:
            row["role"] = role
        result.append(row)
    return result


def _endpoint(spec: InstallSpec, artifacts: dict, reports: list[dict], index: int) -> str:
    """Выбирает проверенный адрес API нужного выхода."""
    address, port = spec.address, spec.node_port
    if spec.role == "haproxy_entry":
        routes = spec.cascade_specs()
        if index >= len(routes):
            return "на порту управления выхода"
        route = routes[index]
        state = artifacts if index == 0 else (artifacts.get("cascade_routes") or {}).get(route.exit_install_id, {})
        target = state.get("haproxy") or {}
        address = target.get("node_address")
        report = reports[index] if index < len(reports) else {}
        port = target.get("node_port") or report.get("actual_port") or report.get("expected_port")
    try:
        address = str(ipaddress.ip_address(address))
    except (ValueError, TypeError):
        return "на порту управления выхода" if spec.role == "haproxy_entry" else "на порту управления ноды"
    if type(port) is not int or not 1 <= port <= 65535:
        return address
    return f"[{address}]:{port}" if ":" in address else f"{address}:{port}"


def _runtime_reason(runtime: dict) -> str:
    """Объясняет подтверждённый локальный сбой контейнера."""
    return {
        "missing": "Контейнер Remnawave-ноды не найден на сервере.",
        "foreign": "Контейнер Remnawave-ноды принадлежит другой установке; его настройки не изменены.",
        "stopped": "Контейнер Remnawave-ноды остановлен. Проверьте причину его завершения.",
        "restarting": "Контейнер Remnawave-ноды постоянно перезапускается. Проверьте настройки запуска.",
        "port_mismatch": "Порт NODE_PORT в контейнере отличается от порта управления в установщике.",
        "secret_mismatch": "Сертификат связи с панелью в контейнере отличается от сохранённого установщиком.",
        "management_listener_missing": "Контейнер запущен, но не слушает порт управления нодой.",
        "management_listener_foreign": "Порт управления нодой занят процессом вне её контейнера.",
        "address_unreachable": "Порт управления недоступен по выбранному IP при проверке с самой ноды.",
    }.get(runtime.get("status"), "")


async def connection_failure(panel, remote, spec: InstallSpec, artifacts: dict, checkpoint) -> str:
    """Уточняет отказ подключения без изменения сервера и панели."""
    reports, runtime = [], {}
    try:
        reports = _reports(await panel.node_diagnostics(spec, artifacts))
    except InstallError:
        pass
    if remote is not None and spec.role != "haproxy_entry":
        try:
            runtime = await check_node_runtime(remote, spec, artifacts)
        except InstallError:
            runtime = {"status": "unavailable"}
    artifacts["node_diagnostics"] = {"checked_at": time.time(), "panel": reports, "runtime": copy.deepcopy(runtime)}
    if checkpoint is not None:
        try:
            await checkpoint(artifacts)
        except InstallError:
            pass
    index = next((index for index, report in enumerate(reports) if report["failure"] != "none"), 0)
    report = reports[index] if reports else {}
    failure = report.get("failure")
    prefix = ""
    if spec.role == "haproxy_entry" and report.get("name"):
        prefix = "Выход «" + report["name"][:60] + "»: "
    if failure == "disabled":
        return (prefix + "Нода отключена в панели. Включите её для продолжения проверки.")[:500]
    local = _runtime_reason(runtime)
    if local:
        return (prefix + local)[:500]
    endpoint = _endpoint(spec, artifacts, reports, index)
    message = {
        "timeout": "Панель не дождалась ответа от " + endpoint + ".",
        "connection_refused": "Соединение панели с " + endpoint + " отклонено сервером.",
        "no_route": "Панель не имеет сетевого маршрута к " + endpoint + ".",
        "tls": "Панель не подтвердила TLS-сертификат связи с нодой. Проверьте сертификат и время сервера.",
        "authentication": "Панель отклонила аутентификацию ноды. Проверьте сертификат связи с панелью.",
        "xray_configuration": "Панель сообщила об ошибке запуска Xray. Проверьте конфигурацию профиля ноды.",
        "node_version_mismatch": "Панель видит версию ноды "
        + report.get("node_version", "не определена")
        + "; установщик ожидает "
        + NODE_VERSION
        + ".",
        "xray_not_running": "Нода доступна панели, но Xray ещё не подтвердил запуск.",
        "connecting": "Панель продолжает подключаться к " + endpoint + ".",
    }.get(failure, "Панель ещё не подтвердила подключение ноды.")
    if runtime.get("status") == "local_ready":
        message += " Локально порт API открыт; проверьте соединение со стороны панели."
    if runtime.get("ufw") == "inactive":
        message += " UFW выключен."
    elif runtime.get("ufw") == "not_installed":
        message += " UFW не установлен."
    elif runtime.get("ufw_management_deny") is True:
        message += " В UFW есть запрещающее правило для порта управления."
    elif runtime.get("ufw") == "active" and runtime.get("ufw_management_allow") is False:
        message += " В UFW не найдено отдельное разрешение порта управления."
    if runtime.get("management_filter") is True:
        message += " Ограничение IP панели активно; проверьте исходящий IP панели."
    elif runtime.get("management_filter") is False:
        message += " Ограничение IP панели установщиком не включено."
    return (prefix + message)[:500]

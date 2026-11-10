from collections.abc import Sequence

from .types import DELETABLE_STATUSES, ENTRY_ROLES, TCP_REALITY_TRANSPORTS


_INSTALL_LABELS = {
    "keys": "Подготовка ключей",
    "panel_profile": "Создание профиля",
    "panel_node": "Создание ноды в панели",
    "docker": "Настройка контейнера ноды",
    "addons": "Настройка дополнений",
    "verify": "Проверка подключения ноды",
    "cdn": "Настройка CDN и сертификата",
    "panel_squad": "Подключение к скваду",
    "chain_verify": "Проверка всей цепочки каскада",
    "panel_host": "Публикация хоста",
}
_EDIT_LABELS = {
    "connection": "Проверка SSH-доступа",
    "prepare": "Подготовка изменённых настроек",
    "cascade": "Настройка выхода каскада",
    "profile": "Обновление профиля",
    "remote": "Применение настроек сервера",
    "node": "Обновление ноды в панели",
    "cloud": "Обновление CDN и сертификата",
    "verify": "Проверка подключения ноды",
    "squad": "Изменение привязки к скваду",
    "host": "Обновление хоста",
}
_DELETE_LABELS = {
    "waiting": "Ожидание удаления входных нод каскада",
    "server_cleanup": "Удаление ноды с VPS",
    "cloud_cleanup": "Удаление ресурсов CDN",
    "cloud_resource": "Удаление CDN-ресурса",
    "cloud_origin_group": "Удаление origin-группы CDN",
    "cloud_certificate": "Удаление сертификата CDN",
    "panel_cleanup": "Удаление объектов Remnawave",
    "panel_host": "Удаление хоста",
    "panel_node": "Удаление ноды из панели",
    "panel_squad": "Удаление привязки к скваду",
    "panel_detach": "Удаление привязки к скваду",
    "panel_user": "Удаление технического пользователя каскада",
    "panel_private_squad": "Удаление сквада каскада",
    "panel_profile": "Удаление профиля",
    "cascade": "Удаление технического доступа каскада",
    "record": "Удаление записи из бота",
}
_EXTRA_LABELS = {
    "preflight": "Проверка сервера перед установкой",
    "panel": "Проверка панели Remnawave",
    "ssh": "Проверка SSH-доступа",
    "ssh_host_key": "Проверка ключа сервера",
    "files": "Подготовка файлов",
    "certificate": "Выпуск сертификата origin",
    "nginx": "Настройка TLS-прокси",
    "origin": "Проверка origin",
    "firewall": "Настройка сетевого доступа",
    "exit_ip": "Проверка выходного IP",
    "cascade_verify": "Проверка связи между нодами",
    "cascade_chain": "Проверка всей цепочки каскада",
    "chain_probe": "Подготовка тестового подключения каскада",
    "bbr": "Настройка BBR",
    "fail2ban": "Настройка fail2ban",
    "warp": "Настройка WARP",
    "psiphon": "Настройка Psiphon",
    "antitorrent": "Настройка анти-торрента",
    "ufw": "Настройка UFW",
    "shaping": "Настройка ограничений скорости",
    "haproxy": "Настройка каскада HAProxy",
    "haproxy_verify": "Проверка TCP-соединения через HAProxy",
    "waiting_exit": "Ожидание сервера 2 — выхода каскада",
    "cloud_auth": "Проверка доступа к облаку",
    "snapshot": "Проверка сохранённых параметров",
    "validation": "Проверка параметров",
    "permission": "Проверка прав администратора",
    "secrets": "Проверка сохранённых доступов",
    "manage": "Изменение настроек ноды",
}


def _steps(value: object) -> list[str]:
    """Оставляет уникальные имена этапов в исходном порядке."""
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    return list(dict.fromkeys(item for item in value if isinstance(item, str) and item))


def _installation_steps(stages: Sequence[str], params: dict | None) -> list[str]:
    """Исключает действия, не выбранные для этой установки."""
    result = _steps(stages)
    if params is None:
        return result
    cdn = str(params.get("transport") or "").startswith("cdn_")
    shaping = params.get("shaping") or {}
    excluded = set()
    if params.get("role") == "haproxy_entry":
        excluded.update({"keys", "panel_profile", "panel_node"})
    if not cdn:
        excluded.add("cdn")
    if not params.get("squad_uuid") or params.get("role") == "exit":
        excluded.add("panel_squad")
    if not params.get("publish_host", True) or params.get("role") in {"exit", "haproxy_exit"}:
        excluded.add("panel_host")
    if params.get("role") not in ENTRY_ROLES or params.get("transport") not in TCP_REALITY_TRANSPORTS:
        excluded.add("chain_verify")
    if not params.get("addons") and not shaping.get("enabled", bool(shaping)):
        excluded.add("addons")
    return [stage for stage in result if stage not in excluded]


def build_progress(
    status: str,
    stage: str,
    artifacts: dict,
    stages: Sequence[str],
    params: dict | None = None,
) -> dict:
    """Считает завершённые этапы без оценки оставшегося времени."""
    finished = status in {"succeeded", "warning"}
    deletion = artifacts.get("deletion")
    if isinstance(deletion, dict):
        operations = _steps(deletion.get("operations") or ["panel_cleanup", "record"])
        done = set(_steps(deletion.get("done")))
        cleanup = artifacts.get("panel_cleanup")
        if isinstance(cleanup, dict) and isinstance(cleanup.get("steps"), list):
            panel_steps = _steps([step.get("key") for step in cleanup["steps"] if isinstance(step, dict)])
            operations = _steps([
                step
                for operation in operations
                for step in (panel_steps if operation == "panel_cleanup" else [operation])
            ])
            done.update(_steps(cleanup.get("done")))
        completed = sum(operation in done for operation in operations)
        total = len(operations)
        percent = min(99, completed * 100 // total) if total else 0
        key = stage.removeprefix("delete:")
        pending = next((operation for operation in operations if operation not in done), "record")
        return {
            "completed": completed,
            "total": total,
            "percent": percent,
            "bar": "▰" * (percent // 10) + "▱" * (10 - percent // 10),
            "label": f"{completed} из {total} этапов · {percent}%",
            "current": _DELETE_LABELS.get(key, _DELETE_LABELS.get(pending, "Удаление объектов Remnawave")),
        }
    edit = artifacts.get("edit")
    if not isinstance(edit, dict) and finished:
        edit = (artifacts.get("last_edit") or {}).get("progress")
    editing = isinstance(edit, dict)
    operations = _steps(edit.get("operations")) if editing else _installation_steps(stages, params)
    done = set(_steps(edit.get("done") if editing else artifacts.get("done")))
    total = len(operations)
    completed = total if finished else sum(operation in done for operation in operations)
    percent = 100 if finished else min(99, completed * 100 // total) if total else 0
    filled = percent // 10
    bar = "▰" * filled + "▱" * (10 - filled)
    if total:
        label = f"{completed} из {total} этапов · {percent}%"
    else:
        label = "Параметры сохранены · 100%" if finished else "Сохранение параметров · 0%"
    labels = _EDIT_LABELS if editing else _INSTALL_LABELS
    if params and params.get("role") == "haproxy_entry":
        labels = {**labels, "docker": "Настройка HAProxy", "verify": "Проверка каскада HAProxy"}
    key = stage.removeprefix("edit:")
    pending = next((operation for operation in operations if operation not in done), None)
    if finished:
        current = "Изменения применены" if editing else "Установка завершена"
    elif status == "draft":
        current = "Заполнение параметров установки"
    elif key in labels and key not in done:
        current = labels[key]
    elif key in _EXTRA_LABELS:
        current = _EXTRA_LABELS[key]
    elif pending:
        current = labels.get(pending, "Выполнение этапа")
    elif editing and not operations:
        current = "Сохранение параметров"
    else:
        current = "Завершение изменений" if editing else "Завершение установки"
    return {"completed": completed, "total": total, "percent": percent, "bar": bar, "label": label, "current": current}


def job_status_text(view: dict) -> str:
    """Собирает одинаковую безопасную карточку для меню и фоновых обновлений."""
    from html import escape

    statuses = {
        "draft": "Черновик",
        "queued": "В очереди",
        "running": "Установка",
        "waiting_dns": "Ожидание DNS / облака",
        "failed": "Ошибка",
        "interrupted": "Прервана",
        "succeeded": "Готово",
        "warning": "Готово с предупреждением",
        "canceled": "Отменена",
        "cancel_requested": "Останавливается",
        "paused": "Приостановлена для изменения настроек",
        "pause_requested": "Останавливается с сохранением доступа",
    }
    params = view.get("pending_params") or view.get("params") or {}
    status = view.get("status", "draft")
    progress = view.get("progress") or {}
    label = statuses.get(status, "Установка")
    checking_connection = status == "waiting_dns" and view.get("stage") in {"verify", "edit:verify"}
    if checking_connection:
        label = "Ожидание подключения ноды"
    attachment = view.get("extra_exit_attachment") or {}
    attachment_status = attachment.get("status")
    attaching = status in {"succeeded", "warning"} and attachment and attachment_status != "succeeded"
    if attaching:
        label = (
            "Выход установлен · Подключение к каскаду не завершено"
            if attachment_status in {"pending", "failed"}
            else "Выход установлен · Подключение к каскаду"
        )
    elif status in {"succeeded", "warning"} and attachment_status == "succeeded":
        label = "Выход подключён к каскаду"
    if view.get("deletion"):
        label = {
            "queued": "В очереди на удаление",
            "running": "Удаление",
            "waiting_dns": "Ожидание подтверждения удаления",
            "failed": "Ошибка удаления",
            "paused": "Удаление приостановлено",
            "canceled": "Удаление остановлено",
        }.get(status, label)
    lines = [
        "<b>" + escape(str(params.get("name") or "Новая нода")[:80]) + "</b> · " + label,
        "Сервер: <code>" + escape(str(params.get("address") or "не задано")[:64]) + "</code>",
    ]
    if params.get("cascade_address"):
        lines.append("IP связи между нодами: <code>" + escape(str(params["cascade_address"])[:64]) + "</code>")
    pair = view.get("cascade_pair") or {}
    if pair:
        entry = view.get("id") == pair.get("entry_id")
        lines.insert(1, "Нода 1 — вход HAProxy" if entry else "Нода 2 — выход HAProxy")
        lines.append("Связанный сервер: нода 2 — выход" if entry else "Связанный сервер: нода 1 — вход")
    if params.get("cascade_routes"):
        lines.append("Направлений каскада: " + str(1 + len(params["cascade_routes"])))
    if attachment:
        address = str(attachment.get("entry_address") or "")
        endpoint = f"[{address}]" if ":" in address else address
        lines.append(
            "Клиентский адрес: <code>" + escape(endpoint + ":" + str(attachment.get("client_port") or "")) + "</code>"
        )
        if attaching:
            lines.append(
                "Откройте карточку входа, чтобы увидеть причину и продолжить подключение."
                if attachment_status == "failed"
                else "Выход готов. Обновление HAProxy, проверка направлений и публикация хоста выполняются на входной ноде."
                if attachment_status == "queued"
                else "План подключения обновлён. Откройте сводку и подтвердите адрес нового направления."
                if attachment_status == "planned"
                else "Автоматическое подключение к входу ещё не завершено. Повторите подключение из этой карточки."
            )
        if attachment.get("error") and not view.get("error"):
            lines.append("Подключение: " + escape(str(attachment["error"])[:512]))
    chain = view.get("chain_verification") or {}
    if status in {"succeeded", "warning"} and chain.get("status") == "passed":
        routes = chain.get("routes") or []
        passed = sum(route.get("status") == "passed" for route in routes if isinstance(route, dict))
        if routes and passed == len(routes):
            lines.append(f"Цепочка проверена: {passed}/{len(routes)} направлений")
    if progress:
        lines.append(
            "<code>" + escape(str(progress.get("bar", ""))) + "</code> " + escape(str(progress.get("label", "")))
        )
    current = progress.get("current") or (
        "Удаление объектов Remnawave"
        if view.get("deletion")
        else "Заполнение параметров установки"
        if status == "draft"
        else "Подготовка установки"
    )
    if attaching:
        current = "Подключение выхода к HAProxy и проверка каскада"
    lines.append("Этап: " + escape(str(current)[:180]))
    if view.get("error"):
        lines.append("Ошибка: " + escape(str(view["error"])[:512]))
    for record in (view.get("dns_records") or [])[:6]:
        name, value = str(record.get("name", "")), str(record.get("value", ""))
        lines.extend([
            "",
            "DNS · " + escape(str(record.get("type") or "CNAME")),
            "Имя: <code>" + escape(name) + "</code>",
            "Значение: <code>" + escape(value) + "</code>",
        ])
    logs = view.get("logs") or []
    log_limit = min(1500, max(0, 4000 - len("\n".join(lines))))
    if logs and log_limit:
        lines.append("<pre>" + escape("\n".join(str(line) for line in logs[-12:])[-log_limit:]) + "</pre>")
    return "\n".join(lines)


def job_actions(view: dict) -> list[tuple[str, str, str]]:
    """Возвращает действия карточки без зависимости от Telegram."""
    status = view.get("status", "draft")
    editable = {"draft", "failed", "waiting_dns", "paused", "succeeded", "warning"}
    actions = []
    attachment = view.get("extra_exit_attachment") or {}
    pair = view.get("cascade_pair") or {}
    if pair:
        actions.append(("Нода 2 — выход" if view.get("id") == pair.get("entry_id") else "Нода 1 — вход", "partner", ""))
    can_delete = view.get("can_delete", status in DELETABLE_STATUSES)
    if view.get("deletion"):
        if status in {"failed", "paused", "canceled", "interrupted"}:
            actions.append(("Повторить удаление", "retry", ""))
            if view.get("deletion_requires_credentials") and can_delete:
                actions.append(("Обновить доступы для удаления", "auth", ""))
        if status in {"queued", "running", "waiting_dns"}:
            actions.append(("Остановить удаление", "stopask", ""))
        if can_delete:
            actions.append(("Удалить запись", "delask", ""))
        actions.extend([("Обновить", "show", ""), ("Все установки", "list", "")])
        return actions
    if status == "draft":
        params = view.get("params") or {}
        step = "type" if params.get("role", "normal") == "normal" and not params.get("address") else "1"
        actions.extend([("Продолжить мастер установки", "step", step), ("Сводка", "summary", "")])
    if status in editable or status in {"queued", "running"}:
        actions.append(("Изменить настройки", "edit", ""))
    params = view.get("pending_params") or view.get("params") or {}
    if status in editable and params.get("role") in ENTRY_ROLES:
        actions.append(("Выходы и страны", "routes", ""))
    if status in editable and (
        view.get("stage") == "ssh_host_key"
        or ("ключ" in str(view.get("error") or "").lower() and "измен" in str(view.get("error") or "").lower())
    ):
        actions.insert(0, ("Проверить новый ключ сервера", "resetfp", ""))
    if (view.get("resources") or {}).get("cdn") or view.get("retained_resources"):
        actions.append(("Ресурсы CDN", "res", ""))
    if status in {"succeeded", "warning"}:
        if not attachment or attachment.get("status") == "succeeded":
            actions.append(("Переустановить эту ноду", "riask", "all"))
        if view.get("extra_exit_for"):
            actions.insert(
                0,
                (
                    "Ход подключения к каскаду"
                    if attachment.get("status") == "queued"
                    else "Карточка входной ноды"
                    if attachment
                    else "Подключить к входной ноде",
                    "routeparent",
                    "",
                ),
            )
    elif status in editable and view.get("extra_exit_for"):
        actions.insert(0, ("К входной ноде", "routeparent", ""))
    if status in {"succeeded", "warning", "failed"} and attachment.get("status") == "pending":
        actions.insert(0, ("Проверить план подключения", "refreshattachment", ""))
    if status in {"succeeded", "warning", "failed"} and attachment.get("status") == "planned":
        actions.insert(0, ("Подтвердить подключение выхода", "summary", ""))
    if status in editable:
        actions.append(("Обновить учётные данные", "auth", ""))
    if status == "failed" and attachment.get("status") != "planned":
        actions.append(("Повторить установку", "retry", ""))
    if status in {"waiting_dns", "warning"} and view.get("retryable", True) and attachment.get("status") != "planned":
        actions.append((
            "Повторить проверку подключения"
            if view.get("stage") in {"verify", "edit:verify"}
            else "Повторить подключение к входу"
            if attachment.get("status") == "pending"
            else "Повторить / проверить DNS",
            "retry",
            "",
        ))
    if status == "paused":
        actions.append(("Продолжить установку без изменений", "retry", ""))
    if status in {"queued", "running", "waiting_dns"}:
        actions.append(("Остановить установку", "stopask", ""))
    if can_delete:
        actions.append((
            "Удалить черновик"
            if status == "draft"
            else "Удалить ноду"
            if status in {"succeeded", "warning"}
            else "Удалить запись",
            "delask",
            "",
        ))
    actions.extend([("Обновить", "show", ""), ("Все установки", "list", "")])
    return actions

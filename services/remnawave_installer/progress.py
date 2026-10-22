from collections.abc import Sequence

from .types import DELETABLE_STATUSES


_INSTALL_LABELS = {
    "keys": "Подготовка ключей",
    "panel_profile": "Создание профиля",
    "panel_node": "Создание ноды в панели",
    "docker": "Настройка контейнера ноды",
    "addons": "Настройка дополнений",
    "verify": "Проверка подключения ноды",
    "cdn": "Настройка CDN и сертификата",
    "panel_squad": "Подключение к скваду",
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
    "bbr": "Настройка BBR",
    "fail2ban": "Настройка fail2ban",
    "warp": "Настройка WARP",
    "psiphon": "Настройка Psiphon",
    "antitorrent": "Настройка анти-торрента",
    "ufw": "Настройка UFW",
    "shaping": "Настройка ограничений скорости",
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
    if not cdn:
        excluded.add("cdn")
    if not params.get("squad_uuid") or params.get("role") == "exit":
        excluded.add("panel_squad")
    if not params.get("publish_host", True) or params.get("role") == "exit":
        excluded.add("panel_host")
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
            operations = _steps([
                *[step.get("key") for step in cleanup["steps"] if isinstance(step, dict)],
                "record",
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
    if view.get("deletion"):
        label = {
            "queued": "В очереди на удаление",
            "running": "Удаление",
            "failed": "Ошибка удаления",
            "paused": "Удаление приостановлено",
            "canceled": "Удаление остановлено",
        }.get(status, label)
    lines = [
        "<b>" + escape(str(params.get("name") or "Новая нода")[:80]) + "</b> · " + label,
        "Сервер: " + escape(str(params.get("address") or "не задано")[:64]),
    ]
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
    lines.append("Этап: " + escape(str(current)[:180]))
    if view.get("error"):
        lines.append("Ошибка: " + escape(str(view["error"])[:512]))
    for record in (view.get("dns_records") or [])[:6]:
        name, value = str(record.get("name", ""))[:100], str(record.get("value", ""))[:120]
        lines.append("DNS: " + escape(name) + " → " + escape(value))
    logs = view.get("logs") or []
    if logs:
        lines.append("<pre>" + escape("\n".join(str(line) for line in logs[-12:])[-1500:]) + "</pre>")
    return "\n".join(lines)


def job_actions(view: dict) -> list[tuple[str, str, str]]:
    """Возвращает действия карточки без зависимости от Telegram."""
    status = view.get("status", "draft")
    editable = {"draft", "failed", "waiting_dns", "paused", "succeeded", "warning"}
    actions = []
    can_delete = view.get("can_delete", status in DELETABLE_STATUSES)
    if view.get("deletion"):
        if status in {"failed", "paused", "canceled", "interrupted"}:
            actions.append(("Повторить удаление", "retry", ""))
        if status in {"queued", "running"}:
            actions.append(("Остановить удаление", "stopask", ""))
        if can_delete:
            actions.append(("Удалить запись", "delask", ""))
        actions.extend([("Обновить", "show", ""), ("Все установки", "list", "")])
        return actions
    if status == "draft":
        actions.extend([("Продолжить мастер установки", "step", "1"), ("Сводка", "summary", "")])
    if status in editable or status in {"queued", "running"}:
        actions.append(("Изменить настройки", "edit", ""))
    if status in editable and (
        view.get("stage") == "ssh_host_key"
        or ("ключ" in str(view.get("error") or "").lower() and "измен" in str(view.get("error") or "").lower())
    ):
        actions.insert(0, ("Проверить новый ключ сервера", "resetfp", ""))
    if (view.get("resources") or {}).get("cdn") or view.get("retained_resources"):
        actions.append(("Ресурсы CDN", "res", ""))
    if status in {"succeeded", "warning"}:
        actions.append(("Переустановить эту ноду", "riask", "all"))
    if status in editable:
        actions.append(("Обновить учётные данные", "auth", ""))
    if status == "failed":
        actions.append(("Повторить установку", "retry", ""))
    if status in {"waiting_dns", "warning"} and view.get("retryable", True):
        actions.append(("Повторить / проверить DNS", "retry", ""))
    if status == "paused":
        actions.append(("Продолжить установку без изменений", "retry", ""))
    if status in {"queued", "running", "waiting_dns"}:
        actions.append(("Остановить установку", "stopask", ""))
    if can_delete:
        actions.append(("Удалить черновик" if status == "draft" else "Удалить запись", "delask", ""))
    actions.extend([("Обновить", "show", ""), ("Все установки", "list", "")])
    return actions

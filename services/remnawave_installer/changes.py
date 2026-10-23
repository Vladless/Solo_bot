import copy

from dataclasses import fields

from .types import InstallSpec


LABELS = {
    "panel_ref": "Панель",
    "name": "Имя ноды",
    "address": "IP сервера",
    "role": "Роль ноды",
    "ssh_username": "SSH-логин",
    "ssh_port": "SSH-порт",
    "host_fingerprint": "Доверенный ключ сервера",
    "transport": "Транспорт",
    "client_port": "Порт клиентов",
    "node_port": "Порт управления",
    "reality_domain": "Домен Reality",
    "domain": "Домен сайта / origin",
    "cdn_domain": "Домен CDN",
    "email": "Email сертификата",
    "site_template": "Шаблон сайта",
    "profile_name": "Имя профиля",
    "host_remark": "Название хоста",
    "squad_uuid": "Сквад",
    "exit_install_id": "Выход каскада",
    "exit_ip": "Выходной IP",
    "exit_interface": "Интерфейс выходного IP",
    "panel_sources": "Адреса панели",
    "trusted_ssh_sources": "Доверенные адреса SSH",
    "addons": "Дополнения",
    "shaping": "Шейпинг",
    "cloud": "Доступ и проект / каталог облака",
    "replace_existing": "Разрешение замены",
    "publish_host": "Публикация хоста",
}
STEP_LABELS = {
    "connection": "Проверить изменённый SSH-доступ",
    "prepare": "Подготовить только изменённые ключи, порты и выходной адрес",
    "cascade": "Обновить только связь с выходом каскада",
    "profile": "Обновить существующий профиль с сохранением UUID инбаунда",
    "remote": "Применить выбранные настройки сервера и дополнений",
    "node": "Обновить существующую ноду в панели",
    "cloud": "Обновить настройки CDN и проверить DNS / сертификат",
    "verify": "Проверить подключение ноды",
    "squad": "Обновить привязку к скваду, сохранив остальные инбаунды",
    "host": "Обновить существующий хост",
}
PROFILE_FIELDS = {
    "transport",
    "client_port",
    "reality_domain",
    "domain",
    "profile_name",
    "addons",
    "exit_ip",
    "exit_install_id",
}
REMOTE_FIELDS = {
    "transport",
    "client_port",
    "node_port",
    "domain",
    "email",
    "site_template",
    "exit_ip",
    "exit_interface",
    "panel_sources",
    "trusted_ssh_sources",
    "ssh_port",
    "addons",
    "shaping",
}
CLOUD_FIELDS = {"transport", "domain", "cdn_domain", "email", "cloud"}
HOST_FIELDS = {"host_remark", "transport", "client_port", "domain", "cdn_domain", "publish_host"}
CONNECTION_FIELDS = {"ssh_username", "ssh_port", "host_fingerprint"}


def changed_fields(old: InstallSpec, new: InstallSpec) -> set[str]:
    """Выделяет только изменившиеся параметры."""
    return {item.name for item in fields(InstallSpec) if getattr(old, item.name) != getattr(new, item.name)}


def is_installed(artifacts: dict) -> bool:
    """Определяет завершённую установку по контрольным точкам."""
    return bool(artifacts.get("installed")) or {"verify", "panel_host", "panel_squad"}.issubset(
        artifacts.get("done", [])
    )


def build_change_plan(
    old: InstallSpec, new: InstallSpec, artifacts: dict, *, visible: InstallSpec | None = None
) -> dict:
    """Строит точечный план без сетевых вызовов и изменений сервера."""
    from .cloud import cloud_change_plan

    changed = changed_fields(old, new)
    visible = visible or old
    displayed = changed_fields(visible, new)
    resources = bool(artifacts.get("profile_uuid") or artifacts.get("node_uuid") or artifacts.get("remote_deployed"))
    immutable = changed & {"panel_ref", "address", "role"}
    if resources and immutable:
        raise ValueError(
            "Изменение «"
            + ", ".join(LABELS[field] for field in sorted(immutable))
            + "» переносит ноду на другой сервер или меняет её роль. Для этого нужна отдельная установка; остальные параметры можно изменить здесь"
        )
    cloud = cloud_change_plan(old, new)
    operations = []
    remote_needed = bool(artifacts.get("remote_deployed") and changed & REMOTE_FIELDS)
    prepare = (
        bool(artifacts.get("profile_uuid") and changed & {"transport", "exit_ip", "exit_interface"}) or remote_needed
    )
    if changed & CONNECTION_FIELDS:
        operations.append("connection")
    if prepare:
        operations.append("prepare")
    if artifacts.get("profile_uuid") and old.role == "entry" and "exit_install_id" in changed:
        operations.append("cascade")
    if artifacts.get("profile_uuid") and changed & PROFILE_FIELDS:
        operations.append("profile")
    if remote_needed:
        operations.append("remote")
    if artifacts.get("node_uuid") and changed & {"name", "node_port"}:
        operations.append("node")
    if (
        artifacts.get("remote_deployed")
        and changed & CLOUD_FIELDS
        and (old.transport.startswith("cdn_") or new.transport.startswith("cdn_"))
    ):
        operations.append("cloud")
    if (
        artifacts.get("node_uuid")
        and artifacts.get("remote_deployed")
        and changed
        & {
            "transport",
            "client_port",
            "node_port",
            "reality_domain",
            "domain",
            "exit_ip",
            "exit_interface",
            "panel_sources",
            "addons",
            "exit_install_id",
        }
    ):
        operations.append("verify")
    if (
        artifacts.get("inbound_uuid")
        and (artifacts.get("remote_deployed") or artifacts.get("squad_uuid"))
        and "squad_uuid" in changed
    ):
        operations.append("squad")
    if (artifacts.get("host_uuid") or is_installed(artifacts)) and changed & HOST_FIELDS:
        operations.append("host")
    warnings = []
    if remote_needed or "profile" in operations and changed - {"profile_name"}:
        warnings.append("Во время применения сетевых настроек возможно короткое прерывание подключения к этой ноде")
    if cloud.get("requires_replacement"):
        warnings.append(cloud.get("reason") or "Провайдер требует отдельный CDN-ресурс для новых параметров")
        warnings.append("Старые CDN-ресурсы сохранятся; их оплата у провайдера продолжится до ручного удаления")
    if "squad_uuid" in changed and resources:
        warnings.append("Изменится доступ пользователей выбранных сквадов к этому инбаунду")
    if (
        changed & {"transport", "reality_domain", "domain", "cdn_domain", "client_port"}
        and old.role == "exit"
        and artifacts.get("dependent_entries")
    ):
        raise ValueError(
            "Параметры выхода используются входными нодами каскада. Изменяйте выход через отдельную миграцию, чтобы не разорвать действующие каскады"
        )
    return {
        "changes": [
            {
                "field": field,
                "label": LABELS.get(field, field),
                "before": copy.deepcopy(getattr(visible, field)),
                "after": copy.deepcopy(getattr(new, field)),
            }
            for field in sorted(displayed)
        ],
        "changed": sorted(changed),
        "operations": operations,
        "steps": [STEP_LABELS[step] for step in operations]
        or ["Сохранить параметры и продолжить с незавершённого этапа"],
        "warnings": warnings,
        "requires_replacement": bool(cloud.get("requires_replacement")),
    }

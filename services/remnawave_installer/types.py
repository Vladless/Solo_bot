import hashlib
import ipaddress
import json
import re

from dataclasses import asdict, dataclass, field, fields
from typing import Any
from uuid import UUID


PANEL_VERSION = "3.2.1"
NODE_VERSION = "3.4.3"
NODE_IMAGE = f"remnawave/node:{NODE_VERSION}"
TRANSPORTS = {
    "vless_tcp_reality": "VLESS TCP Reality",
    "vless_xhttp_reality": "VLESS XHTTP Reality",
    "vless_encryption": "VLESS Encryption",
    "vless_tcp_selfsteal": "VLESS Selfsteal",
    "cdn_vk": "CDN VK",
    "cdn_yandex": "CDN Яндекс",
}
ROLES = {"normal": "Обычная", "entry": "Вход каскада", "exit": "Выход каскада"}
ADDONS = {
    "bbr": "BBR",
    "fail2ban": "fail2ban",
    "warp": "WARP",
    "psiphon": "Psiphon",
    "antitorrent": "Анти-торрент",
    "ufw": "UFW",
}
FINAL_STATUSES = frozenset({"succeeded", "warning", "canceled"})
DELETABLE_STATUSES = frozenset({"draft", "failed", "paused", "canceled", "interrupted"})


class InstallError(ValueError):
    def __init__(self, step: str, message: str, *, retryable: bool = True) -> None:
        self.step = step
        self.message = message
        self.retryable = retryable
        super().__init__(message)


class InstallWaitingError(Exception):
    def __init__(self, message: str, *, records: list[dict] | None = None, retry_after: int = 60) -> None:
        self.message = message
        self.records = records or []
        self.retry_after = retry_after
        super().__init__(message)


def _domain(value: str) -> str:
    """Проверяет и нормализует доменное имя."""
    value = value.strip().rstrip(".").lower()
    try:
        value = value.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise ValueError("Некорректный домен") from error
    if len(value) > 253 or not re.fullmatch(
        r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", value
    ):
        raise ValueError("Укажите домен без протокола, порта и пути")
    return value


def _default_node_name(address: str) -> str:
    """Создаёт короткое стабильное имя из IP сервера."""
    name = "node-" + re.sub(r"[^a-zA-Z0-9-]", "-", address)
    if len(name) > 30:
        name = name[:21].rstrip("-") + "-" + hashlib.sha256(address.encode()).hexdigest()[:8]
    return name


@dataclass
class InstallSpec:
    panel_ref: str = ""
    name: str = ""
    address: str = ""
    ssh_username: str = "root"
    ssh_port: int = 22
    host_fingerprint: str = ""
    transport: str = "vless_tcp_reality"
    role: str = "normal"
    client_port: int = 443
    node_port: int = 2222
    reality_domain: str = "www.samsung.com"
    domain: str = ""
    cdn_domain: str = ""
    email: str = ""
    site_template: str = "standard"
    profile_name: str = ""
    host_remark: str = ""
    squad_uuid: str | None = None
    exit_install_id: str | None = None
    exit_ip: str = ""
    exit_interface: str = ""
    panel_sources: list[str] = field(default_factory=list)
    trusted_ssh_sources: list[str] = field(default_factory=list)
    addons: list[str] = field(default_factory=list)
    shaping: dict[str, Any] = field(default_factory=dict)
    cloud: dict[str, Any] = field(default_factory=dict)
    replace_existing: bool = False
    publish_host: bool = True

    @classmethod
    def from_dict(cls, value: dict, *, complete: bool = True) -> "InstallSpec":
        """Проверяет параметры установки без секретов."""
        names = {item.name for item in fields(cls)}
        unknown = set(value) - names
        if unknown:
            raise ValueError("Неизвестные параметры установки: " + ", ".join(sorted(unknown)))
        spec = cls(**value)
        for item in fields(cls):
            current = getattr(spec, item.name)
            if isinstance(item.default, str) and not isinstance(current, str):
                raise ValueError("Текстовый параметр имеет неверный тип: " + item.name)
        for name in ("replace_existing", "publish_host"):
            if type(getattr(spec, name)) is not bool:
                raise ValueError("Логический параметр имеет неверный тип: " + name)
        if spec.transport not in TRANSPORTS or spec.role not in ROLES:
            raise ValueError("Неизвестный транспорт или роль ноды")
        if spec.site_template not in {"standard", "streaming"}:
            raise ValueError("Неизвестный шаблон сайта")
        if spec.role == "exit":
            spec.publish_host = False
        for name in ("ssh_port", "client_port", "node_port"):
            port = getattr(spec, name)
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError("Порты должны быть целыми числами от 1 до 65535")
        if not isinstance(spec.addons, list) or any(addon not in ADDONS for addon in spec.addons):
            raise ValueError("Неизвестное дополнение")
        spec.addons = list(dict.fromkeys(spec.addons))
        if not isinstance(spec.cloud, dict) or set(spec.cloud) - {"folder_id", "project_id", "auth_mode"}:
            raise ValueError("Неизвестные параметры облака; ключи доступа вводятся отдельно")
        if spec.cloud.get("auth_mode", "token") not in ("token", "service_account"):
            raise ValueError("Неизвестный способ авторизации VK Cloud")
        folder_id = spec.cloud.get("folder_id", "")
        if not isinstance(folder_id, str) or (folder_id and not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", folder_id)):
            raise ValueError("Некорректный ID каталога облака")
        project_id = spec.cloud.get("project_id", "")
        if not isinstance(project_id, str) or (project_id and not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", project_id)):
            raise ValueError("Некорректный ID проекта VK Cloud")
        shaping_types = {
            "enabled": bool,
            "ceiling_mbps": int,
            "penalty_mbps": int,
            "threshold_seconds": int,
            "penalty_seconds": int,
            "volume_gb": int,
            "volume_window_hours": int,
        }
        if not isinstance(spec.shaping, dict) or set(spec.shaping) - shaping_types.keys():
            raise ValueError("Неизвестные параметры шейпинга")
        for name, value_type in shaping_types.items():
            if name in spec.shaping and type(spec.shaping[name]) is not value_type:
                raise ValueError("Неверный тип параметра шейпинга: " + name)
            if (
                name in spec.shaping
                and value_type is int
                and not (0 if name == "volume_gb" else 1) <= spec.shaping[name] <= 1_000_000
            ):
                raise ValueError("Параметр шейпинга вне допустимого диапазона: " + name)
        if spec.shaping and (spec.transport.startswith("cdn_") or spec.role == "exit"):
            raise ValueError("Шейпинг доступен только для прямых транспортов и входа каскада")
        for name in ("panel_sources", "trusted_ssh_sources"):
            values = getattr(spec, name)
            if not isinstance(values, list):
                raise ValueError("Адреса доступа передаются списком IP или CIDR")
            try:
                setattr(spec, name, [str(ipaddress.ip_network(item, strict=False)) for item in values])
            except (ValueError, TypeError) as error:
                raise ValueError("Некорректный IP или CIDR доступа") from error
        if spec.host_fingerprint and not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", spec.host_fingerprint):
            raise ValueError("Некорректный отпечаток SSH-ключа сервера")
        if not spec.name and spec.address:
            try:
                address = str(ipaddress.ip_address(spec.address.strip()))
            except ValueError:
                if complete:
                    raise ValueError("Укажите IP-адрес сервера") from None
            else:
                spec.address = address
                spec.name = _default_node_name(address)
        if not complete:
            return spec
        if not spec.panel_ref or len(spec.panel_ref) > 255:
            raise ValueError("В конфиге бота не настроен адрес панели Remnawave")
        if any(ipaddress.ip_network(item).prefixlen == 0 for item in spec.panel_sources + spec.trusted_ssh_sources):
            raise ValueError("Укажите конкретные доверенные адреса вместо доступа со всего интернета")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]{2,29}", spec.name):
            raise ValueError("Имя ноды: 3–30 символов, латиница, цифры, пробел, дефис и подчёркивание")
        try:
            spec.address = str(ipaddress.ip_address(spec.address.strip()))
        except (ValueError, AttributeError) as error:
            raise ValueError("Укажите IP-адрес сервера") from error
        if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", spec.ssh_username):
            raise ValueError("Некорректный SSH-логин")
        spec.profile_name = spec.profile_name or spec.name[:21].rstrip(" _-") + "-profile"
        if not re.fullmatch(r"[A-Za-z0-9 _-]{2,29}", spec.profile_name):
            raise ValueError("Имя профиля: 2–29 символов, латиница, цифры, пробел, дефис и подчёркивание")
        spec.host_remark = spec.host_remark.strip() or spec.name
        if len(spec.host_remark) > 100:
            raise ValueError("Название хоста не должно превышать 100 символов")
        spec.reality_domain = _domain(spec.reality_domain)
        if spec.transport in {"vless_tcp_selfsteal", "cdn_vk", "cdn_yandex"}:
            spec.domain = _domain(spec.domain)
            if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", spec.email) or len(spec.email) > 254:
                raise ValueError("Для сертификата укажите email")
        if spec.transport.startswith("cdn_"):
            spec.cdn_domain = _domain(spec.cdn_domain)
            if spec.domain == spec.cdn_domain:
                raise ValueError("Origin и CDN должны иметь разные домены")
        if spec.role == "entry" and not spec.exit_install_id:
            raise ValueError("Выберите установленную выходную ноду каскада")
        if spec.exit_install_id is not None and (
            not isinstance(spec.exit_install_id, str) or not re.fullmatch(r"[a-f0-9]{32}", spec.exit_install_id)
        ):
            raise ValueError("Некорректный ID выходной ноды")
        if spec.squad_uuid:
            try:
                UUID(spec.squad_uuid)
            except (ValueError, TypeError, AttributeError) as error:
                raise ValueError("Некорректный UUID сквада") from error
        if spec.role == "exit" and spec.transport != "vless_tcp_reality":
            raise ValueError("Для выхода каскада выберите VLESS TCP Reality")
        if spec.exit_ip:
            try:
                spec.exit_ip = str(ipaddress.ip_address(spec.exit_ip))
            except ValueError as error:
                raise ValueError("Некорректный выходной IP") from error
            if spec.exit_interface and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,32}", spec.exit_interface):
                raise ValueError("Некорректное имя сетевого интерфейса")
        if len({spec.ssh_port, spec.client_port, spec.node_port}) != 3:
            raise ValueError("Порты SSH, клиентов и управления нодой должны различаться")
        if spec.transport in {"vless_tcp_selfsteal", "cdn_vk", "cdn_yandex"} and 80 in {
            spec.ssh_port,
            spec.node_port,
            spec.client_port,
        }:
            raise ValueError("Порт 80 необходим для выпуска сертификата")
        return spec

    def to_dict(self) -> dict:
        """Возвращает параметры для сохранения задачи."""
        return asdict(self)

    def fingerprint(self) -> str:
        """Привязывает снимок установки к неизменным параметрам."""
        payload = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

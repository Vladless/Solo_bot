import copy
import hashlib
import ipaddress
import json
import re
import unicodedata

from dataclasses import asdict, dataclass, field, fields
from typing import Any
from uuid import UUID

from .naming import node_name_length


PANEL_MIN_VERSION = (3, 0, 0)
NODE_VERSION = "3.4.3"
NODE_IMAGE = f"remnawave/node:{NODE_VERSION}"
TRANSPORTS = {
    "vless_tcp_reality": "VLESS TCP Reality",
    "vless_xhttp_reality": "VLESS XHTTP Reality",
    "vless_encryption": "VLESS Encryption",
    "vless_tcp_selfsteal": "VLESS Selfsteal",
    "cdn_vk": "CDN VK",
    "cdn_yandex": "CDN Яндекс",
    "hysteria2": "Hysteria2",
}
CERTIFICATE_TRANSPORTS = frozenset({"vless_tcp_selfsteal", "cdn_vk", "cdn_yandex", "hysteria2"})
ROLES = {
    "normal": "Обычная",
    "entry": "Вход каскада VLESS",
    "exit": "Выход каскада VLESS",
    "haproxy_entry": "Вход HAProxy",
    "haproxy_exit": "Выход HAProxy",
}
ENTRY_ROLES = frozenset({"entry", "haproxy_entry"})
EXIT_ROLES = frozenset({"exit", "haproxy_exit"})
HAPROXY_ROLES = frozenset({"haproxy_entry", "haproxy_exit"})
TCP_REALITY_TRANSPORTS = frozenset({"vless_tcp_reality", "vless_tcp_selfsteal"})
ADDONS = {
    "bbr": "BBR",
    "fail2ban": "fail2ban",
    "warp": "WARP",
    "psiphon": "Psiphon",
    "antitorrent": "Анти-торрент",
    "ufw": "UFW",
}
FINAL_STATUSES = frozenset({"succeeded", "warning", "canceled"})
DELETABLE_STATUSES = frozenset({"draft", "failed", "paused", "canceled", "interrupted", "succeeded", "warning"})


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
    transport: str = "vless_tcp_selfsteal"
    role: str = "normal"
    client_port: int = 443
    node_port: int = 2222
    reality_domain: str = ""
    domain: str = ""
    cdn_domain: str = ""
    email: str = ""
    site_template: str = "standard"
    profile_name: str = ""
    host_remark: str = ""
    squad_uuid: str | None = None
    exit_install_id: str | None = None
    cascade_routes: list[dict[str, Any]] = field(default_factory=list)
    cascade_address: str = ""
    exit_ip: str = ""
    exit_interface: str = ""
    panel_sources: list[str] = field(default_factory=list)
    trusted_ssh_sources: list[str] = field(default_factory=list)
    haproxy_sources: list[str] = field(default_factory=list)
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
        if spec.role == "haproxy_exit" and spec.transport == "vless_tcp_selfsteal" and "client_port" not in value:
            spec.client_port = 9443
        for item in fields(cls):
            current = getattr(spec, item.name)
            if isinstance(item.default, str) and not isinstance(current, str):
                raise ValueError("Текстовый параметр имеет неверный тип: " + item.name)
        for name in ("replace_existing", "publish_host"):
            if type(getattr(spec, name)) is not bool:
                raise ValueError("Логический параметр имеет неверный тип: " + name)
        if spec.transport not in TRANSPORTS or spec.role not in ROLES:
            raise ValueError("Неизвестный транспорт или роль ноды")
        if spec.transport == "hysteria2" and spec.role != "normal":
            raise ValueError("Hysteria2 доступна только для обычной ноды")
        if spec.cascade_address:
            if spec.role not in ENTRY_ROLES | EXIT_ROLES:
                raise ValueError("IP связи между нодами доступен только для каскада")
            try:
                address = ipaddress.ip_address(spec.cascade_address.strip())
            except ValueError as error:
                raise ValueError("Укажите IPv4 или IPv6 связи между нодами без порта и подсети") from error
            if (
                address.is_unspecified
                or address.is_loopback
                or address.is_multicast
                or address.is_link_local
                or getattr(address, "scope_id", None)
                or getattr(address, "ipv4_mapped", None)
            ):
                raise ValueError("Для связи между нодами нужен доступный unicast IP сервера")
            spec.cascade_address = str(address)
        if spec.site_template not in {"standard", "streaming"}:
            raise ValueError("Неизвестный шаблон сайта")
        if spec.role in EXIT_ROLES:
            spec.publish_host = False
        if spec.role in HAPROXY_ROLES and spec.transport not in TCP_REALITY_TRANSPORTS:
            raise ValueError("Для каскада HAProxy выберите Selfsteal или VLESS TCP Reality")
        for name in ("ssh_port", "client_port", "node_port"):
            port = getattr(spec, name)
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError("Порты должны быть целыми числами от 1 до 65535")
        if (
            spec.role == "haproxy_exit"
            and spec.transport == "vless_tcp_selfsteal"
            and spec.client_port in {80, 443, 8443}
        ):
            raise ValueError(
                "Для выхода HAProxy Selfsteal используйте порт 9443 или другой свободный: 80, 443 и 8443 нужны сайту"
            )
        if not isinstance(spec.cascade_routes, list):
            raise ValueError("Направления каскада передаются списком")
        if spec.cascade_routes and spec.role not in ENTRY_ROLES:
            raise ValueError("Дополнительные направления доступны только для входа каскада")
        if spec.cascade_routes and spec.transport.startswith("cdn_"):
            raise ValueError("Несколько выходов каскада недоступны для транспорта CDN")
        exit_ids = {spec.exit_install_id} if spec.exit_install_id else set()
        client_ports = {spec.client_port}
        for route in spec.cascade_routes:
            if not isinstance(route, dict) or set(route) != {"exit_install_id", "client_port", "host_remark"}:
                raise ValueError("Укажите выход, клиентский порт и название каждого направления каскада")
            exit_id = route["exit_install_id"]
            if not isinstance(exit_id, str) or not re.fullmatch(r"[a-f0-9]{32}", exit_id):
                raise ValueError("Некорректный ID выходной ноды направления каскада")
            if exit_id in exit_ids:
                raise ValueError("Выходные ноды направлений каскада должны различаться")
            exit_ids.add(exit_id)
            port = route["client_port"]
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError("Порт направления каскада должен быть числом от 1 до 65535")
            management_ports = {spec.ssh_port, *({spec.node_port} if spec.role != "haproxy_entry" else set())}
            if spec.transport == "vless_tcp_selfsteal" and spec.role != "haproxy_entry":
                management_ports.add(8443)
            if port in client_ports or port in management_ports or needs_certificate(spec) and port == 80:
                raise ValueError("Клиентские порты направлений, SSH и управления должны различаться")
            client_ports.add(port)
            if (
                not isinstance(route["host_remark"], str)
                or not route["host_remark"].strip()
                or len(route["host_remark"].strip()) > 100
            ):
                raise ValueError("Название хоста направления: 1–100 символов")
        spec.cascade_routes = [{**route, "host_remark": route["host_remark"].strip()} for route in spec.cascade_routes]
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
        if spec.shaping and spec.transport == "hysteria2":
            raise ValueError("Шейпинг Hysteria2 не поддерживается: текущий шейпер работает только с TCP")
        if spec.shaping and (spec.transport.startswith("cdn_") or spec.role in EXIT_ROLES):
            raise ValueError("Шейпинг доступен только для прямых транспортов и входа каскада")
        if spec.role == "haproxy_entry" and (
            set(spec.addons) - {"bbr", "fail2ban", "ufw"} or spec.shaping or spec.exit_ip or spec.exit_interface
        ):
            raise ValueError(
                "Вход HAProxy поддерживает BBR, fail2ban и UFW; маршрутизация и шейпинг настраиваются на выходе"
            )
        for name in ("panel_sources", "trusted_ssh_sources", "haproxy_sources"):
            values = getattr(spec, name)
            if not isinstance(values, list):
                raise ValueError("Адреса доступа передаются списком IP или CIDR")
            try:
                setattr(spec, name, [str(ipaddress.ip_network(item, strict=False)) for item in values])
            except (ValueError, TypeError) as error:
                raise ValueError("Некорректный IP или CIDR доступа") from error
        if any(ipaddress.ip_network(item).num_addresses != 1 for item in spec.haproxy_sources):
            raise ValueError("Для HAProxy укажите отдельные IP входных серверов, без подсетей")
        spec.haproxy_sources = list(dict.fromkeys(spec.haproxy_sources))
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
        if (
            not spec.name.strip()
            or any(unicodedata.category(character).startswith("C") for character in spec.name)
            or not 3 <= node_name_length(spec.name) <= 30
        ):
            raise ValueError("Имя ноды: 3–30 символов без управляющих символов")
        try:
            spec.address = str(ipaddress.ip_address(spec.address.strip()))
        except (ValueError, AttributeError) as error:
            raise ValueError("Укажите IP-адрес сервера") from error
        if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", spec.ssh_username):
            raise ValueError("Некорректный SSH-логин")
        profile_basis = re.sub(r"[^A-Za-z0-9 _-]", "", spec.name).strip(" _-") or _default_node_name(spec.address)
        spec.profile_name = spec.profile_name or profile_basis[:21].rstrip(" _-") + "-profile"
        if not re.fullmatch(r"[A-Za-z0-9 _-]{2,29}", spec.profile_name):
            raise ValueError("Имя профиля: 2–29 символов, латиница, цифры, пробел, дефис и подчёркивание")
        spec.host_remark = spec.host_remark.strip() or spec.name
        if len(spec.host_remark) > 100:
            raise ValueError("Название хоста не должно превышать 100 символов")
        if spec.role != "haproxy_entry" and spec.transport in {"vless_tcp_reality", "vless_xhttp_reality"}:
            spec.reality_domain = _domain(spec.reality_domain)
        if needs_certificate(spec):
            spec.domain = _domain(spec.domain)
            if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", spec.email) or len(spec.email) > 254:
                raise ValueError("Для сертификата укажите email")
        if spec.transport.startswith("cdn_"):
            spec.cdn_domain = _domain(spec.cdn_domain)
            if spec.domain == spec.cdn_domain:
                raise ValueError("Origin и CDN должны иметь разные домены")
        if spec.role in ENTRY_ROLES and not spec.exit_install_id:
            raise ValueError("Выберите установленную выходную ноду каскада")
        if spec.role == "haproxy_exit" and (not spec.haproxy_sources or not spec.squad_uuid):
            raise ValueError("Для выхода HAProxy укажите IP входных серверов и выберите клиентский сквад")
        if spec.role == "haproxy_entry" and not spec.squad_uuid:
            raise ValueError("Для входа HAProxy выберите выходную ноду с клиентским сквадом")
        if spec.exit_install_id is not None and (
            not isinstance(spec.exit_install_id, str) or not re.fullmatch(r"[a-f0-9]{32}", spec.exit_install_id)
        ):
            raise ValueError("Некорректный ID выходной ноды")
        if spec.squad_uuid:
            try:
                UUID(spec.squad_uuid)
            except (ValueError, TypeError, AttributeError) as error:
                raise ValueError("Некорректный UUID сквада") from error
        if spec.role in EXIT_ROLES and spec.transport not in TCP_REALITY_TRANSPORTS:
            raise ValueError("Для выхода каскада выберите Selfsteal или VLESS TCP Reality")
        if spec.exit_ip:
            try:
                spec.exit_ip = str(ipaddress.ip_address(spec.exit_ip))
            except ValueError as error:
                raise ValueError("Некорректный выходной IP") from error
            if spec.exit_interface and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,32}", spec.exit_interface):
                raise ValueError("Некорректное имя сетевого интерфейса")
        management_ports = {spec.node_port} if spec.role != "haproxy_entry" else set()
        if spec.ssh_port in management_ports or (
            client_network(spec) == "tcp" and spec.client_port in {spec.ssh_port, *management_ports}
        ):
            raise ValueError("Порты SSH, клиентов и управления нодой должны различаться")
        tcp_ports = {spec.ssh_port, *management_ports}
        if client_network(spec) == "tcp":
            tcp_ports.add(spec.client_port)
        if needs_certificate(spec) and 80 in tcp_ports:
            raise ValueError("Порт 80 необходим для выпуска сертификата")
        return spec

    def to_dict(self) -> dict:
        """Возвращает параметры для сохранения задачи."""
        return asdict(self)

    def transit_address(self) -> str:
        """Возвращает собственный IP связи между нодами."""
        return self.cascade_address or self.address

    def reality_server_name(self) -> str:
        """Возвращает домен TLS целевого сайта REALITY."""
        return self.domain if self.transport == "vless_tcp_selfsteal" else self.reality_domain

    def cascade_specs(self) -> list["InstallSpec"]:
        """Возвращает параметры отдельных направлений входной ноды."""
        primary = copy.deepcopy(self)
        primary.cascade_routes = []
        result = [primary]
        for route in self.cascade_routes:
            current = copy.deepcopy(primary)
            for name, value in route.items():
                setattr(current, name, value)
            result.append(current)
        return result

    def client_ports(self) -> list[int]:
        """Возвращает клиентские порты всех направлений ноды."""
        return [spec.client_port for spec in self.cascade_specs()]

    def fingerprint(self) -> str:
        """Привязывает снимок установки к неизменным параметрам."""
        values = self.to_dict()
        if not values["haproxy_sources"]:
            values.pop("haproxy_sources")
        if not values["cascade_routes"]:
            values.pop("cascade_routes")
        if not values["cascade_address"]:
            values.pop("cascade_address")
        payload = json.dumps(values, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


def client_network(spec: InstallSpec) -> str:
    """Определяет протокол клиентского порта."""
    return "udp" if spec.transport == "hysteria2" else "tcp"


def needs_certificate(spec: InstallSpec) -> bool:
    """Определяет необходимость публичного TLS-сертификата."""
    return spec.role != "haproxy_entry" and spec.transport in CERTIFICATE_TRANSPORTS


def certificate_paths(spec: InstallSpec) -> tuple[str, str]:
    """Возвращает пути сертификата внутри ноды."""
    root = "/etc/letsencrypt/live/" + spec.domain
    return root + "/fullchain.pem", root + "/privkey.pem"

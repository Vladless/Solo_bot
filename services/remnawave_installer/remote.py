import asyncio
import copy
import errno
import hashlib
import hmac
import importlib
import ipaddress
import json
import logging
import re
import shlex
import sys

from pathlib import PurePosixPath

from .types import NODE_IMAGE, InstallError, InstallSpec


ROOT = "/opt/solobot-remnawave"
APT = "DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 --no-remove"
_LOCK = "/run/lock/solobot-remnawave-install.lock"
_LOGGER = logging.getLogger(__name__)


def _load_ssh():
    """Проверяет доступность SSH-библиотеки в окружении бота."""
    try:
        return importlib.import_module("asyncssh")
    except ImportError:
        raise InstallError(
            "ssh_dependencies",
            "В окружении бота недоступна библиотека asyncssh. Обновите зависимости из requirements.txt и перезапустите бота; при запуске в Docker пересоберите образ",
            retryable=False,
        ) from None


def _ssh_failure(ssh, error: Exception, context: dict) -> InstallError:
    """Различает ошибки SSH и записывает только безопасную диагностику."""
    error_class = type(error).__name__
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", error_class):
        error_class = "Exception"
    phase = context.get("phase", "initial")
    if phase not in {"initial", "fallback", "connect"}:
        phase = "initial"
    count = context.get("lookup_count", 0)
    count = count if type(count) is int and 0 <= count <= 1000 else 0
    os_code = getattr(error, "errno", None) if isinstance(error, OSError) else None
    ssh_code = getattr(error, "code", None) if isinstance(error, getattr(ssh, "DisconnectError", ())) else None
    os_code = os_code if type(os_code) is int else None
    ssh_code = ssh_code if type(ssh_code) is int else None
    version = getattr(ssh, "__version__", "")
    if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,47}", version):
        version = "unknown"
    frames = []
    trace = error.__traceback__
    while trace is not None:
        filename = PurePosixPath(trace.tb_frame.f_code.co_filename.replace("\\", "/")).name
        function = trace.tb_frame.f_code.co_name
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", filename):
            filename = "unknown"
        if not re.fullmatch(r"[A-Za-z0-9_.]{1,96}", function):
            function = "unknown"
        frames.append(f"{filename}:{function}:{trace.tb_lineno}")
        trace = trace.tb_next
    metadata = {
        "ssh_phase": phase,
        "ssh_lookup_count": count,
        "ssh_error_class": error_class,
        "ssh_errno": os_code,
        "ssh_disconnect_code": ssh_code,
        "ssh_python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "ssh_asyncssh_version": version,
        "ssh_frames": frames[-6:],
    }
    _LOGGER.warning(
        "Ошибка SSH: class=%s phase=%s lookups=%s errno=%s code=%s python=%s asyncssh=%s frames=%s",
        error_class,
        phase,
        count,
        os_code,
        ssh_code,
        metadata["ssh_python_version"],
        version,
        ";".join(metadata["ssh_frames"]),
        extra=metadata,
    )
    step, retryable = "ssh", True
    if isinstance(error, InstallError):
        step, message, retryable = error.step, error.message, error.retryable
    elif isinstance(error, TimeoutError):
        message = (
            "Истёк срок SSH-подключения. Проверьте доступность сервера"
            if phase == "connect"
            else "Истёк срок проверки SSH-ключа. Проверьте IP, SSH-порт и доступность сервера"
        )
    elif isinstance(error, ConnectionRefusedError) or os_code == errno.ECONNREFUSED:
        message = "Сервер отказал в подключении к SSH-порту. Проверьте порт и работу SSH"
    elif isinstance(error, ConnectionResetError | BrokenPipeError) or os_code in {
        errno.ECONNRESET,
        errno.ECONNABORTED,
        errno.EPIPE,
    }:
        message = "Сервер прервал SSH-соединение. Проверьте ограничения подключений и повторите позже"
    elif isinstance(error, PermissionError) or os_code in {errno.EACCES, errno.EPERM}:
        message = "Окружение бота запрещает подключение к SSH. Проверьте сетевые ограничения процесса"
        retryable = False
    elif os_code in {errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN, errno.EHOSTDOWN}:
        message = "Нет сетевого маршрута к SSH-серверу. Проверьте доступность IP из окружения бота"
    elif isinstance(error, OSError):
        message = "Сетевая ошибка при подключении к SSH. Проверьте IP, порт и сетевые ограничения"
    elif isinstance(error, getattr(ssh, "HostKeyNotVerifiable", ())):
        step, retryable = "ssh_host_key", False
        message = "Не удалось подтвердить доверенный SSH-ключ при подключении. Проверьте ключ сервера"
    elif isinstance(error, getattr(ssh, "KeyExchangeFailed", ())):
        retryable = False
        if phase in {"fallback", "connect"}:
            step = "ssh_host_key"
            message = "Сохранённый ключ SSH сервера не найден. Проверьте сервер и подтвердите новый ключ"
        else:
            message = "Не удалось согласовать алгоритмы SSH. Проверьте настройки SSH-сервера"
    elif isinstance(error, getattr(ssh, "ProtocolError", ())):
        message = "Сервер вернул ошибку протокола SSH. Проверьте SSH-порт и настройки сервера"
        retryable = False
    elif isinstance(error, getattr(ssh, "ConnectionLost", ())):
        message = "Сервер закрыл SSH-соединение. Проверьте ограничения подключений и повторите позже"
    elif isinstance(error, getattr(ssh, "PermissionDenied", ())):
        message = "Не удалось войти по SSH. Проверьте логин, пароль или приватный ключ"
    elif isinstance(error, getattr(ssh, "KeyImportError", ())):
        message = "Не удалось прочитать приватный SSH-ключ. Проверьте формат ключа и пароль ключа"
        retryable = False
    elif isinstance(error, getattr(ssh, "DisconnectError", ())):
        message = "Сервер отклонил SSH-соединение. Проверьте настройки SSH и повторите позже"
    else:
        message = f"Внутренняя ошибка проверки SSH ({error_class}). Подробности — в журнале бота"
        retryable = False
    if phase == "fallback":
        message = "Основное SSH-подключение ответило, но " + message[0].lower() + message[1:]
    return InstallError(step, message, retryable=retryable)


async def _server_host_key(ssh, address: str, port: int, expected: str = "", *, context: dict | None = None):
    """Находит доверенный ключ среди доступных алгоритмов сервера."""
    context = context if context is not None else {}
    context.update(phase="initial", lookup_count=0)

    async def lookup():
        context["lookup_count"] += 1
        key = await asyncio.wait_for(ssh.get_server_host_key(address, port=port, config=None), 15)
        if key is None:
            raise InstallError("ssh", "Сервер не предоставил ключ SSH", retryable=False)
        if not expected or hmac.compare_digest(key.get_fingerprint("sha256"), expected):
            return key
        context["phase"] = "fallback"
        remaining = [
            algorithm
            for algorithm in ssh.public_key.get_default_public_key_algs()
            if algorithm not in key.sig_algorithms
        ]
        while remaining:
            context["lookup_count"] += 1
            candidate = await asyncio.wait_for(
                ssh.get_server_host_key(
                    address,
                    port=port,
                    config=None,
                    server_host_key_algs=[algorithm.decode("ascii") for algorithm in remaining],
                ),
                15,
            )
            if candidate is None:
                raise InstallError("ssh", "Сервер не предоставил ключ SSH", retryable=False)
            if hmac.compare_digest(candidate.get_fingerprint("sha256"), expected):
                return candidate
            following = [algorithm for algorithm in remaining if algorithm not in candidate.sig_algorithms]
            if following == remaining:
                raise RuntimeError("SSH algorithm selection did not advance")
            remaining = following
        raise InstallError(
            "ssh_host_key",
            "Сохранённый ключ SSH сервера не найден. Проверьте сервер и подтвердите новый ключ",
            retryable=False,
        )

    try:
        return await asyncio.wait_for(lookup(), 30)
    except Exception as error:
        raise _ssh_failure(ssh, error, context) from None


def owner_id(spec: InstallSpec) -> str:
    """Возвращает постоянную метку владельца установки."""
    saved = getattr(spec, "_owner_id", None)
    if isinstance(saved, str) and re.fullmatch(r"[a-f0-9]{64}", saved):
        return saved
    return hashlib.sha256(f"{spec.panel_ref}\n{spec.address}\n{spec.name}".encode()).hexdigest()


class RemoteServer:
    def __init__(self, spec: InstallSpec, secrets: dict) -> None:
        self.spec = spec
        self.secrets = secrets
        self.conn = None
        self.sudo = False

    @staticmethod
    async def fingerprint(address: str, port: int, expected: str = "") -> str:
        """Получает отпечаток SSH до аутентификации."""
        key = await _server_host_key(_load_ssh(), address, port, expected)
        return key.get_fingerprint("sha256")

    async def connect(self):
        """Подключается только к подтверждённому ключу сервера."""
        if self.conn is not None:
            return self
        ssh = _load_ssh()
        expected = self.spec.host_fingerprint
        if not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}=?", expected or ""):
            raise InstallError(
                "ssh", "Не удалось подготовить проверку SSH-сервера; повторите проверку подключения", retryable=False
            )
        context = {"phase": "initial", "lookup_count": 0}
        try:
            key = await _server_host_key(ssh, self.spec.address, self.spec.ssh_port, expected, context=context)
            context["phase"] = "connect"
            private_key = self.secrets.get("ssh_private_key")
            keys = [ssh.import_private_key(private_key, self.secrets.get("ssh_key_passphrase"))] if private_key else []
            self.conn = await ssh.connect(
                self.spec.address,
                port=self.spec.ssh_port,
                username=self.spec.ssh_username,
                password=self.secrets.get("ssh_password"),
                client_keys=keys,
                known_hosts=([key], [], []),
                config=None,
                agent_path=None,
                connect_timeout=15,
                login_timeout=20,
                keepalive_interval=20,
                keepalive_count_max=3,
            )
            uid = await self.conn.run("id -u", check=False, timeout=15)
            if uid.exit_status != 0:
                raise InstallError("ssh", "Не удалось проверить права SSH")
            if uid.stdout.strip() != "0":
                sudo = await self.conn.run("sudo -n true", check=False, timeout=15)
                if sudo.exit_status != 0:
                    raise InstallError("ssh", "Нужны root или sudo без пароля", retryable=False)
                self.sudo = True
            return self
        except InstallError as error:
            await self.close()
            if context["phase"] == "connect":
                raise _ssh_failure(ssh, error, context) from None
            raise
        except Exception as error:
            await self.close()
            raise _ssh_failure(ssh, error, context) from None

    async def close(self) -> None:
        """Закрывает SSH-соединение."""
        connection, self.conn = self.conn, None
        if connection is not None:
            connection.close()
            await connection.wait_closed()

    async def __aenter__(self) -> "RemoteServer":
        return await self.connect()

    async def __aexit__(self, *_args: object) -> None:
        await self.close()

    async def run(self, command: str, *, step: str, timeout: int = 300, secret: bool = False) -> str:
        """Выполняет команду под блокировкой без публикации вывода ошибок."""
        if self.conn is None:
            raise InstallError(step, "SSH-соединение не установлено")
        prefix = "sudo -n " if self.sudo else ""
        wrapped = (
            f"{prefix}flock -w 120 -E 75 {shlex.quote(_LOCK)} "
            f"timeout --signal=TERM --kill-after=15s {int(timeout)}s bash -c {shlex.quote(command)}"
        )
        try:
            result = await self.conn.run(wrapped, check=False, timeout=timeout + 150)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise InstallError(step, "Соединение прервано или истёк срок команды") from None
        if result.exit_status != 0:
            message = (
                "Сервер занят другой установкой. Повторите позже"
                if result.exit_status == 75
                else f"Команда этапа завершилась с кодом {result.exit_status}. Проверьте серверный журнал"
            )
            raise InstallError(step, message)
        output = result.stdout or ""
        return output if secret else output[-4_000_000:]

    async def write(self, path: str, content: str, mode: int = 0o600) -> None:
        """Атомарно записывает файл через stdin под серверной блокировкой."""
        normalized = str(PurePosixPath(path))
        if normalized != path or ".." in PurePosixPath(path).parts or not path.startswith("/"):
            raise InstallError("files", "Некорректный путь файла", retryable=False)
        prefixes = (
            ROOT + "/",
            "/etc/systemd/system/solobot-",
            "/etc/sysctl.d/90-solobot-",
            "/etc/fail2ban/jail.d/solobot-",
            "/etc/apt/sources.list.d/solobot-",
            "/etc/apt/keyrings/solobot-",
            "/etc/nginx/conf.d/solobot-",
        )
        if not any(path.startswith(prefix) for prefix in prefixes):
            raise InstallError("files", "Запись вне каталогов установщика запрещена", retryable=False)
        script = """import hashlib,json,os,sys,tempfile
from pathlib import Path
p=Path(sys.argv[1]); root=Path(sys.argv[3]); replace=sys.argv[4]=='yes'
if any(x.is_symlink() for x in [p,*p.parents]): raise SystemExit(41)
manifest=root/'.managed-files.json'
managed=json.loads(manifest.read_text()) if manifest.is_file() else {}
if p.exists() and not str(p).startswith(str(root)+'/'):
 digest=hashlib.sha256(p.read_bytes()).hexdigest()
 if managed.get(str(p))!=digest and not replace: raise SystemExit(42)
p.parent.mkdir(parents=True,exist_ok=True)
data=sys.stdin.buffer.read()
def store(path,data,mode):
 fd,tmp=tempfile.mkstemp(prefix='.solo-',dir=str(path.parent))
 os.fchmod(fd,mode)
 with os.fdopen(fd,'wb') as file:
  file.write(data); file.flush(); os.fsync(file.fileno())
 os.replace(tmp,path)
store(p,data,int(sys.argv[2]))
managed[str(p)]=hashlib.sha256(data).hexdigest()
store(manifest,json.dumps(managed).encode(),0o600)
"""
        prefix = "sudo -n " if self.sudo else ""
        command = (
            f"{prefix}flock -w 120 -E 75 {shlex.quote(_LOCK)} python3 -c {shlex.quote(script)} "
            f"{shlex.quote(path)} {int(mode)} {ROOT} {'yes' if self.spec.replace_existing else 'no'}"
        )
        try:
            result = await self.conn.run(command, input=content, check=False, timeout=180)
            if result.exit_status != 0:
                raise InstallError("files", "Не удалось безопасно записать файл установщика")
        except InstallError:
            raise
        except Exception:
            raise InstallError("files", "Запись файла прервана") from None


async def preflight(remote: RemoteServer, spec: InstallSpec) -> dict:
    """Проверяет Ubuntu, права и конфликты без установки пакетов."""
    from .addons import validate_addons

    validate_addons(spec, remote.secrets)
    service_ports = transport_ports(spec)
    if spec.transport == "vless_tcp_selfsteal" or spec.transport.startswith("cdn_"):
        service_ports.append(4443 if spec.transport == "cdn_yandex" else 8443)
    if len(service_ports) != len(set(service_ports)) or {spec.ssh_port, spec.node_port}.intersection(service_ports):
        raise InstallError("preflight", "Порты SSH, управления, TLS и Xray должны различаться", retryable=False)
    release = await remote.run("cat /etc/os-release", step="preflight")
    values = {}
    for line in release.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value.strip('"')
    if values.get("ID") != "ubuntu" or values.get("VERSION_ID") not in {"22.04", "24.04", "26.04"}:
        raise InstallError("preflight", "Поддерживается Ubuntu 22.04, 24.04 или 26.04", retryable=False)
    architecture = (await remote.run("uname -m", step="preflight")).strip()
    if architecture not in {"x86_64", "aarch64"}:
        raise InstallError("preflight", "Поддерживаются серверы amd64 и arm64", retryable=False)
    await remote.run("test -d /run/systemd/system && command -v python3 && command -v flock", step="preflight")
    marker = (await remote.run(f"cat {ROOT}/.owner 2>/dev/null || true", step="preflight")).strip()
    conflicts = []
    if marker and marker != owner_id(spec):
        conflicts.append("каталог другой установки")
    for container in ("remnanode", "cdn-nginx"):
        existing = await remote.run(
            "if command -v docker >/dev/null; then docker inspect --format "
            + shlex.quote('{{index .Config.Labels "io.solobot.remnawave.owner"}}')
            + f" {container} 2>/dev/null || true; fi",
            step="preflight",
        )
        if existing.strip() and existing.strip() != owner_id(spec):
            conflicts.append("контейнер " + container)
    listeners = await remote.run("ss -tlnpH", step="preflight")
    reserved = transport_ports(spec) + [spec.node_port]
    if spec.transport.startswith("cdn_") or spec.transport == "vless_tcp_selfsteal":
        reserved += [8443 if spec.transport != "cdn_yandex" else 4443]
    if not marker:
        for port in set(reserved):
            if re.search(rf":{port}\s", listeners):
                conflicts.append(f"занятый TCP-порт {port}")
    if conflicts and not spec.replace_existing:
        raise InstallError("preflight", "Требуется явное разрешение замены: " + ", ".join(conflicts), retryable=False)
    return {"os": values.get("VERSION_ID"), "architecture": architecture, "conflicts": conflicts}


def transport_ports(spec: InstallSpec) -> list[int]:
    """Возвращает внешние порты транспорта."""
    if spec.transport == "cdn_vk":
        return [80, 443]
    if spec.transport == "cdn_yandex":
        return [80, 7443]
    return [spec.client_port, 80] if spec.transport == "vless_tcp_selfsteal" else [spec.client_port]


async def claim_paths(remote: RemoteServer, spec: InstallSpec) -> None:
    """Закрепляет собственный каталог и сохраняет чужую установку."""
    script = """import os, pathlib, shutil, sys, time
p=pathlib.Path(sys.argv[1]); owner=sys.argv[2]; replace=sys.argv[3]=='yes'
if p.is_symlink(): raise SystemExit(31)
marker=p/'.owner'
if p.exists() and (not marker.is_file() or marker.read_text().strip()!=owner):
 if not replace: raise SystemExit(32)
 backup=pathlib.Path('/root/solobot-remnawave-backups')
 backup.mkdir(mode=0o700,parents=True,exist_ok=True)
 shutil.copytree(p,backup/(str(time.time_ns())+'-files'),symlinks=True)
p.mkdir(mode=0o700,parents=True,exist_ok=True)
os.chmod(p,0o700)
marker.write_text(owner); os.chmod(marker,0o600)
"""
    await remote.run(
        f"python3 -c {shlex.quote(script)} {ROOT} {owner_id(spec)} {'yes' if spec.replace_existing else 'no'}",
        step="files",
    )


async def ensure_docker(remote: RemoteServer) -> None:
    """Устанавливает Docker из официального репозитория Ubuntu Docker."""
    check = await remote.run(
        "command -v docker >/dev/null && echo DOCKER; docker compose version >/dev/null 2>&1 && echo COMPOSE; true",
        step="docker",
    )
    if "DOCKER" not in check:
        await remote.run(f"{APT} update && {APT} install -y ca-certificates curl gnupg", step="docker", timeout=900)
        key = await remote.run("curl -fsSL https://download.docker.com/linux/ubuntu/gpg", step="docker")
        if "BEGIN PGP PUBLIC KEY BLOCK" not in key:
            raise InstallError("docker", "Не получен ключ официального репозитория Docker")
        architecture = (await remote.run("dpkg --print-architecture", step="docker")).strip()
        distribution = (await remote.run(". /etc/os-release; printf '%s' \"$VERSION_CODENAME\"", step="docker")).strip()
        if architecture not in {"amd64", "arm64"} or distribution not in {"jammy", "noble", "resolute"}:
            raise InstallError("docker", "Не поддерживается репозиторий Docker для этой ОС", retryable=False)
        await remote.write("/etc/apt/keyrings/solobot-docker.asc", key, mode=0o644)
        await remote.write(
            "/etc/apt/sources.list.d/solobot-docker.list",
            (
                f"deb [arch={architecture} signed-by=/etc/apt/keyrings/solobot-docker.asc] "
                f"https://download.docker.com/linux/ubuntu {distribution} stable\n"
            ),
            mode=0o644,
        )
        await remote.run(
            f"{APT} update && {APT} install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin",
            step="docker",
            timeout=900,
        )
    elif "COMPOSE" not in check:
        await remote.run(
            f"{APT} update && if apt-cache show docker-compose-v2 >/dev/null 2>&1; "
            f"then {APT} install -y docker-compose-v2; else {APT} install -y docker-compose-plugin; fi",
            step="docker",
            timeout=900,
        )
    await remote.run("systemctl enable --now docker && docker info >/dev/null && docker compose version", step="docker")


async def _replace_container(remote: RemoteServer, spec: InstallSpec, name: str) -> None:
    """Сохраняет контейнер при явно разрешённом захвате имени."""
    script = """import json,os,pathlib,shutil,subprocess,sys,time
name,owner,replace=sys.argv[1:]
r=subprocess.run(['docker','inspect',name],capture_output=True)
if r.returncode: raise SystemExit(0)
data=json.loads(r.stdout)[0]
if (data.get('Config',{}).get('Labels') or {}).get('io.solobot.remnawave.owner')==owner: raise SystemExit(0)
if replace!='yes': raise SystemExit(33)
p=pathlib.Path('/root/solobot-remnawave-backups')/(str(time.time_ns())+'-'+name)
p.mkdir(mode=0o700,parents=True)
target=p/'inspect.json'; target.write_bytes(r.stdout); os.chmod(target,0o600)
workdir=(data.get('Config',{}).get('Labels') or {}).get('com.docker.compose.project.working_dir')
if workdir:
 for filename in ('docker-compose.yml','docker-compose.yaml','compose.yml','compose.yaml','compose.json'):
  original=pathlib.Path(workdir)/filename
  if original.is_file() and not original.is_symlink():
   shutil.copyfile(original,p/filename); os.chmod(p/filename,0o600)
subprocess.run(['docker','rm','-f',name],check=True,stdout=subprocess.DEVNULL)
"""
    await remote.run(
        f"python3 -c {shlex.quote(script)} {shlex.quote(name)} {owner_id(spec)} "
        + ("yes" if spec.replace_existing else "no"),
        step="docker",
        secret=True,
    )


async def _open_firewall(remote: RemoteServer, spec: InstallSpec) -> None:
    """Открывает клиентские порты и доступ панели в действующем UFW."""
    status = await remote.run("if command -v ufw >/dev/null; then LC_ALL=C ufw status; fi", step="firewall")
    if "Status: active" not in status:
        return
    for port in transport_ports(spec):
        await remote.run(f"ufw allow {port}/tcp comment solobot-remnawave", step="firewall")
    if not spec.panel_sources:
        await remote.run(f"ufw allow {spec.node_port}/tcp comment solobot-remnawave", step="firewall")
    for source in spec.panel_sources:
        await remote.run(
            f"ufw allow from {shlex.quote(source)} to any port {spec.node_port} proto tcp comment solobot-remnawave",
            step="firewall",
        )


async def _management_firewall(remote: RemoteServer, spec: InstallSpec, *, additional_ports=()) -> None:
    """Ограничивает порт ноды доверенными адресами независимо от UFW."""
    from .addons import _nft_service

    if not spec.panel_sources:
        await _disable_management_firewall(remote)
        return
    await remote.run(
        f"command -v nft >/dev/null || ( {APT} update && {APT} install -y nftables )", step="firewall", timeout=900
    )
    lines = [
        "table inet solobot_remnawave_mgmt {",
        "chain inbound { type filter hook input priority -20; policy accept;",
    ]
    for port in sorted({spec.node_port, *additional_ports}):
        for version, family in ((4, "ip"), (6, "ip6")):
            networks = [
                str(ipaddress.ip_network(value, strict=False))
                for value in spec.panel_sources
                if ipaddress.ip_network(value, strict=False).version == version
            ]
            if networks:
                lines.append(f"tcp dport {port} {family} saddr {{ " + ", ".join(networks) + " } accept")
        lines.append(f"tcp dport {port} drop")
    lines.extend(["}", "}", ""])
    await _nft_service(remote, "remnawave_mgmt", "\n".join(lines))


async def _disable_management_firewall(remote: RemoteServer) -> None:
    """Удаляет только подтверждённую собственную защиту порта ноды."""
    script = (
        "import hashlib,json,pathlib,subprocess,sys\nroot=pathlib.Path(sys.argv[1]); unit=pathlib.Path(sys.argv[2])\n"
        "manifest=root/'.managed-files.json'\n"
        "managed=json.loads(manifest.read_text()) if manifest.is_file() else {}\n"
        "def owned(path):\n"
        " return path.is_file() and not path.is_symlink() and managed.get(str(path))==hashlib.sha256(path.read_bytes()).hexdigest()\n"
        "apply=root/'addons'/'apply-remnawave_mgmt.py'\n"
        "if not owned(apply) and not owned(unit): raise SystemExit(0)\n"
        "if unit.exists() and not owned(unit): raise SystemExit(33)\n"
        "service='solobot-remnawave_mgmt-nft.service'\n"
        "if owned(unit):\n"
        " subprocess.run(['systemctl','disable','--now',service],check=False,capture_output=True)\n"
        " if subprocess.run(['systemctl','is-active','--quiet',service],capture_output=True).returncode==0: raise SystemExit(34)\n"
        "present=subprocess.run(['nft','list','table','inet','solobot_remnawave_mgmt'],capture_output=True).returncode==0\n"
        "if present: subprocess.run(['nft','delete','table','inet','solobot_remnawave_mgmt'],check=True)\n"
        "if owned(unit):\n"
        " unit.unlink()\n"
        " subprocess.run(['systemctl','daemon-reload'],check=True)\n"
        "for path in (apply,root/'addons'/'remnawave_mgmt.nft'):\n"
        " if owned(path): path.unlink()\n"
    )
    await remote.run(
        f"python3 -c {shlex.quote(script)} {ROOT} /etc/systemd/system/solobot-remnawave_mgmt-nft.service",
        step="firewall",
    )


async def prepare_encryption(remote: RemoteServer, spec: InstallSpec, artifacts: dict, checkpoint=None) -> None:
    """Получает ключ Encryption из закреплённого образа ноды."""
    if spec.transport != "vless_encryption" or artifacts.get("decryption"):
        return
    await claim_paths(remote, spec)
    await ensure_docker(remote)
    await remote.run(f"docker pull {NODE_IMAGE}", step="keys", timeout=600)
    output = await remote.run(
        f"docker run --rm --entrypoint xray {NODE_IMAGE} vlessenc", step="keys", timeout=120, secret=True
    )
    section = re.search(r"Authentication:\s*ML-KEM-768,\s*Post-Quantum(?P<body>.*)", output, re.DOTALL)
    match = re.search(
        r'"decryption"\s*:\s*"(mlkem768x25519plus\.native\.[^"\s]+)"', section.group("body") if section else ""
    )
    if not match:
        raise InstallError("keys", "Xray не предоставил ожидаемый ключ ML-KEM-768")
    artifacts["decryption"] = match.group(1)
    if checkpoint:
        await checkpoint(artifacts)


async def prepare_exit_ip(remote: RemoteServer, spec: InstallSpec, artifacts: dict, checkpoint=None) -> None:
    """Проверяет выходной адрес и закрепляет явно выбранный интерфейс."""
    if not spec.exit_ip:
        return
    await ensure_http_tools(remote)
    address = ipaddress.ip_address(spec.exit_ip)
    raw = await remote.run("ip -j address show", step="exit_ip")
    try:
        interfaces = json.loads(raw)
        assigned = {
            str(ipaddress.ip_address(entry["local"]))
            for interface in interfaces
            for entry in interface.get("addr_info", [])
            if entry.get("local")
        }
    except (ValueError, TypeError, KeyError):
        raise InstallError("exit_ip", "Не удалось проверить адреса сетевых интерфейсов") from None
    if spec.exit_interface and str(address) in assigned:
        assigned_to_interface = any(
            interface.get("ifname") == spec.exit_interface
            and any(entry.get("local") == str(address) for entry in interface.get("addr_info", []))
            for interface in interfaces
        )
        if not assigned_to_interface:
            previous = (artifacts.get("exit_ip_change") or {}).get("previous") or {}
            if not previous.get("owned") or previous.get("address") != str(address):
                raise InstallError("exit_ip", "Выходной IP назначен другому интерфейсу", retryable=False)
            assigned.discard(str(address))
    if str(address) not in assigned:
        if not spec.exit_interface:
            raise InstallError("exit_ip", "Выходной IP не назначен. Укажите его сетевой интерфейс", retryable=False)
        await remote.run(f"ip link show dev {shlex.quote(spec.exit_interface)} >/dev/null", step="exit_ip")
        await claim_paths(remote, spec)
        artifacts["exit_alias"] = {"address": str(address), "interface": spec.exit_interface, "owned": True}
        if checkpoint:
            await checkpoint(artifacts)
        prefix = 32 if address.version == 4 else 128
        await remote.run(
            f"ip address add {shlex.quote(str(address) + '/' + str(prefix))} dev {shlex.quote(spec.exit_interface)}",
            step="exit_ip",
        )
    alias = artifacts.get("exit_alias") or {}
    if alias.get("owned"):
        managed_interface = spec.exit_interface or alias.get("interface")
        if alias.get("address") != str(address) or alias.get("interface") != managed_interface:
            raise InstallError("exit_ip", "Сохранённый выходной адрес не соответствует установке", retryable=False)
        prefix = 32 if address.version == 4 else 128
        interface = shlex.quote(managed_interface)
        cidr = shlex.quote(str(address) + "/" + str(prefix))
        check = f"ip -j address show dev {interface} | python3 -c " + shlex.quote(
            "import json,sys; address="
            + repr(str(address))
            + "; sys.exit(0 if any(x.get('local')==address for i in json.load(sys.stdin) "
            "for x in i.get('addr_info',[])) else 1)"
        )
        await remote.write(
            ROOT + "/exit-ip.sh", f"set -eu\n{check} || ip address add {cidr} dev {interface}\n", mode=0o700
        )
        await remote.write(
            "/etc/systemd/system/solobot-remnawave-exit-ip.service",
            (
                "[Unit]\nDescription=Node managed outbound address\nAfter=network-online.target\nWants=network-online.target\n"
                f"[Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart=/bin/bash {ROOT}/exit-ip.sh\n"
                f"ExecStop=/sbin/ip address del {str(address)}/{prefix} dev {managed_interface}\n"
                "[Install]\nWantedBy=multi-user.target\n"
            ),
            mode=0o644,
        )
        await remote.run("systemctl daemon-reload && systemctl enable --now solobot-remnawave-exit-ip", step="exit_ip")
    target = "1.1.1.1" if address.version == 4 else "2606:4700:4700::1111"
    family = "-4" if address.version == 4 else "-6"
    await remote.run(f"ip {family} route get {target} from {shlex.quote(str(address))} >/dev/null", step="exit_ip")
    actual = await remote.run(
        f"curl {family} --fail --silent --show-error --connect-timeout 10 --max-time 20 "
        f"--interface {shlex.quote(str(address))} https://api64.ipify.org",
        step="exit_ip",
        timeout=30,
    )
    try:
        if ipaddress.ip_address(actual.strip()) != address:
            raise ValueError
    except ValueError:
        raise InstallError("exit_ip", "Сервер не подтвердил доступ в интернет через выбранный выходной IP") from None
    artifacts["exit_ip_verified"] = str(address)
    if checkpoint:
        await checkpoint(artifacts)


async def deploy_node(
    remote: RemoteServer, spec: InstallSpec, artifacts: dict, log, checkpoint=None, *, runtime_only: bool = False
) -> None:
    """Развёртывает ноду либо обновляет только её образ."""
    if runtime_only:
        spec = copy.copy(spec)
        spec.replace_existing = False
        marker = (await remote.run(f"cat {ROOT}/.owner 2>/dev/null || true", step="docker")).strip()
        if marker != owner_id(spec):
            raise InstallError("docker", "Каталог ноды больше не принадлежит этой установке", retryable=False)
        await _check_container_owner(remote, spec, "remnanode")
    await claim_paths(remote, spec)
    await ensure_docker(remote)
    if not runtime_only:
        await _open_firewall(remote, spec)
        await _management_firewall(remote, spec)
    compose = _node_compose(spec, artifacts)
    await remote.write(ROOT + "/node/compose.json", json.dumps(compose))
    await _replace_container(remote, spec, "remnanode")
    await log("Скачивание и запуск закреплённого образа ноды")
    await remote.run(f"cd {ROOT}/node && docker compose -f compose.json pull", step="docker", timeout=600)
    await remote.run(f"cd {ROOT}/node && docker compose -f compose.json up -d", step="docker", timeout=180)
    if not runtime_only and (spec.transport == "vless_tcp_selfsteal" or spec.transport.startswith("cdn_")):
        await _deploy_tls(remote, spec, artifacts, log)
    artifacts["remote_deployed"] = True
    if checkpoint:
        await checkpoint(artifacts)


def _node_compose(spec: InstallSpec, artifacts: dict) -> dict:
    """Собирает настройки контейнера без изменения образа."""
    secret = artifacts.get("node_secret")
    if not isinstance(secret, str) or not secret or "\n" in secret:
        raise InstallError("docker", "Не сохранён ключ связи с панелью")
    return {
        "services": {
            "remnanode": {
                "container_name": "remnanode",
                "hostname": "remnanode",
                "image": NODE_IMAGE,
                "network_mode": "host",
                "restart": "always",
                "cap_add": ["NET_ADMIN"],
                "labels": {"io.solobot.remnawave.owner": owner_id(spec)},
                "environment": {"NODE_PORT": str(spec.node_port), "SECRET_KEY": secret},
            }
        },
    }


def _uses_tls(spec: InstallSpec) -> bool:
    """Определяет наличие собственного TLS-прокси."""
    return spec.transport == "vless_tcp_selfsteal" or spec.transport.startswith("cdn_")


async def prepare_remote_changes(
    remote: RemoteServer, old_spec: InstallSpec, new_spec: InstallSpec, artifacts: dict, changed, checkpoint=None
) -> None:
    """Готовит ключи и выходной адрес до изменения профиля."""
    from .profiles import _cdn_extra, generate_keys

    changed = set(changed)
    if old_spec.address != new_spec.address or old_spec.panel_ref != new_spec.panel_ref:
        raise InstallError("changes", "Перенос сервера или панели требует отдельной установки", retryable=False)
    artifacts.setdefault("remote_owner_id", owner_id(old_spec))
    old_spec._owner_id = artifacts["remote_owner_id"]
    new_spec._owner_id = artifacts["remote_owner_id"]
    remote.spec._owner_id = artifacts["remote_owner_id"]
    if changed.intersection({"transport", "client_port", "node_port"}):

        def reserved(spec):
            ports = set(transport_ports(spec)) | {spec.node_port}
            if _uses_tls(spec):
                ports.add(4443 if spec.transport == "cdn_yandex" else 8443)
            return ports

        new_ports = reserved(new_spec) - reserved(old_spec)
        listeners = await remote.run("ss -tlnpH", step="preflight")
        if any(re.search(rf":{port}\s", listeners) for port in new_ports):
            raise InstallError("preflight", "Новый порт уже занят другим процессом", retryable=False)
        if old_spec.transport != new_spec.transport and _uses_tls(old_spec):
            await _stop_tls(remote, old_spec)
    if "transport" in changed:
        if new_spec.transport.startswith("cdn_"):
            if not artifacts.get("xhttp_path"):
                artifacts.update(generate_keys(new_spec))
            artifacts["xhttp_extra"] = _cdn_extra(new_spec.transport, artifacts["xhttp_path"])
        elif new_spec.transport != "vless_encryption" and not all(
            artifacts.get(key) for key in ("private_key", "public_key", "short_id")
        ):
            artifacts.update(generate_keys(new_spec))
        if checkpoint:
            await checkpoint(artifacts)
        await prepare_encryption(remote, new_spec, artifacts, checkpoint)
    if changed.intersection({"exit_ip", "exit_interface"}):
        change_key = hashlib.sha256(
            f"{old_spec.exit_ip}\n{old_spec.exit_interface}\n{new_spec.exit_ip}\n{new_spec.exit_interface}".encode()
        ).hexdigest()
        state = artifacts.get("exit_ip_change") or {}
        if state.get("key") != change_key:
            previous = copy.deepcopy(artifacts.get("exit_alias"))
            artifacts["exit_ip_change"] = {"key": change_key, "previous": previous}
            if not (
                previous
                and previous.get("owned")
                and previous.get("address") == new_spec.exit_ip
                and new_spec.exit_interface in {"", previous.get("interface")}
            ):
                artifacts.pop("exit_alias", None)
            artifacts.pop("exit_ip_verified", None)
            if checkpoint:
                await checkpoint(artifacts)
        await prepare_exit_ip(remote, new_spec, artifacts, checkpoint)


async def _cleanup_exit_alias(remote: RemoteServer, spec: InstallSpec, artifacts: dict) -> None:
    """Удаляет только прежний адрес собственной установки."""
    state = artifacts.get("exit_ip_change") or {}
    previous = state.get("previous") or {}
    current = artifacts.get("exit_alias") or {}
    if not previous.get("owned") or previous == current or state.get("cleaned"):
        return
    address = ipaddress.ip_address(previous["address"])
    interface = previous["interface"]
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,32}", interface):
        raise InstallError("exit_ip", "Некорректный снимок прежнего интерфейса", retryable=False)
    if not current.get("owned"):
        await remote.run(
            "systemctl disable --now solobot-remnawave-exit-ip 2>/dev/null || true; "
            "rm -f /etc/systemd/system/solobot-remnawave-exit-ip.service; systemctl daemon-reload",
            step="exit_ip",
        )
    if str(address) != spec.exit_ip or interface != spec.exit_interface:
        script = (
            "import json,subprocess,sys\naddress,interface=sys.argv[1:]\n"
            "result=subprocess.run(['ip','-j','address','show','dev',interface],capture_output=True,text=True,check=True)\n"
            "if any(x.get('local')==address for i in json.loads(result.stdout) for x in i.get('addr_info',[])):\n"
            " prefix='32' if ':' not in address else '128'\n"
            " subprocess.run(['ip','address','del',address+'/'+prefix,'dev',interface],check=True)\n"
        )
        await remote.run(
            f"python3 -c {shlex.quote(script)} {shlex.quote(str(address))} {shlex.quote(interface)}", step="exit_ip"
        )
    state["cleaned"] = True


def _ufw_rules(spec: InstallSpec) -> set[tuple[str, ...]]:
    """Собирает правила, относящиеся к параметрам установки."""
    rules = {("allow", f"{port}/tcp") for port in transport_ports(spec)}
    if not spec.panel_sources:
        rules.add(("allow", f"{spec.node_port}/tcp"))
    for source in spec.panel_sources:
        rules.add(("allow", "from", source, "to", "any", "port", str(spec.node_port), "proto", "tcp"))
    if spec.trusted_ssh_sources:
        for source in spec.trusted_ssh_sources:
            rules.add(("allow", "from", source, "to", "any", "port", str(spec.ssh_port), "proto", "tcp"))
    else:
        rules.add(("allow", f"{spec.ssh_port}/tcp"))
    return rules


async def _change_firewall(remote: RemoteServer, old_spec: InstallSpec, new_spec: InstallSpec, artifacts: dict) -> None:
    """Обновляет собственные правила, сохраняя посторонние разрешения."""
    from .addons import _ufw_commands

    status = await remote.run("if command -v ufw >/dev/null; then LC_ALL=C ufw status; fi", step="firewall")
    if "Status: active" not in status:
        return
    old_rules, new_rules = _ufw_rules(old_spec), _ufw_rules(new_spec)
    for rule in sorted(new_rules - old_rules):
        await remote.run("ufw " + shlex.join(rule) + " comment solobot-remnawave", step="firewall")
    previous = {tuple(rule) for rule in artifacts.get("ufw_previous_added", [])}
    current = await remote.run("LC_ALL=C ufw show added", step="firewall")
    for tokens, owned in _ufw_commands(current):
        if owned and tuple(tokens) in old_rules - new_rules and tuple(tokens) not in previous:
            await remote.run("ufw --force delete " + shlex.join(tokens), step="firewall")


async def _stop_tls(remote: RemoteServer, spec: InstallSpec) -> None:
    """Останавливает только собственный TLS-прокси."""
    script = (
        "import json,subprocess,sys\n"
        "result=subprocess.run(['docker','inspect','cdn-nginx'],capture_output=True)\n"
        "if result.returncode: raise SystemExit(0)\n"
        "labels=json.loads(result.stdout)[0].get('Config',{}).get('Labels') or {}\n"
        "if labels.get('io.solobot.remnawave.owner')!=sys.argv[1]: raise SystemExit(33)\n"
        "subprocess.run(['docker','stop','cdn-nginx'],check=True,stdout=subprocess.DEVNULL)\nprint('STOPPED')\n"
    )
    result = await remote.run(f"python3 -c {shlex.quote(script)} {owner_id(spec)}", step="nginx")
    if "STOPPED" in result:
        await remote.run("systemctl disable --now solobot-remnawave-cert.timer", step="certificate")


async def _check_container_owner(remote: RemoteServer, spec: InstallSpec, name: str) -> None:
    """Отказывает в точечном изменении чужого контейнера."""
    script = (
        "import json,subprocess,sys\nname,owner=sys.argv[1:]\n"
        "result=subprocess.run(['docker','inspect',name],capture_output=True)\n"
        "if result.returncode: raise SystemExit(0)\n"
        "labels=json.loads(result.stdout)[0].get('Config',{}).get('Labels') or {}\n"
        "if labels.get('io.solobot.remnawave.owner')!=owner: raise SystemExit(33)\n"
    )
    await remote.run(f"python3 -c {shlex.quote(script)} {shlex.quote(name)} {owner_id(spec)}", step="docker")


async def apply_remote_changes(
    remote: RemoteServer, old_spec: InstallSpec, new_spec: InstallSpec, artifacts: dict, changed, log, checkpoint=None
) -> None:
    """Применяет выбранные изменения без повторной установки ноды."""
    from .addons import _antitorrent_rules, _fail2ban, _nft_service, _shaping, manage_addons

    changed = set(changed)
    if old_spec.address != new_spec.address or old_spec.panel_ref != new_spec.panel_ref:
        raise InstallError("changes", "Перенос сервера или панели требует отдельной установки", retryable=False)
    artifacts.setdefault("remote_owner_id", owner_id(old_spec))
    new_spec._owner_id = artifacts["remote_owner_id"]
    remote.spec = new_spec
    await claim_paths(remote, new_spec)
    if "node_port" in changed:
        await _check_container_owner(remote, new_spec, "remnanode")
    if _uses_tls(new_spec) and changed.intersection({"transport", "domain", "site_template", "email"}):
        await _check_container_owner(remote, new_spec, "cdn-nginx")
    if "node_port" in changed:
        await _management_firewall(remote, new_spec, additional_ports=(old_spec.node_port,))
    elif "panel_sources" in changed:
        await _management_firewall(remote, new_spec)
    if "node_port" in changed:
        await log("Обновление порта управления нодой")
        await remote.write(ROOT + "/node/compose.json", json.dumps(_node_compose(new_spec, artifacts)))
        await remote.run(
            f"cd {ROOT}/node && docker compose -f compose.json up -d --no-build --pull never",
            step="docker",
            timeout=180,
        )
        await _management_firewall(remote, new_spec)
    firewall_fields = {"transport", "client_port", "node_port", "ssh_port", "panel_sources", "trusted_ssh_sources"}
    if changed.intersection(firewall_fields):
        await _change_firewall(remote, old_spec, new_spec, artifacts)
    old_tls, new_tls = _uses_tls(old_spec), _uses_tls(new_spec)
    if new_tls and changed.intersection({"transport", "domain"}):
        await log("Обновление TLS-прокси и сертификата")
        await _deploy_tls(remote, new_spec, artifacts, log)
    elif old_tls and not new_tls:
        await _stop_tls(remote, new_spec)
    elif new_tls and "site_template" in changed:
        await remote.write(ROOT + "/nginx/html/index.html", _site_html(new_spec.site_template), mode=0o644)
        await remote.run("docker exec cdn-nginx nginx -t && docker exec cdn-nginx nginx -s reload", step="nginx")
    if new_tls and "email" in changed:
        cert_dir = ROOT + "/certbot"
        await remote.run(
            f"docker run --rm -v {cert_dir}/certs:/etc/letsencrypt -v {cert_dir}/var-lib:/var/lib/letsencrypt "
            "certbot/certbot:v3.2.0 update_account --non-interactive --agree-tos --email "
            + shlex.quote(new_spec.email),
            step="certificate",
            timeout=120,
        )
    installed = set(artifacts.get("installed_addons") or [])
    if "addons" in changed:
        for name in sorted(installed - set(new_spec.addons)):
            await manage_addons(remote, new_spec, artifacts, "addon", {"name": name, "enabled": False}, log, checkpoint)
        for name in new_spec.addons:
            if name not in installed:
                await manage_addons(
                    remote, new_spec, artifacts, "addon", {"name": name, "enabled": True}, log, checkpoint
                )
    installed = set(artifacts.get("installed_addons") or [])
    if "fail2ban" in installed.intersection(old_spec.addons) and changed.intersection({
        "ssh_port",
        "trusted_ssh_sources",
    }):
        await _fail2ban(remote, new_spec, artifacts, checkpoint)
    if "antitorrent" in installed.intersection(old_spec.addons) and changed.intersection(firewall_fields):
        await _nft_service(remote, "antitorrent", _antitorrent_rules(new_spec))
    if "shaping" in changed or (
        artifacts.get("shaping", {}).get("enabled") and changed.intersection({"transport", "client_port"})
    ):
        await _shaping(remote, new_spec, artifacts, new_spec.shaping or {"enabled": False})
    if changed.intersection({"exit_ip", "exit_interface"}):
        await _cleanup_exit_alias(remote, new_spec, artifacts)
    if checkpoint:
        await checkpoint(artifacts)


async def _deploy_tls(remote: RemoteServer, spec: InstallSpec, artifacts: dict, log) -> None:
    """Выпускает сертификат и запускает собственный nginx-контейнер."""
    await ensure_http_tools(remote)
    domain = spec.domain
    cert_dir = ROOT + "/certbot"
    await _replace_container(remote, spec, "cdn-nginx")
    existing_cert = await remote.run(
        f"test -s {shlex.quote(cert_dir + '/certs/live/' + domain + '/fullchain.pem')} && echo YES || true",
        step="certificate",
    )
    if "YES" not in existing_cert:
        await log("Проверка порта 80 и выпуск сертификата origin")
        await remote.run("docker stop cdn-nginx >/dev/null 2>&1 || true", step="certificate")
        await remote.run("test -z \"$(ss -tlnH 'sport = :80')\"", step="certificate")
        await remote.run(
            f"mkdir -p {cert_dir}/certs {cert_dir}/var-lib && docker run --rm --network host "
            f"-v {cert_dir}/certs:/etc/letsencrypt -v {cert_dir}/var-lib:/var/lib/letsencrypt "
            f"certbot/certbot:v3.2.0 certonly --standalone --non-interactive --agree-tos "
            f"--email {shlex.quote(spec.email)} -d {shlex.quote(domain)}",
            step="certificate",
            timeout=300,
        )
    await remote.run(
        f"test -s {shlex.quote(cert_dir + '/certs/live/' + domain + '/fullchain.pem')}", step="certificate"
    )
    nginx = _nginx_config(spec, artifacts)
    await remote.write(ROOT + "/nginx/nginx.conf", nginx, mode=0o644)
    await remote.write(ROOT + "/nginx/html/index.html", _site_html(spec.site_template), mode=0o644)
    image = "openresty/openresty:1.25.3.1-alpine" if spec.transport == "cdn_vk" else "nginx:1.28"
    compose = {
        "services": {
            "cdn-nginx": {
                "container_name": "cdn-nginx",
                "image": image,
                "network_mode": "host",
                "restart": "always",
                "labels": {"io.solobot.remnawave.owner": owner_id(spec)},
                "volumes": [
                    "./nginx.conf:/etc/nginx/conf.d/default.conf:ro",
                    "./html:/usr/share/nginx/html:ro",
                    cert_dir + "/certs:/etc/letsencrypt:ro",
                ],
            }
        }
    }
    await remote.write(ROOT + "/nginx/compose.json", json.dumps(compose))
    await remote.run(
        f"cd {ROOT}/nginx && docker compose -f compose.json run --rm --no-deps cdn-nginx nginx -t && "
        "docker compose -f compose.json up -d --force-recreate",
        step="nginx",
        timeout=600,
    )
    renewal = (
        f"set -eu\nflock -w 120 {_LOCK} bash -c "
        + shlex.quote(
            "docker stop cdn-nginx >/dev/null; "
            "trap 'docker start cdn-nginx >/dev/null' EXIT; "
            f"docker run --rm --network host -v {cert_dir}/certs:/etc/letsencrypt "
            f"-v {cert_dir}/var-lib:/var/lib/letsencrypt certbot/certbot:v3.2.0 renew --standalone --quiet"
        )
        + "\n"
    )
    await remote.write(ROOT + "/certbot/renew.sh", renewal, mode=0o700)
    await remote.write(
        "/etc/systemd/system/solobot-remnawave-cert.service",
        (
            "[Unit]\nDescription=Renew node TLS certificate\nAfter=docker.service\n"
            f"[Service]\nType=oneshot\nExecStart=/bin/bash {ROOT}/certbot/renew.sh\n"
        ),
        mode=0o644,
    )
    await remote.write(
        "/etc/systemd/system/solobot-remnawave-cert.timer",
        (
            "[Unit]\nDescription=Node certificate renewal\n[Timer]\nOnCalendar=*-*-* 03,15:00:00\n"
            "RandomizedDelaySec=600\nPersistent=true\n[Install]\nWantedBy=timers.target\n"
        ),
        mode=0o644,
    )
    await remote.run(
        "systemctl daemon-reload && systemctl enable --now solobot-remnawave-cert.timer", step="certificate"
    )
    if spec.transport.startswith("cdn_"):
        port = 8443 if spec.transport == "cdn_vk" else 4443
        await log("Ожидание Xray и проверка origin")
        await remote.run(
            f"for i in $(seq 1 60); do ss -tlnH 'sport = :{port}' | grep -q 127.0.0.1 && exit 0; sleep 5; done; exit 1",
            step="origin",
            timeout=310,
        )
        external_port = 443 if spec.transport == "cdn_vk" else 7443
        url = f"https://{domain}:{external_port}{artifacts['xhttp_path']}"
        status = await remote.run(
            f"curl --silent --show-error --max-time 20 -o /dev/null -w '%{{http_code}}' {shlex.quote(url)}",
            step="origin",
            timeout=30,
        )
        if status.strip() != "400":
            raise InstallError("origin", "Origin не подтвердил цепочку nginx → Xray (ожидался HTTP 400)")


async def ensure_http_tools(remote: RemoteServer) -> None:
    """Обеспечивает HTTPS-проверки независимо от состояния Docker."""
    await remote.run(
        "command -v curl >/dev/null && test -s /etc/ssl/certs/ca-certificates.crt || "
        f"( {APT} update && {APT} install -y curl ca-certificates )",
        step="dependencies",
        timeout=900,
    )


def _site_html(template: str) -> str:
    """Возвращает самостоятельную заглушку без внешних ресурсов."""
    if template == "streaming":
        return (
            "<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>Frame — independent cinema</title><style>body{margin:0;background:#101218;color:#fff;font:16px system-ui}"
            "header,main,footer{max-width:1000px;margin:auto;padding:28px}header{font-size:26px;letter-spacing:.2em}"
            "h1{font-size:clamp(36px,7vw,70px);max-width:650px}p{color:#bfc6d6;max-width:600px;line-height:1.7}"
            ".films{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:20px;margin-top:50px}"
            "article{padding:25px;border-radius:20px;min-height:200px;background:linear-gradient(145deg,#233751,#493441)}"
            "article:nth-child(2){background:linear-gradient(145deg,#2a4a46,#1c2539)}"
            "article:nth-child(3){background:linear-gradient(145deg,#665336,#3b293e)}footer{color:#8f99ab}</style>"
            "<header>FRAME</header><main><p>Stories worth discovering</p><h1>A different perspective.</h1>"
            "<p>A collection of independent films, quiet journeys and unexpected encounters. New stories are coming soon.</p>"
            "<section class=films><article><h2>Beyond the hills</h2><p>Documentary · 2026</p></article>"
            "<article><h2>After the rain</h2><p>Short film · 2026</p></article>"
            "<article><h2>A place to begin</h2><p>Drama · 2026</p></article></section></main>"
            "<footer>Frame · Independent stories. Coming soon.</footer></html>\n"
        )
    return (
        "<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Studio — a new beginning</title><style>body{margin:0;background:#f5f3ed;color:#25352b;font:18px system-ui}"
        "main{max-width:900px;margin:12vh auto;padding:32px}small{letter-spacing:.2em}"
        "h1{font-size:clamp(44px,8vw,84px);font-weight:500;max-width:700px;line-height:1.05}"
        "p{max-width:520px;line-height:1.8;color:#5d6b61}hr{border:0;border-top:1px solid #cdd3c9;margin-top:64px}</style>"
        "<main><small>STUDIO</small><h1>Good things take shape.</h1><p>We are building a thoughtful new space for ideas, "
        "people and possibilities. Our new website will be available soon.</p><hr><p>Thank you for visiting.</p></main></html>\n"
    )


def _nginx_config(spec: InstallSpec, artifacts: dict) -> str:
    """Создаёт изолированный TLS-прокси без изменения системного nginx."""
    certificate = f"/etc/letsencrypt/live/{spec.domain}"
    tls = (
        f"ssl_certificate {certificate}/fullchain.pem;\nssl_certificate_key {certificate}/privkey.pem;\n"
        "ssl_protocols TLSv1.2 TLSv1.3;\nserver_tokens off;\n"
    )
    if spec.transport == "vless_tcp_selfsteal":
        return (
            f"server {{ listen 80; listen [::]:80; server_name {spec.domain}; "
            "location / { return 301 https://$host$request_uri; } }\n"
            f"server {{ listen 127.0.0.1:8443 ssl proxy_protocol; http2 on; server_name {spec.domain};\n"
            + tls
            + "set_real_ip_from 127.0.0.1; real_ip_header proxy_protocol;\n"
            "root /usr/share/nginx/html; index index.html; location / { try_files $uri $uri/ /index.html; }\n}\n"
        )
    path = artifacts.get("xhttp_path")
    if not isinstance(path, str) or not re.fullmatch(r"/[a-z0-9]+/[a-z0-9]+/[a-z0-9]+/", path):
        raise InstallError("nginx", "Некорректный путь XHTTP", retryable=False)
    is_vk = spec.transport == "cdn_vk"
    port, backend = (443, 8443) if is_vk else (7443, 4443)
    extra = (
        "more_clear_headers Server;\n"
        if is_vk
        else "client_header_buffer_size 64k; large_client_header_buffers 8 128k;\n"
    )
    location = (
        f"location {path} {{ proxy_pass http://127.0.0.1:{backend}; proxy_http_version 1.1; "
        "proxy_set_header Host $host; proxy_buffering off; proxy_request_buffering off; proxy_cache off; "
        "proxy_read_timeout 3600s; client_max_body_size 0; }\n"
    )
    if is_vk:
        location += (
            f"location = {path.rstrip('/')} {{ proxy_pass http://127.0.0.1:{backend}{path}; "
            "proxy_http_version 1.1; proxy_set_header Host $host; proxy_buffering off; "
            "proxy_request_buffering off; proxy_cache off; proxy_read_timeout 3600s; client_max_body_size 0; }\n"
        )
    return (
        f"server {{ listen {port} ssl default_server; listen [::]:{port} ssl default_server; http2 on; server_name _;\n"
        + tls
        + extra
        + "add_header Cache-Control 'no-store,no-cache,must-revalidate' always;\n"
        + "add_header CDN-Cache-Control no-store always;\nadd_header Accept-Ranges none always;\n"
        + location
        + "location / { root /usr/share/nginx/html; index index.html; }\n}\n"
    )

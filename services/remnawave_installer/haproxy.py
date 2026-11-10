import hashlib
import ipaddress
import json
import re
import shlex

from .remote import (
    APT,
    ROOT,
    RemoteServer,
    _check_container_owner,
    _listener_conflict,
    _node_listener_pids,
    claim_paths,
    ensure_docker,
    owner_id,
)
from .types import TCP_REALITY_TRANSPORTS, InstallError, InstallSpec


HAPROXY_IMAGE = "haproxy:3.2.26-alpine"
HAPROXY_CONTAINER = "solobot-haproxy"
_DIRECTORY = ROOT + "/haproxy"
_CONFIG = _DIRECTORY + "/haproxy.cfg"
_COMPOSE = _DIRECTORY + "/compose.json"
_RELOAD_SCRIPT = """import hashlib,json,os,pathlib,re,signal,stat,subprocess,sys,tempfile,time
root=pathlib.Path(sys.argv[1]); owner,image,expected,desired,target,ports=sys.argv[2:]
ports=json.loads(ports); cfg=root/'haproxy/haproxy.cfg'; manifest=root/'.managed-files.json'
mount='/usr/local/etc/haproxy/haproxy.cfg'; name='solobot-haproxy'
def require(condition,code):
 if not condition: raise SystemExit(code)
def digest(value):
 return hashlib.sha256(value).hexdigest()
def run(arguments,**kwargs):
 return subprocess.run(arguments,capture_output=True,check=True,timeout=20,**kwargs).stdout
def read(path):
 descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
 with os.fdopen(descriptor,'rb') as stream:
  metadata=os.fstat(stream.fileno()); require(stat.S_ISREG(metadata.st_mode),31)
  return stream.read(),metadata
def inspect():
 data=json.loads(run(['docker','inspect',name]))[0]
 require((data.get('Config',{}).get('Labels') or {}).get('io.solobot.remnawave.owner')==owner,33)
 require(data.get('State',{}).get('Running') is True and data.get('Config',{}).get('Image')==image,34)
 require(data.get('HostConfig',{}).get('NetworkMode')=='host',35)
 mounts=[item for item in data.get('Mounts',[]) if item.get('Destination')==mount]
 require(len(mounts)==1 and mounts[0].get('Type')=='bind' and mounts[0].get('Source')==str(cfg) and mounts[0].get('RW') is False,36)
 master=data['State']['Pid']; require(type(master) is int and master>1,37)
 arguments=pathlib.Path('/proc/'+str(master)+'/cmdline').read_bytes().split(b'\\0')
 require(pathlib.Path(os.fsdecode(arguments[0])).name=='haproxy' and b'-W' in arguments and b'-db' in arguments,38)
 require(any(arguments[index]==b'-f' and arguments[index+1]==mount.encode() for index in range(len(arguments)-1)),38)
 return master
def workers(master):
 result=set()
 for child in pathlib.Path('/proc/'+str(master)+'/task/'+str(master)+'/children').read_text().split():
  try:
   if pathlib.Path('/proc/'+child+'/comm').read_text().strip()=='haproxy': result.add(int(child))
  except FileNotFoundError: pass
 return result
def runtime_hash():
 return digest(run(['docker','exec',name,'cat',mount]))
def atomic_manifest(value,metadata):
 descriptor,temporary=tempfile.mkstemp(prefix='.solo-reload-',dir=str(root))
 try:
  os.fchmod(descriptor,stat.S_IMODE(metadata.st_mode))
  with os.fdopen(descriptor,'wb') as stream:
   stream.write(value); stream.flush(); os.fsync(stream.fileno())
  os.replace(temporary,manifest)
 finally:
  if os.path.exists(temporary): os.unlink(temporary)
def write(value,inode,current):
 descriptor=os.open(cfg,os.O_RDWR|os.O_NOFOLLOW)
 with os.fdopen(descriptor,'r+b',buffering=0) as stream:
  metadata=os.fstat(stream.fileno())
  require((metadata.st_dev,metadata.st_ino)==inode and digest(stream.read())==current,39)
  try:
   stream.seek(0); offset=0
   while offset<len(value):
    count=stream.write(value[offset:]); require(count is not None and count>0,40); offset+=count
   stream.truncate(len(value)); os.fsync(stream.fileno())
  except BaseException:
   stream.seek(0); state['partial']=digest(stream.read()); raise
def reload(master,expected_ports,checksum):
 require(inspect()==master and runtime_hash()==checksum,41)
 previous=workers(master); require(bool(previous),42)
 os.kill(master,signal.SIGUSR2); deadline=time.monotonic()+35
 while time.monotonic()<deadline:
  current=workers(master); listeners={}
  for line in run(['ss','-tlnpH']).decode().splitlines():
   fields=line.split()
   if len(fields)<4 or not fields[3].rsplit(':',1)[-1].isdigit(): continue
   address,port=fields[3].rsplit(':',1)
   family='ipv4' if address=='0.0.0.0' else 'ipv6' if address in ('[::]','::') else None
   listeners.setdefault((int(port),family),set()).update(int(pid) for pid in re.findall(r'pid=(\\d+)',line))
  if current-previous and all(listeners.get((port,family),set()) & current for port in expected_ports for family in ('ipv4','ipv6')):
   require(inspect()==master and runtime_hash()==checksum,43); return
  time.sleep(1)
 raise SystemExit(44)
def main():
 global state
 state={}
 old,metadata=read(cfg); before,manifest_metadata=read(manifest); managed=json.loads(before)
 require(digest(old)==expected and managed.get(str(cfg))==expected,45)
 candidate,candidate_metadata=read(pathlib.Path(target))
 require(candidate_metadata.st_uid==metadata.st_uid and not candidate_metadata.st_mode & 0o022,46)
 require(digest(candidate)==desired and managed.get(target)==desired,46)
 master=inspect(); require(runtime_hash()==expected,47)
 run(['docker','exec','-i',name,'haproxy','-c','-f','/dev/stdin'],input=candidate)
 backup=root/'haproxy/backups'/str(time.time_ns()); backup.mkdir(mode=0o700,parents=True)
 for filename,content in [('haproxy.cfg',old),('managed-files.json',before)]:
  descriptor=os.open(backup/filename,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(descriptor,'wb') as stream:
   stream.write(content); stream.flush(); os.fsync(stream.fileno())
 inode=(metadata.st_dev,metadata.st_ino); checksum=digest(candidate); updated=None
 try:
  write(candidate,inode,expected)
  managed[str(cfg)]=checksum; updated=json.dumps(managed).encode(); atomic_manifest(updated,manifest_metadata)
  reload(master,ports,checksum)
 except BaseException:
  current,_=read(cfg); require(digest(current) in {expected,checksum,state.get('partial')},48)
  write(old,inode,digest(current)); current_manifest,_=read(manifest)
  require(current_manifest in (before,updated),49); atomic_manifest(before,manifest_metadata)
  old_ports=sorted({int(port) for port in re.findall(rb'(?m)^    bind (?:0\\.0\\.0\\.0|\\[::\\]):(\\d+)',old)})
  reload(master,old_ports,expected)
  raise
 print('RELOADED')
def interrupted(signum,frame):
 raise SystemExit(50)
signal.signal(signal.SIGTERM,interrupted)
main()
"""


def _target(artifacts: dict) -> tuple[str, int]:
    """Проверяет сохранённый адрес выхода каскада."""
    value = artifacts.get("haproxy") if isinstance(artifacts, dict) else None
    try:
        address = str(ipaddress.ip_address(value["address"]))
        port = value["port"]
    except (ValueError, KeyError, TypeError):
        raise InstallError("haproxy", "Не сохранён проверенный выход каскада HAProxy", retryable=False) from None
    if type(port) is not int or not 1 <= port <= 65535:
        raise InstallError("haproxy", "Некорректный порт выхода каскада HAProxy", retryable=False)
    return address, port


def _route_targets(spec: InstallSpec, artifacts: dict) -> list[tuple[InstallSpec, str, int]]:
    """Проверяет адреса и отдельные порты всех направлений."""
    if spec.role != "haproxy_entry" or spec.transport not in TCP_REALITY_TRANSPORTS:
        raise InstallError("haproxy", "HAProxy поддерживает вход TCP Reality или Selfsteal", retryable=False)
    result = []
    ports = set()
    children = artifacts.get("cascade_routes") or {}
    if not isinstance(children, dict):
        raise InstallError("haproxy", "Некорректные сохранённые направления каскада", retryable=False)
    for index, route in enumerate(spec.cascade_specs()):
        if type(route.client_port) is not int or not 1 <= route.client_port <= 65535 or route.client_port in ports:
            raise InstallError("haproxy", "Некорректный или повторяющийся клиентский порт HAProxy", retryable=False)
        child = artifacts if index == 0 else children.get(route.exit_install_id, {})
        address, port = _target(child)
        if ipaddress.ip_address(spec.transit_address()).version != ipaddress.ip_address(address).version:
            raise InstallError(
                "haproxy", "IP связи входа и выхода каскада должны быть одной версии: IPv4 или IPv6", retryable=False
            )
        ports.add(route.client_port)
        result.append((route, address, port))
    return result


def build_haproxy_config(spec: InstallSpec, artifacts: dict) -> str:
    """Собирает прозрачный TCP-прокси с PROXY v2."""
    routes = _route_targets(spec, artifacts)
    config = (
        "global\n"
        "    log stdout format raw local0\n"
        "    maxconn 65535\n"
        "defaults\n"
        "    log global\n"
        "    mode tcp\n"
        "    option tcplog\n"
        "    option clitcpka\n"
        "    option srvtcpka\n"
        "    timeout connect 10s\n"
        "    timeout client 24h\n"
        "    timeout server 24h\n"
    )
    for index, (route, address, port) in enumerate(routes):
        name = "reality" if index == 0 else f"reality_{route.client_port}"
        endpoint = f"[{address}]:{port}" if ipaddress.ip_address(address).version == 6 else f"{address}:{port}"
        source = ""
        if spec.cascade_address:
            source_address = (
                f"ipv6@[{spec.cascade_address}]"
                if ipaddress.ip_address(spec.cascade_address).version == 6
                else f"ipv4@{spec.cascade_address}"
            )
            source = f"    source {source_address}\n"
        config += (
            f"frontend {name}\n"
            f"    bind 0.0.0.0:{route.client_port}\n"
            f"    bind [::]:{route.client_port} v6only\n"
            f"    default_backend {name}_exit\n"
            f"backend {name}_exit\n"
            f"{source}"
            f"    server exit {endpoint} send-proxy-v2 check inter 5s fall 3 rise 2\n"
        )
    return config


def _compose(spec: InstallSpec) -> dict:
    """Описывает отдельный контейнер TCP-прокси."""
    return {
        "services": {
            HAPROXY_CONTAINER: {
                "container_name": HAPROXY_CONTAINER,
                "image": HAPROXY_IMAGE,
                "network_mode": "host",
                "restart": "always",
                "user": "0:0",
                "read_only": True,
                "cap_drop": ["ALL"],
                "cap_add": ["NET_BIND_SERVICE"],
                "security_opt": ["no-new-privileges:true"],
                "labels": {"io.solobot.remnawave.owner": owner_id(spec)},
                "volumes": [_CONFIG + ":/usr/local/etc/haproxy/haproxy.cfg:ro"],
            },
        },
    }


async def check_haproxy(remote: RemoteServer, spec: InstallSpec, artifacts: dict) -> None:
    """Проверяет контейнер, собственный listener и доступность выхода."""
    routes = _route_targets(spec, artifacts)
    expected = hashlib.sha256(build_haproxy_config(spec, artifacts).encode()).hexdigest()
    script = (
        "import hashlib,json,socket,subprocess,sys\n"
        "owner,expected,source,targets=sys.argv[1:]\n"
        f"data=json.loads(subprocess.run(['docker','inspect',{HAPROXY_CONTAINER!r}],capture_output=True,check=True).stdout)[0]\n"
        "if (data.get('Config',{}).get('Labels') or {}).get('io.solobot.remnawave.owner')!=owner: raise SystemExit(33)\n"
        "if not data.get('State',{}).get('Running'): raise SystemExit(34)\n"
        "health=(data.get('State',{}).get('Health') or {}).get('Status')\n"
        "if health in ('unhealthy','starting'): raise SystemExit(35)\n"
        f"if not any(m.get('Source')=={_CONFIG!r} and m.get('Destination')=='/usr/local/etc/haproxy/haproxy.cfg' "
        "and m.get('RW') is False for m in data.get('Mounts',[])): raise SystemExit(36)\n"
        f"runtime=subprocess.run(['docker','exec',{HAPROXY_CONTAINER!r},'cat','/usr/local/etc/haproxy/haproxy.cfg'],check=True,capture_output=True)\n"
        "if hashlib.sha256(runtime.stdout).hexdigest()!=expected: raise SystemExit(37)\n"
        f"subprocess.run(['docker','exec',{HAPROXY_CONTAINER!r},'haproxy','-c','-f','/usr/local/etc/haproxy/haproxy.cfg'],check=True,capture_output=True)\n"
        "for address,port in json.loads(targets):\n"
        " with socket.create_connection((address,int(port)),timeout=10,source_address=(source,0) if source else None): pass\n"
    )
    targets = json.dumps([[address, port] for _route, address, port in routes])
    await remote.run(
        f"python3 -c {shlex.quote(script)} {owner_id(spec)} {expected} {shlex.quote(spec.cascade_address)} {shlex.quote(targets)}",
        step="haproxy_verify",
        timeout=30,
    )
    for _attempt in range(6):
        pids = await _node_listener_pids(remote, spec, name=HAPROXY_CONTAINER)
        listeners = await remote.run("ss -tlnpH", step="haproxy_verify")
        present = {
            int(line.split()[3].rsplit(":", 1)[-1])
            for line in listeners.splitlines()
            if len(line.split()) >= 4 and line.split()[3].rsplit(":", 1)[-1].isdecimal()
        }
        if all(
            route.client_port in present and not _listener_conflict(listeners, route.client_port, pids)
            for route, _, _ in routes
        ):
            return
        await remote.run("sleep 2", step="haproxy_verify", timeout=5)
    raise InstallError("haproxy_verify", "Собственный контейнер HAProxy не подтвердил все клиентские TCP-порты")


async def _running_haproxy(remote: RemoteServer, spec: InstallSpec) -> bool:
    """Определяет работающий собственный контейнер без его остановки."""
    script = (
        "import json,subprocess,sys\n"
        f"result=subprocess.run(['docker','inspect',{HAPROXY_CONTAINER!r}],capture_output=True)\n"
        "if result.returncode:\n"
        " subprocess.run(['docker','info'],capture_output=True,check=True); raise SystemExit(0)\n"
        "data=json.loads(result.stdout)[0]\n"
        "if (data.get('Config',{}).get('Labels') or {}).get('io.solobot.remnawave.owner')!=sys.argv[1]: raise SystemExit(33)\n"
        "if data.get('State',{}).get('Running') is True: print('RUNNING')\n"
    )
    return (await remote.run(f"python3 -c {shlex.quote(script)} {owner_id(spec)}", step="haproxy")).strip() == "RUNNING"


async def _reload_haproxy(
    remote: RemoteServer, spec: InstallSpec, previous: str, config: str, *, rollback: bool = False
) -> None:
    """Обновляет bind-mounted inode и плавно переключает рабочие процессы."""
    candidate = _DIRECTORY + ("/rollback.cfg" if rollback else "/candidate.cfg")
    if rollback:
        await remote.write(candidate, config, mode=0o644)
    expected = hashlib.sha256(previous.encode()).hexdigest()
    desired = hashlib.sha256(config.encode()).hexdigest()
    ports = sorted({int(port) for port in re.findall(r"(?m)^    bind (?:0\.0\.0\.0|\[::\]):(\d+)", config)})
    if not ports:
        raise InstallError("haproxy", "Конфигурация HAProxy не содержит клиентских портов", retryable=False)
    result = await remote.run(
        f"python3 -c {shlex.quote(_RELOAD_SCRIPT)} {ROOT} {owner_id(spec)} {HAPROXY_IMAGE} "
        f"{expected} {desired} {candidate} {shlex.quote(json.dumps(ports))}",
        step="haproxy_rollback" if rollback else "haproxy_reload",
        timeout=180,
    )
    if result.strip() != "RELOADED":
        raise InstallError("haproxy", "Плавная перезагрузка HAProxy не подтверждена")


async def deploy_haproxy(remote: RemoteServer, spec: InstallSpec, artifacts: dict, log, checkpoint=None) -> None:
    """Проверяет новую конфигурацию и возвращает прежнюю при сбое."""
    config = build_haproxy_config(spec, artifacts)
    await claim_paths(remote, spec)
    await ensure_docker(remote)
    await _check_container_owner(remote, spec, HAPROXY_CONTAINER)
    snapshot_script = (
        "import hashlib,json,pathlib,sys\n"
        "root=pathlib.Path(sys.argv[1]); result={}\n"
        "manifest=root/'.managed-files.json'\n"
        "managed=json.loads(manifest.read_text()) if manifest.is_file() else {}\n"
        "for name in ('haproxy.cfg','compose.json'):\n"
        " path=root/'haproxy'/name\n"
        " if path.is_symlink(): raise SystemExit(31)\n"
        " if path.exists():\n"
        "  if managed.get(str(path))!=hashlib.sha256(path.read_bytes()).hexdigest(): raise SystemExit(32)\n"
        "  result[name]=path.read_text()\n"
        "print(json.dumps(result))\n"
    )
    try:
        previous = json.loads(await remote.run(f"python3 -c {shlex.quote(snapshot_script)} {ROOT}", step="haproxy"))
    except (TypeError, ValueError):
        raise InstallError("haproxy", "Не удалось проверить предыдущую конфигурацию HAProxy", retryable=False) from None
    if not isinstance(previous, dict) or set(previous) - {"haproxy.cfg", "compose.json"}:
        raise InstallError("haproxy", "Некорректный снимок предыдущей конфигурации HAProxy", retryable=False)
    running = await _running_haproxy(remote, spec)
    if running and not all(name in previous for name in ("haproxy.cfg", "compose.json")):
        raise InstallError("haproxy", "У работающего HAProxy отсутствует собственная конфигурация", retryable=False)
    candidate = _DIRECTORY + "/candidate.cfg"
    await remote.write(candidate, config, mode=0o644)
    await log("Проверка конфигурации и запуск HAProxy с PROXY v2")
    if not running:
        await remote.run(f"docker pull {HAPROXY_IMAGE}", step="haproxy", timeout=600)
        await remote.run(
            f"docker run --rm --network none --read-only -v {candidate}:/usr/local/etc/haproxy/haproxy.cfg:ro "
            f"{HAPROXY_IMAGE} haproxy -c -f /usr/local/etc/haproxy/haproxy.cfg",
            step="haproxy",
            timeout=60,
        )
    reloaded = False
    try:
        if running:
            await _reload_haproxy(remote, spec, previous["haproxy.cfg"], config)
            reloaded = True
        else:
            await remote.write(_CONFIG, config, mode=0o644)
            await remote.write(_COMPOSE, json.dumps(_compose(spec)))
            await remote.run(
                f"cd {_DIRECTORY} && docker compose -f compose.json up -d --force-recreate --pull never",
                step="haproxy",
                timeout=180,
            )
        await check_haproxy(remote, spec, artifacts)
    except InstallError:
        await _check_container_owner(remote, spec, HAPROXY_CONTAINER)
        if running:
            if reloaded:
                await _reload_haproxy(remote, spec, config, previous["haproxy.cfg"], rollback=True)
                await log("Восстановлена предыдущая конфигурация HAProxy")
        elif all(name in previous for name in ("haproxy.cfg", "compose.json")):
            await remote.write(_CONFIG, previous["haproxy.cfg"], mode=0o644)
            await remote.write(_COMPOSE, previous["compose.json"])
            await remote.run(
                f"cd {_DIRECTORY} && docker compose -f compose.json up -d --force-recreate --pull never",
                step="haproxy_rollback",
                timeout=180,
            )
            await log("Восстановлена предыдущая конфигурация HAProxy")
        else:
            remove_script = (
                "import json,subprocess,sys\n"
                f"result=subprocess.run(['docker','inspect',{HAPROXY_CONTAINER!r}],capture_output=True)\n"
                "if result.returncode:\n"
                " subprocess.run(['docker','info'],check=True,capture_output=True); raise SystemExit(0)\n"
                "data=json.loads(result.stdout)[0]\n"
                "if (data.get('Config',{}).get('Labels') or {}).get('io.solobot.remnawave.owner')!=sys.argv[1]: raise SystemExit(33)\n"
                f"subprocess.run(['docker','rm','-f',{HAPROXY_CONTAINER!r}],check=True,capture_output=True)\n"
            )
            await remote.run(f"python3 -c {shlex.quote(remove_script)} {owner_id(spec)}", step="haproxy_rollback")
        raise
    from .remote import _open_firewall

    await _open_firewall(remote, spec)
    artifacts.update(remote_deployed=True, haproxy_image=HAPROXY_IMAGE)
    if checkpoint:
        await checkpoint(artifacts)


async def protect_haproxy_exit(remote: RemoteServer, spec: InstallSpec, *, additional_ports=()) -> None:
    """Разрешает PROXY v2 только с доверенных адресов входа."""
    if spec.role != "haproxy_exit":
        return
    sources = getattr(spec, "haproxy_sources", [])
    if not sources:
        raise InstallError("firewall", "Укажите доверенные IP входов HAProxy", retryable=False)
    try:
        networks = [ipaddress.ip_network(value, strict=False) for value in sources]
    except ValueError:
        raise InstallError("firewall", "Некорректный IP входа HAProxy", retryable=False) from None
    if any(network.prefixlen != network.max_prefixlen for network in networks):
        raise InstallError("firewall", "Разрешены только отдельные IP входов HAProxy", retryable=False)
    ports = {spec.client_port, *additional_ports}
    if any(type(port) is not int or not 1 <= port <= 65535 for port in ports):
        raise InstallError("firewall", "Некорректный порт выхода HAProxy", retryable=False)
    await remote.run(
        f"command -v nft >/dev/null || ( {APT} update && {APT} install -y nftables )",
        step="firewall",
        timeout=900,
    )
    script = (
        "import hashlib,json,pathlib,subprocess,sys\n"
        "root=pathlib.Path(sys.argv[1]); manifest=root/'.managed-files.json'\n"
        "managed=json.loads(manifest.read_text()) if manifest.is_file() else {}\n"
        "paths=(root/'addons'/'haproxy_exit.nft',root/'addons'/'apply-haproxy_exit.py',"
        "pathlib.Path('/etc/systemd/system/solobot-haproxy_exit-nft.service'))\n"
        "owned=[]\n"
        "for path in paths:\n"
        " if path.is_symlink(): raise SystemExit(31)\n"
        " if path.exists():\n"
        "  if managed.get(str(path))!=hashlib.sha256(path.read_bytes()).hexdigest(): raise SystemExit(32)\n"
        "  owned.append(path)\n"
        "listed=subprocess.run(['nft','-j','list','tables'],capture_output=True,check=True)\n"
        "tables=json.loads(listed.stdout).get('nftables',[])\n"
        "present=any(item.get('table',{}).get('family')=='inet' and item.get('table',{}).get('name')=='solobot_haproxy_exit' for item in tables)\n"
        "if present and not owned: raise SystemExit(33)\n"
    )
    await remote.run(f"python3 -c {shlex.quote(script)} {ROOT}", step="firewall")
    lines = [
        "table inet solobot_haproxy_exit {",
        "chain inbound { type filter hook input priority -30; policy accept;",
    ]
    for port in sorted(ports):
        for version, family in ((4, "ip"), (6, "ip6")):
            allowed = [str(network) for network in networks if network.version == version]
            if allowed:
                lines.append(f"tcp dport {port} {family} saddr {{ " + ", ".join(allowed) + " } accept")
        lines.append(f"tcp dport {port} drop")
    lines.extend(["}", "}", ""])
    from .addons import _nft_service

    await _nft_service(remote, "haproxy_exit", "\n".join(lines))

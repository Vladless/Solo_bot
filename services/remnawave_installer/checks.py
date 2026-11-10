import hashlib
import ipaddress
import json
import re
import shlex

from urllib.parse import urlencode

import httpx

from .types import InstallError, InstallSpec, InstallWaitingError, needs_certificate


def _node_runtime_script() -> str:
    """Собирает проверку ноды без вывода секретов и журналов."""
    return """import hashlib,json,os,re,socket,subprocess,sys
owner,address,port,digest=sys.argv[1:]
port=int(port)
result={'status':'unavailable','management_port':port,'log_hints':[]}
def run(args,timeout=3):
 try:
  value=subprocess.run(args,capture_output=True,timeout=timeout,env={**os.environ,'LC_ALL':'C'})
  return value.returncode,value.stdout[:262144],value.stderr[:262144]
 except FileNotFoundError:
  return 127,b'',b'command_not_found'
 except (OSError,subprocess.TimeoutExpired):
  return None,b'',b''
def finish(status):
 result['status']=status
 print(json.dumps(result))
 raise SystemExit(0)
code,output,error=run(['docker','inspect','remnanode'],5)
if code is None: finish('unavailable')
if code:
 finish('missing' if b'No such object' in error or b'No such container' in error else 'unavailable')
try:
 data=json.loads(output)[0]
 config=data.get('Config') or {}
 state=data.get('State') or {}
 if not isinstance(config,dict) or not isinstance(state,dict): raise ValueError
except (ValueError,TypeError,KeyError,IndexError):
 finish('unavailable')
labels=config.get('Labels') or {}
if not isinstance(labels,dict) or labels.get('io.solobot.remnawave.owner')!=owner: finish('foreign')
status=state.get('Status')
result['container_state']=status if status in ('created','running','paused','restarting','removing','exited','dead') else 'unknown'
result['running']=state.get('Running') is True
for source,target in ((state.get('ExitCode'),'exit_code'),(data.get('RestartCount'),'restart_count')):
 if type(source) is int and -1<=source<=1000000: result[target]=source
environment={}
for item in config.get('Env') or []:
 if isinstance(item,str) and '=' in item:
  key,value=item.split('=',1)
  if key in ('NODE_PORT','SECRET_KEY'): environment[key]=value
result['env_port_matches']=environment.get('NODE_PORT')==str(port)
result['secret_matches']=bool(digest) and hashlib.sha256(environment.get('SECRET_KEY','').encode()).hexdigest()==digest
code,output,error=run(['docker','top','remnanode','-eo','pid'])
pids={int(line.strip()) for line in output.decode(errors='replace').splitlines() if line.strip().isdigit()} if code==0 else set()
code,output,error=run(['ss','-tlnpH','sport = :'+str(port)])
result['listener_owned']=None
if code==0:
 lines=[line for line in output.decode(errors='replace').splitlines() if line.strip()]
 found=[{int(value) for value in re.findall(r'pid=(\\d+)',line)} for line in lines]
 result['listener_present']=bool(lines)
 result['listener_owned']=bool(lines) and bool(pids) and all(value and value.issubset(pids) for value in found)
for name,target in (('loopback_connect','127.0.0.1'),('address_connect',address)):
 try:
  with socket.create_connection((target,port),timeout=2): result[name]=True
 except OSError:
  result[name]=False
code,output,error=run(['ufw','status','verbose'])
text=output.decode(errors='replace')
result['ufw']='active' if code==0 and 'Status: active' in text else 'inactive' if code==0 and 'Status: inactive' in text else 'not_installed' if code==127 and error==b'command_not_found' else 'unavailable'
if code==0:
 lines=[line for line in text.splitlines() if line.split() and line.split()[0] in (str(port),str(port)+'/tcp')]
 result['ufw_management_allow']=any(re.search(r'\\bALLOW\\b',line) for line in lines)
 result['ufw_management_deny']=any(re.search(r'\\b(?:DENY|REJECT)\\b',line) for line in lines)
 policy=re.search(r'Default:\\s*(allow|deny|reject)\\s*\\(incoming\\)',text)
 result['ufw_default_incoming']=policy.group(1) if policy else 'unknown'
code,output,error=run(['nft','list','table','inet','solobot_remnawave_mgmt'])
result['management_filter']=True if code==0 else False if code is not None and (b'No such file or directory' in error or b'No such table' in error) else None
if code==0:
 text=output.decode(errors='replace')
 result['management_filter_ipv4']='ip saddr' in text
 result['management_filter_ipv6']='ip6 saddr' in text
code,output,error=run(['docker','logs','--since','10m','--tail','120','remnanode'])
text=(output+error).decode(errors='replace')[-262144:]
patterns={
 'invalid_secret':r'(?:SECRET_KEY[^\\n]*(?:invalid|missing|not found)|invalid[^\\n]*SECRET_KEY)',
 'tls_certificate':r'(?:certificate[^\\n]*(?:expired|not yet valid)|unknown ca|tls[^\\n]*(?:handshake|alert|error)|certificate verify failed)',
 'xray_config':r'(?:failed[^\\n]*(?:start|parse)[^\\n]*xray|xray[^\\n]*(?:failed|fatal)|failed to (?:load|parse) (?:config|configuration)|failed to build)',
 'address_in_use':r'address already in use',
 'address_not_assigned':r'cannot assign requested address',
 'permission':r'(?:permission denied|operation not permitted)',
}
result['log_hints']=[name for name,pattern in patterns.items() if re.search(pattern,text,re.I)]
if state.get('Restarting') is True or status=='restarting': finish('restarting')
if not result['running']: finish('stopped')
if not result['env_port_matches']: finish('port_mismatch')
if digest and not result['secret_matches']: finish('secret_mismatch')
if result.get('listener_present') is False: finish('management_listener_missing')
if result['listener_owned'] is False: finish('management_listener_foreign')
if not result['address_connect']: finish('address_unreachable')
finish('local_ready' if result['listener_owned'] is True else 'unknown')
"""


async def check_node_runtime(remote, spec: InstallSpec, artifacts: dict) -> dict:
    """Проверяет локальную готовность ноды без изменения сервера."""
    if spec.role == "haproxy_entry":
        return {"status": "not_applicable"}
    from .remote import owner_id

    address = str(ipaddress.ip_address(spec.address))
    if type(spec.node_port) is not int or not 1 <= spec.node_port <= 65535:
        raise ValueError("Некорректный порт управления нодой")
    secret = artifacts.get("node_secret")
    digest = hashlib.sha256(secret.encode()).hexdigest() if isinstance(secret, str) and secret else ""
    command = shlex.join([
        "python3",
        "-c",
        _node_runtime_script(),
        owner_id(spec),
        address,
        str(spec.node_port),
        digest,
    ])
    try:
        raw = await remote.run(command, step="verify", timeout=35, secret=True)
        if not isinstance(raw, str) or len(raw) > 32768:
            raise ValueError
        result = json.loads(raw)
    except (InstallError, ValueError, TypeError):
        return {"status": "unavailable", "management_port": spec.node_port}
    if not isinstance(result, dict):
        return {"status": "unavailable", "management_port": spec.node_port}
    statuses = {
        "local_ready",
        "missing",
        "foreign",
        "stopped",
        "restarting",
        "port_mismatch",
        "secret_mismatch",
        "management_listener_missing",
        "management_listener_foreign",
        "address_unreachable",
        "unknown",
        "unavailable",
    }
    status = result.get("status")
    safe = {"status": status if isinstance(status, str) and status in statuses else "unavailable"}
    safe["management_port"] = spec.node_port
    for key in (
        "running",
        "env_port_matches",
        "secret_matches",
        "listener_present",
        "listener_owned",
        "loopback_connect",
        "address_connect",
        "management_filter",
        "management_filter_ipv4",
        "management_filter_ipv6",
        "ufw_management_allow",
        "ufw_management_deny",
    ):
        if type(result.get(key)) is bool:
            safe[key] = result[key]
    for key in ("restart_count", "exit_code"):
        value = result.get(key)
        if type(value) is int and -1 <= value <= 1000000:
            safe[key] = value
    if isinstance(result.get("container_state"), str) and result["container_state"] in {
        "created",
        "running",
        "paused",
        "restarting",
        "removing",
        "exited",
        "dead",
        "unknown",
    }:
        safe["container_state"] = result["container_state"]
    if isinstance(result.get("ufw"), str) and result["ufw"] in {"active", "inactive", "not_installed", "unavailable"}:
        safe["ufw"] = result["ufw"]
    if isinstance(result.get("ufw_default_incoming"), str) and result["ufw_default_incoming"] in {
        "allow",
        "deny",
        "reject",
        "unknown",
    }:
        safe["ufw_default_incoming"] = result["ufw_default_incoming"]
    hints = result.get("log_hints")
    if isinstance(hints, list):
        safe["log_hints"] = [
            name
            for name in hints
            if isinstance(name, str)
            and name
            in {
                "invalid_secret",
                "tls_certificate",
                "xray_config",
                "address_in_use",
                "address_not_assigned",
                "permission",
            }
        ]
    return safe


async def check_origin_dns(spec: InstallSpec) -> None:
    """Проверяет адрес origin до создания ресурсов панели и облака."""
    if not needs_certificate(spec):
        return
    family = "AAAA" if ipaddress.ip_address(spec.address).version == 6 else "A"
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        for resolver in ("https://dns.google/resolve", "https://cloudflare-dns.com/dns-query"):
            try:
                response = await client.get(
                    resolver + "?" + urlencode({"name": spec.domain, "type": family}),
                    headers={"accept": "application/dns-json"},
                )
                response.raise_for_status()
                records = response.json().get("Answer", [])
                if any(
                    item.get("type") == (28 if family == "AAAA" else 1)
                    and ipaddress.ip_address(item.get("data", "").strip()) == ipaddress.ip_address(spec.address)
                    for item in records
                    if isinstance(item, dict)
                ):
                    return
            except (httpx.HTTPError, ValueError, AttributeError, TypeError):
                continue
    raise InstallWaitingError(
        (
            "Домен Hysteria2"
            if spec.transport == "hysteria2"
            else "Домен сайта Selfsteal"
            if spec.transport == "vless_tcp_selfsteal"
            else "Origin"
        )
        + " пока не указывает на выбранный сервер. Обновите DNS и нажмите «Проверить»",
        records=[{"type": family, "name": spec.domain, "value": spec.address}],
    )


async def check_hysteria(remote, spec: InstallSpec) -> None:
    """Проверяет TLS и UDP-порт собственного контейнера Hysteria2."""
    if spec.transport != "hysteria2":
        return
    from .remote import ROOT, _hysteria_certificate_command, _node_listener_pids, owner_id

    await remote.run(_hysteria_certificate_command(spec), step="verify")
    mount_check = (
        "import json,subprocess,sys\n"
        "data=json.loads(subprocess.run(['docker','inspect','remnanode'],capture_output=True,check=True).stdout)[0]\n"
        "if (data.get('Config',{}).get('Labels') or {}).get('io.solobot.remnawave.owner')!=sys.argv[1]: raise SystemExit(33)\n"
        "if not data.get('State',{}).get('Running'): raise SystemExit(34)\n"
        "if not any(m.get('Source')==sys.argv[2] and m.get('Destination')=='/etc/letsencrypt' "
        "and m.get('RW') is False for m in data.get('Mounts',[])): raise SystemExit(35)\n"
    )
    await remote.run(f"python3 -c {shlex.quote(mount_check)} {owner_id(spec)} {ROOT}/certbot/certs", step="verify")
    for _attempt in range(6):
        pids = await _node_listener_pids(remote, spec)
        listeners = await remote.run(f"ss -ulnpH 'sport = :{spec.client_port}'", step="verify")
        for line in listeners.splitlines():
            fields = line.split()
            if len(fields) < 4 or fields[3].rsplit(":", 1)[-1] != str(spec.client_port):
                continue
            process_ids = {int(value) for value in re.findall(r"pid=(\d+)", line)}
            if process_ids and process_ids.issubset(pids):
                return
        await remote.run("sleep 2", step="verify", timeout=5)
    raise InstallError("verify", "Сертификат готов, но собственная нода не слушает клиентский UDP-порт Hysteria2")

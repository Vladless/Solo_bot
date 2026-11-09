import asyncio
import base64
import ipaddress
import json
import re
import secrets
import shlex
import uuid

from .remote import ROOT, owner_id
from .types import NODE_IMAGE, InstallError, _domain


_PROBE_SCRIPT = """import ipaddress,json,os,pathlib,socket,subprocess,sys,time
folder=pathlib.Path(sys.argv[1]); name,image,entry,port,domain,expected,site,owner=sys.argv[2:]
configuration=json.loads((folder/'config.json').read_text())
with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as listener:
 listener.bind(('127.0.0.1',0)); socks_port=listener.getsockname()[1]
configuration['inbounds'][0]['port']=socks_port
runtime=folder/'runtime.json'
with runtime.open('x') as output:
 os.chmod(runtime,0o600); json.dump(configuration,output)
environment={key:value for key,value in os.environ.items() if key.lower() not in {'http_proxy','https_proxy','all_proxy','no_proxy'} }
process=None
def request(arguments):
 return subprocess.run(['curl','--disable','--silent','--show-error','--fail','--connect-timeout','4','--max-time','8','--noproxy','',*arguments],env=environment,capture_output=True,text=True,timeout=10)
try:
 if not expected:
  baseline=request(['--proxy','','--ipv4','https://api64.ipify.org'])
  try:
   expected=str(ipaddress.ip_address(baseline.stdout.strip())) if baseline.returncode==0 else ''
  except ValueError:
   expected=''
  if not expected:
   print(json.dumps({'error':'baseline'})); raise SystemExit(0)
 if site=='yes':
  target='['+entry+']' if ':' in entry else entry
  response=request(['--proxy','','--connect-to',domain+':443:'+target+':'+port,'--output','/dev/null','--write-out','%{http_code}','https://'+domain+'/'])
  if response.returncode or response.stdout.strip()!='200':
   print(json.dumps({'error':'site'})); raise SystemExit(0)
 process=subprocess.Popen(['docker','run','--rm','--pull=never','--network','host','--name',name,'--label','io.solobot.remnawave.owner='+owner,'--entrypoint','xray','--volume',str(runtime)+':/etc/xray/config.json:ro',image,'run','-c','/etc/xray/config.json'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 deadline=time.monotonic()+60
 while time.monotonic()<deadline:
  if process.poll() is not None:
   print(json.dumps({'error':'runtime'})); break
  response=request(['--proxy','socks5h://127.0.0.1:'+str(socks_port),'https://api64.ipify.org'])
  try:
   observed=str(ipaddress.ip_address(response.stdout.strip())) if response.returncode==0 else ''
  except ValueError:
   observed=''
  if observed:
   if expected and ipaddress.ip_address(expected)!=ipaddress.ip_address(observed):
    print(json.dumps({'error':'exit_ip'})); break
   print(json.dumps({'observed_exit_ip':observed,'site_verified':site=='yes'})); break
  time.sleep(2)
 else:
  print(json.dumps({'error':'connection'}))
except (OSError,subprocess.SubprocessError):
 print(json.dumps({'error':'connection'}))
finally:
 if process is not None and process.poll() is None:
  process.terminate()
  try:
   process.wait(timeout=10)
  except subprocess.TimeoutExpired:
   process.kill(); process.wait(timeout=5)
"""

_CLEANUP_SCRIPT = """import json,os,pathlib,subprocess,sys,tempfile
root=pathlib.Path(sys.argv[1]); folder=pathlib.Path(sys.argv[2]); name,owner=sys.argv[3:]
if folder.parent!=root or not folder.name.startswith('.cascade-probe-') or folder.is_symlink(): raise SystemExit(31)
result=subprocess.run(['docker','inspect',name],capture_output=True,text=True,timeout=15)
if result.returncode==0:
 data=json.loads(result.stdout)[0]
 if (data.get('Config',{}).get('Labels') or {}).get('io.solobot.remnawave.owner')!=owner: raise SystemExit(32)
 subprocess.run(['docker','rm','--force',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=20)
else:
 subprocess.run(['docker','info','--format','{{.ID}}'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=15)
paths=[folder/'config.json',folder/'runtime.json']
if any(path.is_symlink() for path in paths): raise SystemExit(33)
manifest=root/'.managed-files.json'
managed=json.loads(manifest.read_text()) if manifest.is_file() else {}
for path in paths:
 path.unlink(missing_ok=True); managed.pop(str(path),None)
if folder.exists(): folder.rmdir()
if manifest.is_file():
 descriptor,temporary=tempfile.mkstemp(prefix='.solo-probe-',dir=str(root))
 try:
  os.fchmod(descriptor,0o600)
  with os.fdopen(descriptor,'w') as output:
   json.dump(managed,output); output.flush(); os.fsync(output.fileno())
  os.replace(temporary,manifest)
 finally:
  if os.path.exists(temporary): os.unlink(temporary)
"""


def _client_config(entry_address, entry_port, server_name, public_key, short_id, probe_uuid) -> dict:
    """Собирает изолированный клиент только со служебным доступом."""
    return {
        "log": {"loglevel": "none"},
        "inbounds": [{"listen": "127.0.0.1", "port": 0, "protocol": "socks", "settings": {"udp": False}}],
        "outbounds": [
            {
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": entry_address,
                            "port": entry_port,
                            "users": [{"id": probe_uuid, "encryption": "none", "flow": "xtls-rprx-vision"}],
                        }
                    ]
                },
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "serverName": server_name,
                        "publicKey": public_key,
                        "shortId": short_id,
                        "fingerprint": "firefox",
                    },
                },
            }
        ],
    }


def _command(script: str, *arguments: str) -> str:
    """Кодирует аргументы служебной команды без интерполяции shell."""
    return "python3 -c " + shlex.quote(script) + " " + shlex.join(arguments)


async def check_cascade_client(
    remote,
    *,
    entry_address: str,
    entry_port: int,
    server_name: str,
    public_key: str,
    short_id: str,
    probe_uuid: str,
    expected_exit_ip: str = "",
    check_site: bool = False,
) -> dict:
    """Проверяет полную цепочку служебным клиентом и удаляет его файлы."""
    try:
        if not all(
            isinstance(value, str)
            for value in (entry_address, server_name, public_key, short_id, probe_uuid, expected_exit_ip)
        ):
            raise ValueError
        address = ipaddress.ip_address(entry_address)
        if (
            address.is_unspecified
            or address.is_loopback
            or address.is_multicast
            or address.is_link_local
            or getattr(address, "scope_id", None)
        ):
            raise ValueError
        if type(entry_port) is not int or not 1 <= entry_port <= 65535 or type(check_site) is not bool:
            raise ValueError
        server_name = _domain(server_name)
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", public_key):
            raise ValueError
        if len(base64.urlsafe_b64decode(public_key + "=")) != 32:
            raise ValueError
        if not re.fullmatch(r"(?:[a-fA-F0-9]{2}){1,8}", short_id):
            raise ValueError
        probe_uuid = str(uuid.UUID(probe_uuid))
        expected_exit_ip = str(ipaddress.ip_address(expected_exit_ip)) if expected_exit_ip else ""
    except (TypeError, ValueError, AttributeError):
        raise InstallError(
            "cascade_verify", "Некорректные параметры сквозной проверки каскада", retryable=False
        ) from None
    token = secrets.token_hex(12)
    folder = ROOT + "/.cascade-probe-" + token
    container = "solobot-chain-probe-" + token
    owner = owner_id(remote.spec)
    configuration = _client_config(str(address), entry_port, server_name, public_key, short_id, probe_uuid)
    cleanup = _command(_CLEANUP_SCRIPT, ROOT, folder, container, owner)
    probe = _command(
        _PROBE_SCRIPT,
        folder,
        container,
        NODE_IMAGE,
        str(address),
        str(entry_port),
        server_name,
        expected_exit_ip,
        "yes" if check_site else "no",
        owner,
    )
    command = (
        "set -eu\ncleanup() { " + cleanup + "; }\ntrap cleanup EXIT\n"
        "trap 'exit 143' TERM\ntrap 'exit 130' INT HUP\n" + probe
    )
    try:
        await remote.write(folder + "/config.json", json.dumps(configuration), mode=0o600)
        raw = await remote.run(command, step="cascade_verify", timeout=100, secret=True)
        try:
            result = json.loads(raw)
            failure = result.get("error")
            if failure:
                messages = {
                    "site": "Сайт Selfsteal не подтвердил HTTPS через вход HAProxy; проверьте сертификат и SNI выхода",
                    "runtime": "Не удалось запустить временный Xray-клиент сквозной проверки",
                    "exit_ip": "Каскад отвечает через другой выходной IP, отличный от заданного",
                    "baseline": "Выходная нода не подтвердила свой внешний IPv4 для сквозной проверки каскада",
                    "connection": "Сквозная проверка VLESS → каскад → HTTPS не прошла за отведённое время",
                }
                raise InstallError("cascade_verify", messages.get(failure, "Сквозная проверка каскада не завершилась"))
            observed = str(ipaddress.ip_address(result["observed_exit_ip"]))
            if expected_exit_ip and ipaddress.ip_address(observed) != ipaddress.ip_address(expected_exit_ip):
                raise InstallError("cascade_verify", "Каскад отвечает через другой выходной IP, отличный от заданного")
            if check_site and result.get("site_verified") is not True:
                raise ValueError
        except InstallError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError):
            raise InstallError("cascade_verify", "Сервер не вернул корректный результат сквозной проверки") from None
        return {"observed_exit_ip": observed, "site_verified": check_site}
    finally:
        task = asyncio.create_task(remote.run(cleanup, step="cascade_verify", timeout=60, secret=True))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise
        except Exception:
            raise InstallError(
                "cascade_verify", "Не удалось удалить временные ресурсы сквозной проверки каскада"
            ) from None

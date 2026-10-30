import json
import shlex

from .remote import ROOT, RemoteServer, _ufw_rules, owner_id
from .types import InstallError, InstallSpec


_CLEANUP_SCRIPT = r"""import hashlib,ipaddress,json,os,pathlib,re,shlex,shutil,subprocess,sys,tempfile
root=pathlib.Path(sys.argv[1]); owner=sys.argv[2]; plan=json.loads(sys.argv[3])
plan_hash=hashlib.sha256(json.dumps(plan,sort_keys=True).encode()).hexdigest()
garbage=root.with_name(root.name+'.removing-'+owner)
receipt_path=root.with_name('.'+root.name+'.removing-'+owner+'.json')
units=("solobot-remnawave-cert.timer","solobot-remnawave-cert.service","solobot-shaper.service","solobot-psiphon.service","solobot-antitorrent.service","solobot-shaper-nft.service","solobot-antitorrent-nft.service","solobot-remnawave_mgmt-nft.service","solobot-remnawave-exit-ip.service")
unit_paths={name:pathlib.Path('/etc/systemd/system')/name for name in units}
extra_paths=(pathlib.Path('/etc/sysctl.d/90-solobot-remnawave-bbr.conf'),pathlib.Path('/etc/sysctl.d/90-solobot-conntrack.conf'),pathlib.Path('/etc/fail2ban/jail.d/solobot-remnawave.conf'))
tables={'solobot_shaper':'shaper','solobot_antitorrent':'antitorrent','solobot_remnawave_mgmt':'remnawave_mgmt'}
def fail(message):
 print(json.dumps({'error':message},ensure_ascii=False)); raise SystemExit(0)
def command(args,check=False):
 try: result=subprocess.run(args,capture_output=True,text=True)
 except FileNotFoundError: result=subprocess.CompletedProcess(args,127,'','')
 if result.returncode==127 and args[0]=='systemctl': fail('Команда systemctl недоступна; состояние служб не проверено')
 if check and result.returncode: raise RuntimeError('cleanup command failed')
 return result
def safe(path):
 return not any(part.is_symlink() for part in (path,*path.parents))
def digest(path):
 return hashlib.sha256(path.read_bytes()).hexdigest()
def canonical_rule(tokens):
 result=list(tokens)
 for keyword in ('from','to'):
  if keyword in result:
   index=result.index(keyword)+1
   if index<len(result) and result[index]!='any':
    try: result[index]=str(ipaddress.ip_network(result[index],strict=False))
    except ValueError: pass
 return result
def prove_ufw_absent():
 configuration=pathlib.Path('/etc/ufw')
 if configuration.exists() or configuration.is_symlink() or plan.get('ufw_previous_added') is not None or plan.get('ufw_was_active') is not None or plan.get('ufw_installed'):
  fail('Команда UFW недоступна, но сохранились сведения о его установке; состояние правил не проверено')
 nft=command(['nft','-j','list','ruleset'])
 if nft.returncode: fail('Команда UFW недоступна; не удалось проверить правила nftables')
 data=json.loads(nft.stdout)
 if not isinstance(data,dict) or not isinstance(data.get('nftables'),list) or any(not isinstance(item,dict) for item in data['nftables']): fail('Некорректный список правил nftables')
 if re.search('ufw|solobot-remnawave',json.dumps(data).lower()): fail('Команда UFW недоступна, но в ядре остались его правила')
 for proc,binary in (('/proc/net/ip_tables_names','iptables-legacy-save'),('/proc/net/ip6_tables_names','ip6tables-legacy-save')):
  path=pathlib.Path(proc)
  names=path.read_text().split() if path.exists() else []
  if any(not re.fullmatch('[A-Za-z0-9_]+',name) for name in names): fail('Некорректный список таблиц iptables')
  if not names: continue
  saved=command([binary])
  if saved.returncode: fail('Команда UFW недоступна; не удалось проверить прежние правила iptables')
  listed={line[1:].strip() for line in saved.stdout.splitlines() if line.startswith('*')}
  if not set(names).issubset(listed): fail('Получен неполный список правил iptables')
  if re.search('ufw|solobot-remnawave',saved.stdout.lower()): fail('Команда UFW недоступна, но в ядре остались его правила iptables')
def read_ufw_rules():
 result=command(['ufw','show','added'])
 if result.returncode==127:
  prove_ufw_absent(); return []
 if result.returncode not in (0,127): fail('Не удалось проверить правила UFW')
 parsed=[]
 for line in result.stdout.splitlines():
  try: tokens=shlex.split(line)
  except ValueError: fail('Некорректные правила UFW')
  if len(tokens)<3 or tokens[0]!='ufw' or tokens[1] not in ('allow','deny','reject','limit','route'): continue
  ours=False
  if 'comment' in tokens:
   index=tokens.index('comment'); ours=tokens[index+1:]==['solobot-remnawave']; tokens=tokens[:index]
  parsed.append((tokens[1:],ours))
 return parsed
def table_present(name):
 result=command(['nft','-j','list','tables'])
 if result.returncode: fail('Команда nft недоступна или не может прочитать таблицы; состояние правил не проверено')
 data=json.loads(result.stdout)
 if not isinstance(data,dict) or not isinstance(data.get('nftables'),list): fail('Некорректный список таблиц nftables')
 found=[]
 for item in data['nftables']:
  if not isinstance(item,dict): fail('Некорректный список таблиц nftables')
  if 'table' not in item: continue
  table=item['table']
  if not isinstance(table,dict) or not isinstance(table.get('family'),str) or not isinstance(table.get('name'),str): fail('Некорректный список таблиц nftables')
  found.append((table['family'],table['name']))
 return ('inet',name) in found
def address_inventory():
 result=command(['ip','-j','address','show'])
 if result.returncode: fail('Команда ip недоступна или не может прочитать адреса; выходной адрес не проверен')
 data=json.loads(result.stdout)
 if not isinstance(data,list) or any(not isinstance(link,dict) or not isinstance(link.get('ifname'),str) or not isinstance(link.get('addr_info'),list) or any(not isinstance(value,dict) for value in link['addr_info']) for link in data): fail('Некорректный список сетевых адресов')
 return data
def keep_ufw_active(rules):
 previous=plan.get('ufw_previous_added')
 if plan.get('ufw_was_active') is not False or previous is None: return True
 original={tuple(canonical_rule(rule)) for rule in previous}
 remaining={tuple(canonical_rule(rule)) for rule,ours in rules if not ours or tuple(canonical_rule(rule)) in original}
 return remaining!=original
if not re.fullmatch('[a-f0-9]{64}',owner): fail('Некорректная метка владельца установки')
if not safe(garbage) or not safe(receipt_path): fail('Служебный путь удаления является символической ссылкой')
receipt=json.loads(receipt_path.read_text()) if receipt_path.exists() else None
if receipt is not None and (not isinstance(receipt,dict) or receipt.get('owner')!=owner or receipt.get('root')!=str(root) or receipt.get('plan_hash')!=plan_hash):
 fail('Некорректное подтверждение удаления каталога')
def remove_garbage():
 if garbage.exists():
  if not receipt or not garbage.is_dir() or garbage.stat().st_ino!=receipt.get('inode') or garbage.stat().st_dev!=receipt.get('device'):
   fail('Принадлежность временного каталога удаления не подтверждена')
  shutil.rmtree(garbage)
 if receipt_path.exists(): receipt_path.unlink()
def inspect_containers():
 available=command(['docker','version','--format','{{.Server.Version}}'])
 if available.returncode==127: fail('Команда Docker недоступна; состояние контейнеров не проверено')
 if available.returncode: fail('Docker недоступен; невозможно проверить принадлежность контейнеров')
 listed=command(['docker','ps','-aq'],check=True).stdout.split()
 if not listed: return []
 data=json.loads(command(['docker','inspect',*listed],check=True).stdout)
 if not isinstance(data,list) or len(data)!=len(listed): fail('Docker вернул неполный список контейнеров')
 return data
containers=inspect_containers()
owned_containers=[]
for item in containers:
 identifier=item.get('Id'); name=str(item.get('Name','')).lstrip('/')
 labels=item.get('Config',{}).get('Labels') or {}
 ours=labels.get('io.solobot.remnawave.owner')==owner
 if name in ('remnanode','cdn-nginx'):
  if not ours: fail('Контейнер '+name+' принадлежит другой установке')
  if not isinstance(identifier,str) or not re.fullmatch('[a-f0-9]{64}',identifier): fail('Некорректный идентификатор контейнера')
  owned_containers.append(identifier)
 for mount in item.get('Mounts',[]):
  source=mount.get('Source')
  if isinstance(source,str) and (source==str(garbage) or source.startswith(str(garbage)+'/')): fail('Временный каталог удаления используется контейнером')
  if isinstance(source,str) and (source==str(root) or source.startswith(str(root)+'/')) and not (ours and name in ('remnanode','cdn-nginx')):
   fail('Каталог установщика используется посторонним контейнером')
if not safe(root): fail('Каталог установщика или его родитель является символической ссылкой')
mounts=pathlib.Path('/proc/self/mountinfo')
if mounts.is_file():
 for line in mounts.read_text().splitlines():
  fields=line.split()
  if len(fields)>4:
   mount=re.sub(r'\\([0-7]{3})',lambda match:chr(int(match[1],8)),fields[4])
   if any(mount==str(path) or mount.startswith(str(path)+'/') for path in (root,garbage)): fail('В каталоге установщика есть отдельная файловая система')
if not root.exists():
 if owned_containers or any(path.exists() or path.is_symlink() for path in (*unit_paths.values(),*extra_paths)):
  fail('Каталог установщика отсутствует, но на сервере остались ресурсы без подтверждённой принадлежности')
 for name in units:
  if command(['systemctl','is-active','--quiet',name]).returncode==0: fail('Каталог установщика отсутствует, но его служба ещё работает')
 for table in tables:
  if table_present(table): fail('Каталог установщика отсутствует, но остались правила nftables')
 ufw_current=read_ufw_rules(); preserve_ufw=keep_ufw_active(ufw_current)
 for tokens,ours in ufw_current:
  if ours and canonical_rule(tokens) not in [canonical_rule(rule) for rule in (plan.get('ufw_previous_added') or [])] and not (preserve_ufw and canonical_rule(tokens) in [canonical_rule(rule) for rule in plan.get('ssh_rules',[])]):
   fail('Каталог установщика отсутствует, но остались его правила UFW')
 if plan.get('warp_owned') and plan.get('warp_preexisting') is False and command(['systemctl','is-active','--quiet','warp-svc']).returncode==0:
  fail('Каталог установщика отсутствует, но его WARP ещё работает')
 if plan.get('warp_owned') and plan.get('warp_preexisting') is None: fail('Не сохранено исходное состояние WARP')
 for alias in plan.get('aliases',[]):
  if any(value.get('local')==alias['address'] for link in address_inventory() if link['ifname']==alias['interface'] for value in link['addr_info']):
   fail('Каталог установщика отсутствует, но остался его выходной адрес')
 remove_garbage()
 warnings=list((receipt or {}).get('warnings') or [])
 if plan.get('warp_owned') and plan.get('warp_preexisting') is True: warnings.append('Сохранён ранее установленный WARP')
 if preserve_ufw and any(ours for _rule,ours in ufw_current): warnings.append('Сохранены исходные правила UFW или доступ по SSH')
 if preserve_ufw and plan.get('ufw_was_active') is False: warnings.append('UFW сохранён: сторонние правила изменились после установки')
 print(json.dumps({'completed':True,'warnings':warnings},ensure_ascii=False)); raise SystemExit(0)
if garbage.exists(): fail('Одновременно существуют рабочий и временный каталоги удаления')
marker=root/'.owner'; manifest_path=root/'.managed-files.json'; state_path=root/'.cleanup-state.json'
if not safe(marker) or not marker.is_file() or marker.read_text().strip()!=owner:
 fail('Каталог ноды больше не принадлежит этой установке')
if not safe(manifest_path) or (manifest_path.exists() and not manifest_path.is_file()): fail('Некорректный манифест установщика')
managed=json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
if not isinstance(managed,dict): fail('Некорректный манифест установщика')
for path,value in managed.items():
 if not isinstance(path,str) or not isinstance(value,str) or not re.fullmatch('[a-f0-9]{64}',value): fail('Некорректный манифест установщика')
if not safe(state_path): fail('Состояние удаления является символической ссылкой')
state=json.loads(state_path.read_text()) if state_path.exists() else {'owner':owner,'done':[]}
if not isinstance(state,dict) or state.get('owner')!=owner or not isinstance(state.get('done'),list): fail('Некорректное состояние удаления')
if state.get('plan_hash',plan_hash)!=plan_hash: fail('План удаления изменился после начала очистки')
state['plan_hash']=plan_hash
warnings=list(state.get('warnings') or [])
def owned(path):
 return safe(path) and path.is_file() and managed.get(str(path))==digest(path)
def validate(path):
 if (path.exists() or path.is_symlink()) and not owned(path): fail('Файл изменён вне установщика: '+str(path))
for path in (*unit_paths.values(),*extra_paths): validate(path)
for name,path in unit_paths.items():
 info=command(['systemctl','show',name,'--property=FragmentPath','--property=DropInPaths'])
 fields=dict(line.split('=',1) for line in info.stdout.splitlines() if '=' in line)
 fragment=fields.get('FragmentPath','')
 if fields.get('DropInPaths') or (fragment and fragment!=str(path)):
  fail('Системная служба изменена вне установщика: '+name)
 if fragment and not path.exists() and str(path) not in state.get('validated',[]):
  fail('У службы отсутствует подтверждённый файл: '+name)
for table,name in tables.items():
 if table_present(table):
  evidence=(root/'addons'/('apply-'+name+'.py'),root/'addons'/(name+'.nft'),unit_paths['solobot-'+name+'-nft.service'])
  if not any(owned(path) or str(path) in state.get('validated',[]) for path in evidence):
   fail('Принадлежность таблицы nftables не подтверждена: '+table)
aliases=plan.get('aliases',[])
for alias in aliases:
 address=ipaddress.ip_address(alias['address']); interface=alias['interface']; prefix=32 if address.version==4 else 128
 if not isinstance(interface,str) or not re.fullmatch('[A-Za-z0-9_.:-]{1,32}',interface): fail('Некорректный интерфейс выходного адреса')
 if str(address)==plan.get('address'): fail('Выходной адрес совпадает с адресом управления сервером')
 for link in address_inventory():
  if link['ifname']!=interface: continue
  for value in link.get('addr_info',[]):
   if value.get('local')==str(address) and value.get('prefixlen')!=prefix: fail('Сетевой префикс выходного адреса изменён вне установщика')
if plan.get('warp_owned'):
 if plan.get('warp_preexisting') is None: fail('Не сохранено исходное состояние WARP; нельзя безопасно отличить общий WARP от созданного установщиком')
 if plan['warp_preexisting']:
  warnings.append('Сохранён ранее установленный WARP')
 else:
  for path in ('/etc/apt/keyrings/solobot-warp.asc','/etc/apt/sources.list.d/solobot-warp.list'):
   if not owned(pathlib.Path(path)): fail('Принадлежность WARP не подтверждена')
ufw_rules=[]
previous=plan.get('ufw_previous_added')
if previous is not None and (not isinstance(previous,list) or any(not isinstance(tokens,list) or not tokens or any(not isinstance(token,str) for token in tokens) for tokens in previous)):
 fail('Некорректный снимок исходных правил UFW')
ufw_current=read_ufw_rules(); preserve_ufw=keep_ufw_active(ufw_current)
if preserve_ufw and plan.get('ufw_was_active') is False: warnings.append('UFW сохранён: сторонние правила изменились после установки')
for tokens,ours in ufw_current:
 if not ours: continue
 normalized=canonical_rule(tokens)
 if preserve_ufw and normalized in [canonical_rule(rule) for rule in plan.get('ssh_rules',[])]:
  warnings.append('Сохранено правило UFW для доступа по SSH'); continue
 if previous is not None:
  if normalized not in [canonical_rule(rule) for rule in previous]: ufw_rules.append(tokens)
 elif normalized in [canonical_rule(rule) for rule in plan.get('ufw_expected',[])]: ufw_rules.append(tokens)
 else: fail('У правила UFW отсутствует снимок исходного состояния: '+shlex.join(tokens))
sysctl_targets=((extra_paths[0],plan.get('bbr_previous',{}),{'net.core.default_qdisc':'fq','net.ipv4.tcp_congestion_control':'bbr'}),(extra_paths[1],plan.get('conntrack_previous',{}),{'net.netfilter.nf_conntrack_acct':'1'}))
for path,values,expected in sysctl_targets:
 if path.exists() and (not values or set(values)!=set(expected)): fail('Не сохранены исходные настройки sysctl: '+str(path))
 for key,value in values.items():
  if key not in expected or not isinstance(value,str) or not re.fullmatch('[A-Za-z0-9_-]+',value): fail('Некорректный снимок sysctl')
def store():
 state['warnings']=list(dict.fromkeys(warnings))
 fd,tmp=tempfile.mkstemp(prefix='.cleanup-',dir=str(root)); os.fchmod(fd,0o600)
 with os.fdopen(fd,'w') as output:
  json.dump(state,output); output.flush(); os.fsync(output.fileno())
 os.replace(tmp,state_path)
def step(name,action):
 if name in state['done']: return
 action(); state['done'].append(name); store()
state.setdefault('validated',[str(path) for path in (*unit_paths.values(),*extra_paths) if owned(path)])
store()
def stop_unit(name,path):
 if not path.exists() and str(path) not in state['validated']: return
 validate(path)
 command(['systemctl','disable','--now',name])
 active=command(['systemctl','is-active',name]).stdout.strip()
 if active in ('active','reloading','activating','deactivating'): raise RuntimeError('service still active')
 if path.exists(): path.unlink()
 command(['systemctl','daemon-reload'],check=True)
for name,path in unit_paths.items(): step('unit:'+name,lambda name=name,path=path:stop_unit(name,path))
def remove_table(table):
 if table_present(table): command(['nft','delete','table','inet',table],check=True)
for table in tables: step('nft:'+table,lambda table=table:remove_table(table))
def remove_container(identifier):
 inspected=command(['docker','inspect',identifier])
 if inspected.returncode:
  command(['docker','info'],check=True); return
 data=json.loads(inspected.stdout)[0]
 if (data.get('Config',{}).get('Labels') or {}).get('io.solobot.remnawave.owner')!=owner: fail('Принадлежность контейнера изменилась')
 command(['docker','rm','-f',identifier],check=True)
for identifier in owned_containers: step('container:'+identifier,lambda identifier=identifier:remove_container(identifier))
def remove_alias(alias):
 address=ipaddress.ip_address(alias['address']); interface=alias['interface']; prefix=32 if address.version==4 else 128
 for link in address_inventory():
  if link['ifname']!=interface: continue
  for value in link['addr_info']:
   if value.get('local')==str(address):
    if value.get('prefixlen')!=prefix: fail('Сетевой префикс выходного адреса изменился перед удалением')
    command(['ip','address','del',str(address)+'/'+str(prefix),'dev',interface],check=True)
for alias in aliases: step('alias:'+alias['address']+':'+alias['interface'],lambda alias=alias:remove_alias(alias))
def restore_sysctl(path,values,expected):
 if path.exists():
  validate(path)
  for key,value in values.items():
   if command(['sysctl','-n',key],check=True).stdout.strip()==expected[key]: command(['sysctl','-w',key+'='+value],check=True)
  path.unlink()
for path,values,expected in sysctl_targets:
 step('sysctl:'+str(path),lambda path=path,values=values,expected=expected:restore_sysctl(path,values,expected))
def remove_jail():
 path=extra_paths[2]
 if not path.exists() and str(path) not in state['validated']: return
 if path.exists(): validate(path); path.unlink()
 if command(['systemctl','is-active','--quiet','fail2ban']).returncode==0: command(['fail2ban-client','reload'],check=True)
step('fail2ban',remove_jail)
def remove_warp():
 if not plan.get('warp_owned') or plan.get('warp_preexisting'): return
 command(['warp-cli','--accept-tos','disconnect'],check=True)
 command(['systemctl','disable','--now','warp-svc'],check=True)
 if command(['systemctl','is-active','--quiet','warp-svc']).returncode==0: raise RuntimeError('WARP still active')
step('warp',remove_warp)
def remove_ufw():
 keep_active=keep_ufw_active(read_ufw_rules())
 ssh_rules=[canonical_rule(rule) for rule in plan.get('ssh_rules',[])]
 delayed_ssh=[]
 for tokens in ufw_rules:
  if canonical_rule(tokens) in ssh_rules: delayed_ssh.append(tokens)
  else: command(['ufw','--force','delete',*tokens],check=True)
 keep_active=keep_active or keep_ufw_active(read_ufw_rules())
 if keep_active:
  if plan.get('ufw_was_active') is False: warnings.append('UFW сохранён: сторонние правила изменились после установки')
  if delayed_ssh: warnings.append('Сохранено правило UFW для доступа по SSH')
 elif plan.get('ufw_was_active') is False:
  command(['ufw','--force','disable'],check=True)
  for tokens in delayed_ssh: command(['ufw','--force','delete',*tokens],check=True)
step('ufw',remove_ufw)
remaining=inspect_containers()
if any((item.get('Config',{}).get('Labels') or {}).get('io.solobot.remnawave.owner')==owner and str(item.get('Name','')).lstrip('/') in ('remnanode','cdn-nginx') for item in remaining): fail('Контейнер ноды ещё существует')
for item in remaining:
 for mount in item.get('Mounts',[]):
  source=mount.get('Source')
  if isinstance(source,str) and (source==str(root) or source.startswith(str(root)+'/')): fail('Каталог установщика используется контейнером')
for table in tables:
 if table_present(table): fail('Таблица nftables ещё существует: '+table)
for alias in aliases:
 if any(value.get('local')==alias['address'] for link in address_inventory() if link['ifname']==alias['interface'] for value in link['addr_info']): fail('Выходной адрес ещё существует')
for path in (*unit_paths.values(),*extra_paths):
 if path.exists() or path.is_symlink(): fail('Файл установщика ещё существует: '+str(path))
if not safe(root) or not marker.is_file() or marker.read_text().strip()!=owner: fail('Принадлежность каталога изменилась во время удаления')
metadata=root.stat()
if receipt is not None and (receipt.get('inode')!=metadata.st_ino or receipt.get('device')!=metadata.st_dev): fail('Каталог установки был заменён после начала удаления')
receipt={'owner':owner,'root':str(root),'plan_hash':plan_hash,'inode':metadata.st_ino,'device':metadata.st_dev,'warnings':list(dict.fromkeys(warnings))}
fd,tmp=tempfile.mkstemp(prefix='.cleanup-',dir=str(root.parent)); os.fchmod(fd,0o600)
with os.fdopen(fd,'w') as output:
 json.dump(receipt,output); output.flush(); os.fsync(output.fileno())
os.replace(tmp,receipt_path)
os.rename(root,garbage)
remove_garbage()
print(json.dumps({'completed':True,'warnings':list(dict.fromkeys(warnings))},ensure_ascii=False))
"""


def _plan(spec: InstallSpec, artifacts: dict) -> dict:
    """Выбирает подтверждённые данные для очистки сервера."""
    aliases = []
    for value in (artifacts.get("exit_alias"), (artifacts.get("exit_ip_change") or {}).get("previous")):
        if isinstance(value, dict) and value.get("owned"):
            alias = {key: value.get(key) for key in ("address", "interface")}
            if alias not in aliases:
                aliases.append(alias)
    expected = []
    edit = artifacts.get("edit") or {}
    params = [spec.to_dict(), edit.get("before"), edit.get("after"), edit.get("previous_target")]
    params.extend((edit.get("applied_specs") or {}).values())
    for value in params:
        if not isinstance(value, dict):
            continue
        candidate = InstallSpec.from_dict(value, complete=False)
        if candidate.address != spec.address:
            continue
        expected.extend([list(rule) for rule in sorted(_ufw_rules(candidate))])
    previous = artifacts.get("conntrack_accounting_previous")
    return {
        "address": spec.address,
        "aliases": aliases,
        "bbr_previous": artifacts.get("bbr_previous") or {},
        "conntrack_previous": {"net.netfilter.nf_conntrack_acct": previous} if previous in {"0", "1"} else {},
        "warp_owned": artifacts.get("warp_owned") is True,
        "warp_preexisting": artifacts.get("warp_preexisting"),
        "ufw_previous_added": artifacts.get("ufw_previous_added"),
        "ufw_was_active": artifacts.get("ufw_was_active"),
        "ufw_installed": "ufw" in (artifacts.get("installed_addons") or []),
        "ufw_expected": expected,
        "ssh_rules": [
            ["allow", "from", source, "to", "any", "port", str(spec.ssh_port), "proto", "tcp"]
            for source in spec.trusted_ssh_sources
        ]
        if spec.trusted_ssh_sources
        else [["allow", f"{spec.ssh_port}/tcp"]],
    }


async def cleanup_remote(remote: RemoteServer, spec: InstallSpec, artifacts: dict, checkpoint=None, log=None) -> None:
    """Удаляет ресурсы сервера с проверкой владельца и повторяемыми этапами."""
    state = artifacts.setdefault("remote_cleanup", {})
    if state.get("completed"):
        return
    state["started"] = True
    if checkpoint:
        await checkpoint(artifacts)
    if log:
        await log("Удаление принадлежащих установке ресурсов сервера")
    payload = json.dumps(_plan(spec, artifacts), separators=(",", ":"))
    result = await remote.run(
        f"python3 -c {shlex.quote(_CLEANUP_SCRIPT)} {shlex.quote(ROOT)} {owner_id(spec)} {shlex.quote(payload)}",
        step="remote_cleanup",
        timeout=600,
    )
    try:
        response = json.loads(result)
    except (ValueError, TypeError):
        raise InstallError("remote_cleanup", "Сервер не подтвердил удаление; запись сохранена") from None
    if isinstance(response, dict) and isinstance(response.get("error"), str):
        raise InstallError("remote_cleanup", response["error"] + "; запись сохранена", retryable=False)
    if not isinstance(response, dict) or response.get("completed") is not True:
        raise InstallError("remote_cleanup", "Очистка сервера не завершена; запись сохранена")
    state.update(completed=True, warnings=response.get("warnings") or [])
    if checkpoint:
        await checkpoint(artifacts)
    if log:
        for warning in state["warnings"]:
            await log(warning)

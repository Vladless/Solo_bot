import ipaddress
import re
import shlex

from urllib.parse import urlencode

import httpx

from .types import InstallError, InstallSpec, InstallWaitingError, needs_certificate


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

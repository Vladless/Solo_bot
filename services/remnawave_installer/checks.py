import ipaddress

from urllib.parse import urlencode

import httpx

from .types import InstallSpec, InstallWaitingError


async def check_origin_dns(spec: InstallSpec) -> None:
    """Проверяет адрес origin до создания ресурсов панели и облака."""
    if spec.transport not in {"vless_tcp_selfsteal", "cdn_vk", "cdn_yandex"}:
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
        "Origin пока не указывает на выбранный сервер. Обновите DNS и нажмите «Проверить»",
        records=[{"type": family, "name": spec.domain, "value": spec.address}],
    )

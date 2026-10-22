import asyncio
import copy
import hashlib
import json
import re
import time
import uuid

from collections.abc import Awaitable, Callable
from urllib.parse import quote

import httpx
import jwt

from .types import InstallError, InstallSpec, InstallWaitingError


VK_API = "https://msk.cloud.vk.com/api/cdn/api/v1"
YANDEX_CDN = "https://cdn.api.cloud.yandex.net/cdn/v1"
YANDEX_CERTIFICATES = "https://certificate-manager.api.cloud.yandex.net/certificate-manager/v1"
YANDEX_IAM = "https://iam.api.cloud.yandex.net/iam/v1/tokens"
YANDEX_RESOURCES = "https://resource-manager.api.cloud.yandex.net/resource-manager/v1"
YANDEX_OPERATIONS = "https://operation.api.cloud.yandex.net/operations"
DOH = ("https://dns.google/resolve", "https://cloudflare-dns.com/dns-query")
Checkpoint = Callable[[dict], Awaitable[None]]


def _http_client() -> httpx.AsyncClient:
    """Создаёт клиент без общих заголовков авторизации."""
    return httpx.AsyncClient(timeout=30, follow_redirects=False)


def _identifier(value) -> str:
    """Проверяет идентификатор облачного ресурса."""
    if (
        isinstance(value, bool)
        or not isinstance(value, str | int)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", str(value))
    ):
        raise InstallError("cloud", "Облако не вернуло корректный идентификатор ресурса")
    return str(value)


def _dns_name(value: str) -> str:
    """Нормализует имя записи DNS."""
    return value.lower().rstrip(".")


async def _cname_visible(client: httpx.AsyncClient, record: dict) -> bool:
    """Сверяет конкретную CNAME-запись через публичные DNS."""

    async def ask(endpoint: str) -> bool:
        try:
            result = await client.get(
                endpoint,
                params={"name": record["name"], "type": "CNAME"},
                headers={"Accept": "application/dns-json"},
                timeout=10,
            )
            payload = result.json() if result.status_code == 200 else {}
            return any(
                isinstance(item, dict)
                and item.get("type") == 5
                and _dns_name(str(item.get("name") or "")) == _dns_name(record["name"])
                and _dns_name(str(item.get("data") or "")) == _dns_name(record["value"])
                for item in payload.get("Answer", [])
            )
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            return False

    return any(await asyncio.gather(*(ask(endpoint) for endpoint in DOH)))


async def _probe_cdn(client: httpx.AsyncClient, spec: InstallSpec, path: str) -> int:
    """Проверяет HTTPS-цепочку CDN до транспорта."""
    try:
        response = await client.get(
            f"https://{spec.cdn_domain}{path}",
            params={"_token": str(time.time_ns())} if spec.transport == "cdn_vk" else None,
            follow_redirects=spec.transport == "cdn_yandex",
            timeout=15,
        )
        return response.status_code
    except httpx.HTTPError:
        return 0


class _Cloud:
    def __init__(
        self,
        client: httpx.AsyncClient,
        spec: InstallSpec,
        secrets: dict,
        artifacts: dict,
        checkpoint: Checkpoint | None,
    ) -> None:
        self.client = client
        self.spec = spec
        self.secrets = secrets
        self.artifacts = artifacts
        self.state = artifacts.setdefault("cloud", {})
        self.checkpoint = checkpoint
        self.iam_token = ""

    async def save(self) -> None:
        """Сохраняет артефакты перед продолжением установки."""
        if self.checkpoint:
            await self.checkpoint(self.artifacts)

    async def request(
        self,
        method: str,
        url: str,
        *,
        body: dict | None = None,
        params: dict | None = None,
        headers: dict | None = None,
        auth: str = "",
    ):
        """Выполняет запрос без раскрытия токенов в ошибках."""
        request_headers = dict(headers or {})
        if auth == "vk":
            token = self.secrets.get("vk_token")
            if not isinstance(token, str) or not token.strip():
                raise InstallError("cloud_auth", "Укажите действующий токен VK Cloud")
            request_headers["X-Auth-Token"] = token.strip()
        if auth == "yandex":
            if not self.iam_token:
                await self.refresh_iam()
            request_headers["Authorization"] = f"Bearer {self.iam_token}"
        try:
            response = await self.client.request(method, url, json=body, params=params, headers=request_headers)
            if response.status_code == 401 and auth == "yandex":
                await self.refresh_iam()
                request_headers["Authorization"] = f"Bearer {self.iam_token}"
                response = await self.client.request(method, url, json=body, params=params, headers=request_headers)
        except (asyncio.CancelledError, InstallError):
            raise
        except httpx.HTTPError as error:
            raise InstallError("cloud", "Облако недоступно; созданные ресурсы сохранены для повтора") from error
        if response.status_code in {401, 403}:
            message = (
                "Обновите токен VK Cloud"
                if auth == "vk"
                else "Проверьте ключ сервисного аккаунта и его права editor на каталог"
            )
            raise InstallError("cloud_auth", message)
        if not 200 <= response.status_code < 300:
            raise InstallError("cloud", f"Облачный API отклонил запрос: HTTP {response.status_code}")
        if not response.content:
            return {}
        try:
            payload = response.json()
        except ValueError as error:
            raise InstallError("cloud", "Облако вернуло некорректный JSON") from error
        if not isinstance(payload, dict | list):
            raise InstallError("cloud", "Облако вернуло некорректный ответ")
        return payload

    async def refresh_iam(self) -> None:
        """Обменивает авторизованный ключ на временный IAM-токен."""
        value = self.secrets.get("yandex_key")
        try:
            key = json.loads(value) if isinstance(value, str) else value
            if not isinstance(key, dict) or not all(
                isinstance(key.get(name), str) and key[name] for name in ("id", "service_account_id", "private_key")
            ):
                raise ValueError
            issued = int(time.time())
            token = jwt.encode(
                {"iss": key["service_account_id"], "aud": YANDEX_IAM, "iat": issued, "exp": issued + 3600},
                key["private_key"],
                algorithm="PS256",
                headers={"kid": key["id"]},
            )
        except (ValueError, TypeError, jwt.PyJWTError) as error:
            raise InstallError(
                "cloud_auth", "Некорректный авторизованный JSON-ключ сервисного аккаунта Яндекса"
            ) from error
        result = await self.request("POST", YANDEX_IAM, body={"jwt": token})
        iam = result.get("iamToken") if isinstance(result, dict) else None
        if not isinstance(iam, str) or not iam:
            raise InstallError("cloud_auth", "Яндекс не вернул IAM-токен")
        self.iam_token = iam

    async def wait_dns(self, records: list[dict]) -> None:
        """Приостанавливает задачу до появления заданных записей."""
        self.state["dns_records"] = records
        missing = [record for record in records if not await _cname_visible(self.client, record)]
        await self.save()
        if missing:
            raise InstallWaitingError(
                "Пропишите CNAME-записи у регистратора и нажмите «Проверить»", records=records, retry_after=60
            )

    async def wait_delivery(self) -> dict:
        """Ожидает раскатки CDN с сохранением срока ожидания."""
        path = self.artifacts.get("xhttp_path")
        if not isinstance(path, str) or not re.fullmatch(r"/(?:[A-Za-z0-9_-]+/)+", path):
            raise InstallError("cloud", "В снимке отсутствует корректный путь XHTTP", retryable=False)
        self.state.setdefault("delivery_started_at", time.time())
        status = await _probe_cdn(self.client, self.spec, path)
        self.state["last_http_status"] = status
        self.state["ready"] = status in ({400} if self.spec.transport == "cdn_vk" else {200, 400})
        await self.save()
        if self.state["ready"]:
            self.state["dns_records"] = []
            await self.save()
            return self.state
        limit = 1800 if self.spec.transport == "cdn_vk" else 10800
        if time.time() - self.state["delivery_started_at"] >= limit:
            self.state.pop("delivery_started_at", None)
            await self.save()
            hint = "; проверьте порт origin в файрволе хостера" if status in {502, 504} else ""
            raise InstallError("cloud", f"CDN не завершил раскатку: HTTP {status}{hint}")
        raise InstallWaitingError(f"Ожидается раскатка CDN, последний ответ HTTP {status}", retry_after=30)

    async def list_yandex(self, url: str, key: str, params: dict) -> list[dict]:
        """Читает все страницы списка ресурсов Яндекса."""
        items = []
        page_token = ""
        seen = set()
        while True:
            payload = await self.request(
                "GET",
                url,
                params={**params, "pageSize": 100, **({"pageToken": page_token} if page_token else {})},
                auth="yandex",
            )
            page = payload.get(key, []) if isinstance(payload, dict) else None
            if not isinstance(page, list) or any(not isinstance(item, dict) for item in page):
                raise InstallError("cloud", "Некорректный список ресурсов Яндекса")
            items.extend(page)
            page_token = payload.get("nextPageToken")
            if not page_token:
                return items
            if page_token in seen:
                raise InstallError("cloud", "Яндекс повторил страницу списка ресурсов")
            seen.add(page_token)

    async def yandex_create(self, kind: str, url: str, body: dict, *, method: str = "POST") -> dict:
        """Создаёт ресурс с устойчивым ключом идемпотентности."""
        operations = self.state.setdefault("operations", {})
        requests = self.state.setdefault("requests", {})
        details = self.state.setdefault("request_bodies", {})
        if kind not in requests:
            requests[kind] = str(uuid.uuid4())
            details[kind] = {"method": method, "url": url, "body": copy.deepcopy(body)}
            await self.save()
        if kind not in operations:
            result = await self.request(
                method, url, body=body, auth="yandex", headers={"Idempotency-Key": requests[kind]}
            )
            if not isinstance(result, dict) or type(result.get("done")) is not bool:
                raise InstallError("cloud", "Яндекс не вернул операцию создания ресурса")
            operations[kind] = {"id": _identifier(result.get("id")), "started_at": time.time()}
            await self.save()
        else:
            result = await self.request(
                "GET", f"{YANDEX_OPERATIONS}/{quote(operations[kind]['id'], safe='')}", auth="yandex"
            )
        if result.get("error"):
            raise InstallError(
                "cloud", f"Операция Яндекса завершилась ошибкой: код {result['error'].get('code', 'неизвестен')}"
            )
        if result.get("done") is not True:
            if time.time() - operations[kind]["started_at"] > 300:
                raise InstallError("cloud", "Операция Яндекса не завершилась за пять минут; её ID сохранён")
            raise InstallWaitingError("Яндекс создаёт ресурс; операция сохранена", retry_after=10)
        resource = result.get("response")
        if not isinstance(resource, dict):
            raise InstallError("cloud", "Яндекс не вернул результат операции")
        self.state[kind + "_id"] = _identifier(resource.get("id"))
        await self.save()
        return resource

    async def configure_yandex(self) -> dict:
        """Создаёт CDN и сертификат Яндекса с двумя DNS-паузами."""
        await self.refresh_iam()
        folder_id = self.state.get("folder_id") or self.spec.cloud.get("folder_id")
        if not folder_id:
            clouds = await self.list_yandex(f"{YANDEX_RESOURCES}/clouds", "clouds", {})
            if not clouds:
                raise InstallError("cloud", "Сервисному аккаунту недоступны облака; укажите каталог")
            folders = await self.list_yandex(
                f"{YANDEX_RESOURCES}/folders", "folders", {"cloudId": _identifier(clouds[0].get("id"))}
            )
            if not folders:
                raise InstallError("cloud", "Сервисному аккаунту недоступны каталоги")
            folder_id = folders[0].get("id")
        folder_id = _identifier(folder_id)
        self.state["folder_id"] = folder_id
        await self.save()
        await self.request(
            "GET", f"{YANDEX_CDN}/resources", params={"folderId": folder_id, "pageSize": 1}, auth="yandex"
        )
        name = "solo-" + self.state["nonce"]
        group_name = self.state.get("origin_group_name", name)
        source = f"{self.spec.domain}:7443"
        if self.state.get("origin_group_id"):
            group = await self.request(
                "GET",
                f"{YANDEX_CDN}/originGroups/{_identifier(self.state['origin_group_id'])}",
                params={"folderId": folder_id},
                auth="yandex",
            )
        else:
            group = await self.yandex_create(
                "origin_group",
                f"{YANDEX_CDN}/originGroups",
                {
                    "folderId": folder_id,
                    "name": group_name,
                    "useNext": True,
                    "origins": [{"source": source, "enabled": True, "backup": False}],
                    "providerType": "ourcdn",
                },
            )
        if (
            group.get("folderId") != folder_id
            or group.get("name") != group_name
            or [item.get("source") for item in group.get("origins", [])] != [source]
        ):
            raise InstallError(
                "cloud", "Сохранённая группа источников изменена; чужие ресурсы не затронуты", retryable=False
            )
        if not self.state.get("certificate_id"):
            await self.yandex_create(
                "certificate",
                f"{YANDEX_CERTIFICATES}/certificates/requestNew",
                {
                    "folderId": folder_id,
                    "name": name,
                    "domains": [self.spec.cdn_domain],
                    "challengeType": "DNS",
                    "labels": {"solo-installer": self.state["nonce"]},
                },
            )
        certificate = await self.request(
            "GET",
            f"{YANDEX_CERTIFICATES}/certificates/{_identifier(self.state['certificate_id'])}",
            params={"view": "FULL"},
            auth="yandex",
        )
        if (
            certificate.get("folderId") != folder_id
            or certificate.get("name") != name
            or certificate.get("domains") != [self.spec.cdn_domain]
        ):
            raise InstallError("cloud", "Сохранённый сертификат не соответствует установке", retryable=False)
        if certificate.get("status") != "ISSUED":
            if certificate.get("status") in {"INVALID", "REVOKED", "FAILED"}:
                raise InstallError("cloud", "Яндекс отклонил выпуск сертификата; проверьте Certificate Manager")
            records = [
                {"type": "CNAME", "name": item["dnsChallenge"]["name"], "value": item["dnsChallenge"]["value"]}
                for item in certificate.get("challenges", [])
                if isinstance(item, dict)
                and isinstance(item.get("dnsChallenge"), dict)
                and item["dnsChallenge"].get("type") == "CNAME"
                and item["dnsChallenge"].get("name")
                and item["dnsChallenge"].get("value")
            ]
            if not records:
                raise InstallWaitingError("Яндекс готовит DNS-проверку сертификата", retry_after=10)
            await self.wait_dns(records)
            self.state.setdefault("certificate_wait_started_at", time.time())
            await self.save()
            if time.time() - self.state["certificate_wait_started_at"] > 10800:
                self.state.pop("certificate_wait_started_at", None)
                await self.save()
                raise InstallError("cloud", "Сертификат Яндекса не выпущен за три часа")
            raise InstallWaitingError(
                "DNS подтверждён; ожидается выпуск сертификата Яндекса", records=records, retry_after=60
            )
        if self.state.get("resource_id"):
            resource = await self.request(
                "GET", f"{YANDEX_CDN}/resources/{_identifier(self.state['resource_id'])}", auth="yandex"
            )
        else:
            resource = await self.yandex_create(
                "resource",
                f"{YANDEX_CDN}/resources",
                {
                    "folderId": folder_id,
                    "cname": self.spec.cdn_domain,
                    "origin": {"originGroupId": self.state["origin_group_id"]},
                    "secondaryHostnames": {"values": []},
                    "originProtocol": "HTTPS",
                    "active": True,
                    "providerType": "ourcdn",
                    "labels": {"solo-installer": self.state["nonce"]},
                    "options": {
                        "hostOptions": {"host": {"enabled": True, "value": self.spec.domain}},
                        "allowedHttpMethods": {"enabled": True, "value": ["GET", "HEAD", "OPTIONS"]},
                        "disableCache": {"enabled": True, "value": True},
                        "edgeCacheSettings": {"enabled": False},
                        "browserCacheSettings": {"enabled": True, "value": "0"},
                        "compressionOptions": {"gzipOn": {"enabled": True, "value": False}},
                        "redirectOptions": {"redirectHttpToHttps": {"enabled": True, "value": True}},
                    },
                    "sslCertificate": {"type": "CM", "data": {"cm": {"id": self.state["certificate_id"]}}},
                },
            )
        if (
            resource.get("folderId") != folder_id
            or resource.get("cname") != self.spec.cdn_domain
            or str(resource.get("originGroupId")) != self.state["origin_group_id"]
        ):
            raise InstallError("cloud", "Сохранённый CDN-ресурс не соответствует установке", retryable=False)
        target = resource.get("providerCname")
        if not isinstance(target, str) or not target:
            raise InstallWaitingError("Яндекс готовит CNAME ресурса CDN", retry_after=30)
        self.state["cname_target"] = target
        await self.wait_dns([{"type": "CNAME", "name": self.spec.cdn_domain, "value": target}])
        return await self.wait_delivery()

    @staticmethod
    def vk_items(payload) -> list[dict]:
        """Читает список объектов API VK CDN."""
        items = payload.get("results") if isinstance(payload, dict) else payload
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise InstallError("cloud", "VK Cloud вернул некорректный список ресурсов")
        return items

    async def vk_ensure(self, kind: str, endpoint: str, body: dict, matches: Callable[[dict], bool]) -> dict:
        """Восстанавливает собственный объект VK после потери ответа."""
        identifier = self.state.get(kind + "_id")
        if identifier:
            return await self.request("GET", f"{VK_API}/{endpoint}/{_identifier(identifier)}", auth="vk")
        items = self.vk_items(await self.request("GET", f"{VK_API}/{endpoint}", auth="vk"))
        owned = [item for item in items if matches(item)]
        if len(owned) > 1:
            raise InstallError("cloud", "Найдено несколько объектов VK одной установки", retryable=False)
        if owned:
            result = owned[0]
        else:
            intents = self.state.setdefault("intents", {})
            if intents.get(kind):
                raise InstallError(
                    "cloud", "Результат прошлого запроса VK неизвестен; проверьте ресурс вручную, дубликат не создан"
                )
            intents[kind] = True
            await self.save()
            try:
                result = await self.request("POST", f"{VK_API}/{endpoint}", body=body, auth="vk")
            except InstallError as error:
                if error.step == "cloud_auth":
                    intents.pop(kind, None)
                    await self.save()
                raise
        if not isinstance(result, dict):
            raise InstallError("cloud", "VK Cloud не вернул созданный объект")
        self.state[kind + "_id"] = _identifier(result.get("id"))
        await self.save()
        return result

    @staticmethod
    def vk_target(payload) -> str:
        """Извлекает единственную выданную VK цель CNAME."""
        values = []
        pending = [payload]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
            elif isinstance(value, str) and re.fullmatch(r"cl-[a-zA-Z0-9-]+\.service\.cdn\.msk\.vkcs\.cloud\.?", value):
                values.append(_dns_name(value))
        if len(set(values)) != 1:
            raise InstallError("cloud", "VK Cloud не вернул однозначную цель CNAME")
        return values[0]

    async def configure_vk(self) -> dict:
        """Создаёт VK CDN и продолжает установку после DNS-паузы."""
        client = await self.request("GET", f"{VK_API}/clients/me", auth="vk")
        target = self.vk_target(client)
        self.state["cname_target"] = target
        name = "solo-" + self.state["nonce"]
        group_name = self.state.get("origin_group_name", name)
        source = self.spec.domain
        group = await self.vk_ensure(
            "origin_group",
            "originGroups",
            {
                "name": group_name,
                "useNext": True,
                "origins": [{"source": source, "enabled": True, "backup": False}],
            },
            lambda item: item.get("name") == group_name,
        )
        if group.get("name") != group_name or [item.get("source") for item in group.get("origins", [])] != [source]:
            raise InstallError("cloud", "Сохранённая группа VK не соответствует установке", retryable=False)
        resource = await self.vk_ensure(
            "resource",
            "resources",
            {
                "cname": self.spec.cdn_domain,
                "originGroup": int(self.state["origin_group_id"]),
                "originProtocol": "HTTPS",
                "secondaryHostnames": [],
                "options": {
                    "host_header": {"enabled": True, "value": self.spec.cdn_domain},
                    "edge_cache_settings": {"enabled": False, "default": "0"},
                    "browser_cache_settings": {"enabled": True, "value": "0"},
                    "gzipOn": {"enabled": True, "value": False},
                    "brotli_compression": {"enabled": False, "value": ["text/html"]},
                    "cors": {"enabled": False, "value": []},
                    "ignoreQueryString": {"enabled": False, "value": False},
                },
            },
            lambda item: item.get("cname") == self.spec.cdn_domain
            and str(item.get("originGroup")) == self.state["origin_group_id"],
        )
        if (
            resource.get("cname") != self.spec.cdn_domain
            or str(resource.get("originGroup")) != self.state["origin_group_id"]
        ):
            raise InstallError("cloud", "Сохранённый ресурс VK не соответствует установке", retryable=False)
        await self.wait_dns([{"type": "CNAME", "name": self.spec.cdn_domain, "value": target}])
        resource_id = _identifier(self.state["resource_id"])
        if not self.state.get("certificate_requested"):
            await self.save()
            await self.request("POST", f"{VK_API}/resources/{resource_id}/ssl/le/issue", auth="vk")
            self.state["certificate_requested"] = True
            await self.save()
        if not self.state.get("cache_purged"):
            await self.save()
            await self.request("POST", f"{VK_API}/resources/{resource_id}/purge", body={"type": "full"}, auth="vk")
            self.state["cache_purged"] = True
            await self.save()
        return await self.wait_delivery()


async def configure_cdn(
    spec: InstallSpec, secrets: dict, artifacts: dict, checkpoint: Checkpoint | None = None
) -> dict:
    """Настраивает выбранный CDN без повторного создания ресурсов."""
    if spec.transport not in {"cdn_vk", "cdn_yandex"}:
        raise InstallError("cloud", "CDN выбран для неподдерживаемого транспорта", retryable=False)
    state = artifacts.setdefault("cloud", {})
    provider = "vk" if spec.transport == "cdn_vk" else "yandex"
    if state.get("provider") not in {None, provider}:
        raise InstallError("cloud", "Провайдер сохранённой установки изменился", retryable=False)
    if state.get("domain") not in {None, spec.cdn_domain}:
        raise InstallError("cloud", "Домен сохранённой установки изменился", retryable=False)
    state.setdefault("provider", provider)
    state.setdefault("domain", spec.cdn_domain)
    state.setdefault("origin_domain", spec.domain)
    state.setdefault("nonce", uuid.uuid4().hex[:24])
    async with _http_client() as client:
        cloud = _Cloud(client, spec, secrets, artifacts, checkpoint)
        await cloud.save()
        return await cloud.configure_vk() if provider == "vk" else await cloud.configure_yandex()


def cloud_change_plan(previous_spec: InstallSpec, spec: InstallSpec) -> dict:
    """Определяет точечную замену неизменяемых ресурсов CDN."""
    previous_cdn = previous_spec.transport in {"cdn_vk", "cdn_yandex"}
    current_cdn = spec.transport in {"cdn_vk", "cdn_yandex"}
    reasons = []
    if current_cdn and previous_spec.transport != spec.transport:
        reasons.append("провайдер CDN")
    if current_cdn and previous_spec.cdn_domain != spec.cdn_domain:
        reasons.append("основной CNAME")
    if current_cdn and previous_spec.cloud.get("folder_id", "") != spec.cloud.get("folder_id", ""):
        reasons.append("каталог облака")
    return {
        "requires_replacement": bool(reasons),
        "recreate": bool(reasons),
        "reason": ", ".join(reasons),
        "retained": ["прежний CDN-ресурс", "прежний сертификат"]
        if previous_cdn and (reasons or not current_cdn)
        else [],
    }


def _change_target(spec: InstallSpec, artifacts: dict) -> str:
    """Связывает изменение облака с точным набором параметров."""
    value = [spec.transport, spec.domain, spec.cdn_domain, spec.cloud, artifacts.get("xhttp_path")]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _retain_cloud(artifacts: dict, state: dict) -> None:
    """Сохраняет прежние ресурсы без автоматического удаления."""
    if not any(
        state.get(key) for key in ("resource_id", "origin_group_id", "certificate_id", "operations", "requests")
    ):
        return
    retained = artifacts.setdefault("retained_resources", [])
    identity = tuple(
        state.get(key) for key in ("provider", "resource_id", "origin_group_id", "certificate_id", "nonce")
    )
    if any(
        tuple(item.get(key) for key in ("provider", "resource_id", "origin_group_id", "certificate_id", "nonce"))
        == identity
        for item in retained
    ):
        return
    retained.append({
        "kind": "cloud",
        **{
            key: copy.deepcopy(state[key])
            for key in (
                "provider",
                "domain",
                "origin_domain",
                "resource_id",
                "origin_group_id",
                "certificate_id",
                "folder_id",
                "nonce",
                "operations",
                "requests",
                "request_bodies",
            )
            if key in state
        },
        "status": "retained",
    })


async def _settle_yandex_updates(cloud: _Cloud, target: str) -> None:
    """Дожидается прежних записей перед изменением их назначения."""
    state = cloud.state
    operations, requests = state.setdefault("operations", {}), state.setdefault("requests", {})
    details = state.setdefault("request_bodies", {})
    previous = [kind for kind in requests if kind.startswith("edit_") and not kind.endswith(target[:16])]
    for kind in previous:
        operation = operations.get(kind)
        if operation:
            result = await cloud.request("GET", f"{YANDEX_OPERATIONS}/{quote(operation['id'], safe='')}", auth="yandex")
        else:
            request = details.get(kind)
            if not request or request.get("method") != "PATCH":
                raise InstallError("cloud", "Сначала восстановите неизвестный результат предыдущего изменения Яндекса")
            await cloud.save()
            result = await cloud.request(
                "PATCH",
                request["url"],
                body=request["body"],
                auth="yandex",
                headers={"Idempotency-Key": requests[kind]},
            )
            if not isinstance(result, dict) or type(result.get("done")) is not bool:
                raise InstallError("cloud", "Яндекс не вернул предыдущую операцию изменения")
            operations[kind] = {"id": _identifier(result.get("id")), "started_at": time.time()}
            await cloud.save()
        if result.get("done") is not True:
            raise InstallWaitingError("Яндекс завершает предыдущее изменение; новый план сохранён", retry_after=10)
        operations.pop(kind, None)
        requests.pop(kind, None)
        details.pop(kind, None)
        state.pop(kind + "_id", None)
        await cloud.save()


async def _update_existing_cdn(cloud: _Cloud, previous_spec: InstallSpec, target: str) -> None:
    """Обновляет подтверждённые источники и параметры своего CDN."""
    state, spec = cloud.state, cloud.spec
    provider = "vk" if spec.transport == "cdn_vk" else "yandex"
    if state.get("provider") != provider or state.get("domain") != spec.cdn_domain:
        raise InstallError("cloud", "Сохранённый CDN не соответствует изменяемой установке", retryable=False)
    group_id, resource_id = _identifier(state.get("origin_group_id")), _identifier(state.get("resource_id"))
    name = state.get("origin_group_name", "solo-" + str(state.get("nonce", "")))
    if not re.fullmatch(r"solo-[a-f0-9]{24}", name):
        raise InstallError("cloud", "Не подтверждена принадлежность группы CDN", retryable=False)
    if provider == "yandex":
        await _settle_yandex_updates(cloud, target)
        folder_id = _identifier(state.get("folder_id"))
        group = await cloud.request(
            "GET", f"{YANDEX_CDN}/originGroups/{group_id}", params={"folderId": folder_id}, auth=provider
        )
        resource = await cloud.request("GET", f"{YANDEX_CDN}/resources/{resource_id}", auth=provider)
        source = spec.domain + ":7443"
        if (
            group.get("folderId") != folder_id
            or resource.get("folderId") != folder_id
            or resource.get("labels", {}).get("solo-installer") != state.get("nonce")
        ):
            raise InstallError("cloud", "Каталог или метка ресурса Яндекса изменены", retryable=False)
        origin_group = resource.get("originGroupId")
    else:
        group = await cloud.request("GET", f"{VK_API}/originGroups/{group_id}", auth=provider)
        resource = await cloud.request("GET", f"{VK_API}/resources/{resource_id}", auth=provider)
        source = spec.domain
        origin_group = resource.get("originGroup")
    origins = group.get("origins")
    approved_domains = {previous_spec.domain, spec.domain, state.get("origin_domain")}
    approved_domains.update(state.get("origin_intents", []))
    edit = cloud.artifacts.get("edit") or {}
    for value in [edit.get("before"), edit.get("previous_target"), *(edit.get("applied_specs") or {}).values()]:
        if not isinstance(value, dict):
            continue
        try:
            candidate = InstallSpec.from_dict(value)
        except (ValueError, TypeError):
            continue
        if (
            candidate.panel_ref == previous_spec.panel_ref
            and candidate.address == previous_spec.address
            and candidate.role == previous_spec.role
        ):
            approved_domains.add(candidate.domain)
    approved_sources = {
        domain + (":7443" if provider == "yandex" else "")
        for domain in approved_domains
        if isinstance(domain, str) and domain
    }
    if (
        group.get("name") != name
        or str(group.get("id")) != group_id
        or not isinstance(origins, list)
        or len(origins) != 1
        or not isinstance(origins[0], dict)
        or origins[0].get("source") not in approved_sources
        or str(resource.get("id")) != resource_id
        or resource.get("cname") != spec.cdn_domain
        or str(origin_group) != group_id
    ):
        raise InstallError("cloud", "Объекты CDN изменены вне установщика; обновление остановлено", retryable=False)
    if state.get("edit_target") != target:
        state.pop("delivery_started_at", None)
        state["control_update_started_at"] = time.time()
    state["ready"] = False
    state["edit_target"] = target
    await cloud.save()

    operation = "edit_group_" + target[:16]
    if origins[0]["source"] != source or operation in state.get("operations", {}):
        actual_domain = origins[0]["source"].removesuffix(":7443") if provider == "yandex" else origins[0]["source"]
        state["origin_intents"] = [actual_domain, spec.domain]
        state["origin_update_pending"] = True
        await cloud.save()
        if provider == "yandex":
            await cloud.yandex_create(
                operation,
                f"{YANDEX_CDN}/originGroups",
                {
                    "folderId": folder_id,
                    "originGroupId": group_id,
                    "groupName": name,
                    "useNext": group.get("useNext", True),
                    "origins": [{"source": source, "enabled": True, "backup": False}],
                },
                method="PATCH",
            )
        else:
            await cloud.request(
                "PUT",
                f"{VK_API}/originGroups/{group_id}",
                body={
                    "name": name,
                    "useNext": group.get("useNext", True),
                    "origins": [{"source": source, "enabled": True, "backup": False}],
                },
                auth=provider,
            )
    options = copy.deepcopy(resource.get("options") or {})
    desired_options = copy.deepcopy(options)
    if provider == "yandex":
        desired_options["hostOptions"] = {"host": {"enabled": True, "value": spec.domain}}
    else:
        desired_options["host_header"] = {"enabled": True, "value": spec.cdn_domain}
    operation = "edit_resource_" + target[:16]
    if options != desired_options or operation in state.get("operations", {}):
        await cloud.save()
        if provider == "yandex":
            await cloud.yandex_create(
                operation,
                f"{YANDEX_CDN}/resources/{resource_id}",
                {
                    "options": desired_options,
                    "updateMask": "options",
                },
                method="PATCH",
            )
        else:
            await cloud.request(
                "PUT",
                f"{VK_API}/resources/{resource_id}",
                body={
                    "originGroup": int(group_id),
                    "originProtocol": resource.get("originProtocol", "HTTPS"),
                    "secondaryHostnames": resource.get("secondaryHostnames", []),
                    "options": desired_options,
                },
                auth=provider,
            )
    if provider == "yandex":
        confirmed_group = await cloud.request(
            "GET", f"{YANDEX_CDN}/originGroups/{group_id}", params={"folderId": folder_id}, auth=provider
        )
        confirmed_resource = await cloud.request("GET", f"{YANDEX_CDN}/resources/{resource_id}", auth=provider)
        desired_host = desired_options["hostOptions"]
        actual_host = confirmed_resource.get("options", {}).get("hostOptions")
    else:
        confirmed_group = await cloud.request("GET", f"{VK_API}/originGroups/{group_id}", auth=provider)
        confirmed_resource = await cloud.request("GET", f"{VK_API}/resources/{resource_id}", auth=provider)
        desired_host = desired_options["host_header"]
        actual_host = confirmed_resource.get("options", {}).get("host_header")
    if [item.get("source") for item in confirmed_group.get("origins", [])] != [source] or actual_host != desired_host:
        if time.time() - state.setdefault("control_update_started_at", time.time()) >= 900:
            raise InstallError("cloud", "Облако не подтвердило выбранные настройки за пятнадцать минут")
        await cloud.save()
        raise InstallWaitingError("Облако применяет новые настройки; прежние идентификаторы сохранены", retry_after=30)
    state["origin_domain"] = spec.domain
    state["origin_intents"] = [spec.domain]
    state.pop("origin_update_pending", None)
    await cloud.save()


async def update_cdn(
    previous_spec: InstallSpec,
    spec: InstallSpec,
    secrets: dict,
    artifacts: dict,
    checkpoint: Checkpoint | None = None,
    *,
    allow_recreate: bool = False,
) -> dict:
    """Применяет точечное изменение CDN, сохраняя прежний ресурс."""
    if spec.transport not in {"cdn_vk", "cdn_yandex"}:
        _retain_cloud(artifacts, artifacts.get("cloud") or {})
        _retain_cloud(artifacts, (artifacts.get("cloud_pending") or {}).get("state") or {})
        artifacts.pop("cloud", None)
        artifacts.pop("cloud_pending", None)
        if checkpoint:
            await checkpoint(artifacts)
        return {"ready": True, "disabled": True}
    target = _change_target(spec, artifacts)
    current = artifacts.get("cloud") or {}
    pending = artifacts.get("cloud_pending")
    if pending and pending.get("target") != target:
        if not allow_recreate:
            raise InstallError(
                "cloud", "Подтвердите новый план: незавершённые ресурсы CDN будут сохранены отдельно", retryable=False
            )
        _retain_cloud(artifacts, pending.get("state") or {})
        artifacts.pop("cloud_pending", None)
        pending = None
        if checkpoint:
            await checkpoint(artifacts)
    if current.get("edit_target") == target and current.get("ready"):
        return current
    plan = cloud_change_plan(previous_spec, spec)
    if plan["requires_replacement"]:
        if not allow_recreate:
            raise InstallError(
                "cloud", "Требуется подтверждение замены только ресурсов CDN: " + plan["reason"], retryable=False
            )
        if not pending:
            state = {}
            same_folder = previous_spec.cloud.get("folder_id", "") == spec.cloud.get("folder_id", "")
            if (
                current.get("resource_id")
                and previous_spec.transport == spec.transport
                and current.get("origin_domain", previous_spec.domain) == spec.domain
                and same_folder
                and not current.get("origin_update_pending")
            ):
                state.update({key: current[key] for key in ("origin_group_id", "folder_id") if current.get(key)})
                state["origin_group_name"] = current.get("origin_group_name", "solo-" + str(current.get("nonce", "")))
            pending = {"target": target, "state": state}
            artifacts["cloud_pending"] = pending
            if checkpoint:
                await checkpoint(artifacts)
        shadow = {**artifacts, "cloud": pending["state"]}

        async def save_pending(value: dict) -> None:
            pending["state"] = value["cloud"]
            if checkpoint:
                await checkpoint(artifacts)

        result = await configure_cdn(spec, secrets, shadow, save_pending)
        if result.get("ready") is not True:
            raise InstallError("cloud", "Новый ресурс CDN ещё не готов")
        _retain_cloud(artifacts, current)
        result.update(edit_target=target, origin_domain=spec.domain)
        artifacts["cloud"] = result
        artifacts.pop("cloud_pending", None)
        if checkpoint:
            await checkpoint(artifacts)
        return result
    if not current.get("resource_id"):
        return await configure_cdn(spec, secrets, artifacts, checkpoint)
    async with _http_client() as client:
        cloud = _Cloud(client, spec, secrets, artifacts, checkpoint)
        await _update_existing_cdn(cloud, previous_spec, target)
        return await cloud.configure_vk() if spec.transport == "cdn_vk" else await cloud.configure_yandex()

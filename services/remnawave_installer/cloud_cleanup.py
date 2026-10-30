import asyncio
import copy
import hashlib
import json
import re
import uuid

from urllib.parse import quote

import httpx

from .cloud import VK_API, YANDEX_CDN, YANDEX_CERTIFICATES, YANDEX_OPERATIONS, _Cloud, _http_client, _identifier
from .types import InstallError, InstallSpec, InstallWaitingError


_KINDS = ("resource", "origin_group", "certificate")
_COLLECTIONS = {"resource": "resources", "origin_group": "originGroups", "certificate": "certificates"}


def _blocked(message: str) -> InstallError:
    """Сохраняет запись при недоказанной безопасности удаления."""
    return InstallError("cloud_cleanup", message + "; запись установки сохранена", retryable=False)


def _states(artifacts: dict) -> list[dict]:
    """Собирает текущие, незавершённые и сохранённые облачные ресурсы."""
    retained = artifacts.get("retained_resources", [])
    pending = artifacts.get("cloud_pending", {})
    if not isinstance(retained, list) or not isinstance(pending, dict):
        raise _blocked("Некорректная история ресурсов CDN")
    values = [artifacts.get("cloud", {}), pending.get("state", {})]
    for item in retained:
        if not isinstance(item, dict):
            raise _blocked("Некорректный снимок сохранённого ресурса")
        if item.get("kind") == "cloud":
            values.append(item)
    result = []
    for value in values:
        if not isinstance(value, dict):
            raise _blocked("Некорректный снимок ресурсов CDN")
        if not any(value.get(key) for key in (*[kind + "_id" for kind in _KINDS], "operations", "requests", "intents")):
            continue
        if value.get("provider") not in {"vk", "yandex"} or not re.fullmatch(
            r"[a-f0-9]{24}", str(value.get("nonce", ""))
        ):
            raise _blocked("Не сохранены провайдер или отметка владельца CDN")
        scope = "project_id" if value["provider"] == "vk" else "folder_id"
        _identifier(value.get(scope))
        result.append(value)
    return result


def _binding(spec: InstallSpec, artifacts: dict) -> str:
    """Связывает план с неизменным снимком установки."""
    value = {"params": spec.to_dict(), "states": _states(artifacts)}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _saved_plan(spec: InstallSpec, artifacts: dict) -> dict | None:
    """Отказывает при подмене завершённого или промежуточного плана."""
    saved = artifacts.get("cloud_cleanup")
    if saved is None:
        return None
    if (
        not isinstance(saved, dict)
        or saved.get("version") != 1
        or saved.get("install_id") != artifacts.get("install_id")
        or saved.get("binding") != _binding(spec, artifacts)
        or not isinstance(saved.get("steps"), list)
        or not isinstance(saved.get("done"), list)
    ):
        raise _blocked("Сохранённый план очистки CDN не соответствует установке")
    return saved


def _vk_auth_mode(spec: InstallSpec, secrets: dict | None = None) -> str:
    """Выбирает полный набор доступов без сетевого переключения режима."""
    preferred = spec.cloud.get("auth_mode", "token")
    if secrets is not None:
        token = isinstance(secrets.get("vk_token"), str) and bool(secrets["vk_token"].strip())
        account = all(
            isinstance(secrets.get(field), str) and secrets[field].strip()
            for field in ("vk_username", "vk_password", "vk_user_domain")
        )
        if token and not account:
            return "token"
        if account and not token:
            return "service_account"
    return preferred


def required_cloud_secrets(spec: InstallSpec, artifacts: dict, secrets: dict | None = None) -> set[str]:
    """Возвращает доступы только для незавершённой очистки CDN."""
    saved = _saved_plan(spec, artifacts)
    if saved and saved.get("completed") is True:
        return set()
    providers = {state["provider"] for state in _states(artifacts)}
    result = {"yandex_key"} if "yandex" in providers else set()
    if "vk" in providers:
        result.update(
            {"vk_username", "vk_password", "vk_user_domain"}
            if _vk_auth_mode(spec, secrets) == "service_account"
            else {"vk_token"}
        )
    return result


def _scope(state: dict) -> tuple[str, str]:
    """Определяет изолированное пространство облачного аккаунта."""
    return state["provider"], _identifier(state["project_id" if state["provider"] == "vk" else "folder_id"])


def _path(provider: str, kind: str, identifier: str | None = None) -> str:
    """Формирует путь только к поддерживаемым объектам CDN."""
    base = VK_API if provider == "vk" else YANDEX_CERTIFICATES if kind == "certificate" else YANDEX_CDN
    return base + "/" + _COLLECTIONS[kind] + ("/" + quote(_identifier(identifier), safe="") if identifier else "")


class _CleanupCloud(_Cloud):
    async def request(self, method, url, *, body=None, params=None, headers=None, auth="", missing=False):
        """Проверяет контракт удаления и не раскрывает токены."""
        request_headers = dict(headers or {})
        if auth == "vk":
            if not url.startswith(VK_API + "/"):
                raise _blocked("Некорректный адрес API VK Cloud")
            url = f"{VK_API}/projects/{quote(self.spec.cloud['project_id'], safe='')}/{url[len(VK_API) + 1 :]}"
            request_headers["X-Auth-Token"] = await self.vk_auth_token()
        elif auth == "yandex":
            if not self.iam_token:
                await self.refresh_iam()
            request_headers["Authorization"] = "Bearer " + self.iam_token
        try:
            response = await self.client.request(method, url, json=body, params=params, headers=request_headers)
            if response.status_code == 401 and auth == "yandex":
                await self.refresh_iam()
                request_headers["Authorization"] = "Bearer " + self.iam_token
                response = await self.client.request(method, url, json=body, params=params, headers=request_headers)
            elif response.status_code == 401 and auth == "vk" and self.spec.cloud.get("auth_mode") == "service_account":
                await self.refresh_vk()
                request_headers["X-Auth-Token"] = self.vk_token
                response = await self.client.request(method, url, json=body, params=params, headers=request_headers)
        except httpx.HTTPError:
            raise InstallError("cloud_cleanup", "Ответ облака не получен; повторите удаление") from None
        if response.status_code in {401, 403}:
            raise InstallError("cloud_auth", "Проверьте облачные доступы и права удаления сохранённых ресурсов CDN")
        if response.status_code == 404 and missing:
            return None
        accepted = (
            {204} if method == "DELETE" and auth == "vk" else {200} if method == "DELETE" else set(range(200, 300))
        )
        if response.status_code not in accepted:
            raise InstallError("cloud_cleanup", f"Облако отклонило {method}: HTTP {response.status_code}")
        if not response.content:
            return {}
        try:
            result = response.json()
        except ValueError:
            raise InstallError("cloud_cleanup", "Облако вернуло некорректный JSON") from None
        if not isinstance(result, dict | list):
            raise InstallError("cloud_cleanup", "Облако вернуло некорректный ответ")
        return result

    async def items(self, kind: str) -> dict[str, dict]:
        """Читает полный список для проверки чужих связей."""
        provider, scope = _scope(self.state)
        if provider == "vk":
            payload = await self.request("GET", _path(provider, kind), auth=provider)
            items = payload.get("results") if isinstance(payload, dict) else payload
            if isinstance(payload, dict) and (
                payload.get("next") or ("count" in payload and payload["count"] != len(items or []))
            ):
                raise _blocked("VK Cloud вернул неполный список ресурсов")
        else:
            items, token, seen = [], "", set()
            while True:
                payload = await self.request(
                    "GET",
                    _path(provider, kind),
                    params={"folderId": scope, "pageSize": 1000, **({"pageToken": token} if token else {})},
                    auth=provider,
                )
                page = payload.get(_COLLECTIONS[kind]) if isinstance(payload, dict) else None
                if not isinstance(page, list):
                    raise _blocked("Яндекс вернул неполный список ресурсов")
                items.extend(page)
                token = payload.get("nextPageToken")
                if not token:
                    break
                if not isinstance(token, str) or token in seen:
                    raise _blocked("Яндекс повторил страницу списка ресурсов")
                seen.add(token)
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise _blocked("Облако вернуло некорректный список ресурсов")
        result = {}
        for item in items:
            identifier = _identifier(item.get("id"))
            if identifier in result:
                raise _blocked("Облако вернуло повторные идентификаторы ресурсов")
            result[identifier] = item
        return result

    async def get(self, kind: str, identifier: str) -> dict | None:
        """Подтверждает существование конкретного объекта."""
        provider, scope = _scope(self.state)
        params = {"folderId": scope} if provider == "yandex" and kind == "origin_group" else None
        value = await self.request("GET", _path(provider, kind, identifier), params=params, auth=provider, missing=True)
        if value is not None and (not isinstance(value, dict) or _identifier(value.get("id")) != identifier):
            raise _blocked("Облако вернуло другой объект вместо запрошенного")
        return value


def _approved_domains(spec: InstallSpec, artifacts: dict, states: list[dict]) -> set[str]:
    """Собирает подтверждённые источники прежних настроек."""
    edit = artifacts.get("edit") or {}
    values = [spec.domain, *[item.get("origin_domain") for item in states]]
    for state in states:
        values.extend(state.get("origin_intents", []))
    for params in [edit.get(key) for key in ("before", "after", "previous_target")]:
        if (
            isinstance(params, dict)
            and params.get("address") == spec.address
            and params.get("panel_ref") == spec.panel_ref
        ):
            values.append(params.get("domain"))
    return {value for value in values if isinstance(value, str) and value}


def _owns(kind: str, item: dict, state: dict, domains: set[str], names: set[str]) -> bool:
    """Проверяет отметки установщика и управляемые связи объекта."""
    provider, scope = _scope(state)
    if provider == "yandex" and item.get("folderId") != scope:
        return False
    if kind == "origin_group":
        origins = item.get("origins")
        return bool(
            item.get("name") in names
            and item.get("useNext") is True
            and isinstance(origins, list)
            and len(origins) == 1
            and isinstance(origins[0], dict)
            and origins[0].get("enabled") is True
            and origins[0].get("backup") is False
            and origins[0].get("source") in {domain + (":7443" if provider == "yandex" else "") for domain in domains}
        )
    if kind == "certificate":
        labels = item.get("labels")
        return bool(
            item.get("name") == "solo-" + state["nonce"]
            and item.get("domains") == [state.get("domain")]
            and isinstance(labels, dict)
            and labels.get("solo-installer") == state["nonce"]
            and not item.get("deletionProtection")
        )
    group = item.get("originGroup" if provider == "vk" else "originGroupId")
    certificate = _certificate_id(item)
    labels = item.get("labels")
    return bool(
        item.get("cname") == state.get("domain")
        and str(group) == str(state.get("origin_group_id"))
        and item.get("secondaryHostnames", []) == []
        and item.get("originProtocol") == "HTTPS"
        and (provider == "vk" or isinstance(labels, dict) and labels.get("solo-installer") == state["nonce"])
        and (provider == "vk" or certificate == str(state.get("certificate_id")))
    )


def _certificate_id(item: dict) -> str | None:
    """Проверяет форму ссылки на облачный сертификат."""
    value = item.get("sslCertificate")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _blocked("Облако вернуло некорректную привязку сертификата")
    data = value.get("data") or {}
    if not isinstance(data, dict) or not isinstance(data.get("cm", {}), dict):
        raise _blocked("Облако вернуло некорректную привязку сертификата")
    certificate = data.get("cm", {}).get("id")
    return str(certificate) if certificate is not None else None


def _snapshot(kind: str, item: dict) -> dict:
    """Фиксирует идентичность без изменяющихся статусов раскатки."""
    fields = {
        "resource": (
            "id",
            "folderId",
            "cname",
            "originGroup",
            "originGroupId",
            "labels",
            "secondaryHostnames",
            "createdAt",
            "created_at",
            "originProtocol",
            "options",
            "active",
            "enabled",
            "ssl_automated",
            "sslEnabled",
            "sslCertificate",
            "sslData",
        ),
        "origin_group": ("id", "folderId", "name", "origins", "useNext", "createdAt", "created_at"),
        "certificate": ("id", "folderId", "name", "domains", "labels", "createdAt", "deletionProtection"),
    }[kind]
    result = {key: copy.deepcopy(item[key]) for key in fields if key in item}
    if kind == "resource" and isinstance(result.get("sslCertificate"), dict):
        result["sslCertificate"].pop("status", None)
    return result


async def _settle_creations(cloud: _CleanupCloud, state: dict) -> None:
    """Дожидается известных операций до построения плана удаления."""
    if state["provider"] != "yandex":
        return
    operations = state.get("operations", {})
    if not isinstance(operations, dict):
        raise _blocked("Некорректный снимок операций Яндекса")
    for kind, operation in operations.items():
        if not isinstance(operation, dict):
            raise _blocked("Некорректная операция Яндекса")
        identifier = _identifier(operation.get("id"))
        result = await cloud.request("GET", f"{YANDEX_OPERATIONS}/{identifier}", auth="yandex", missing=True)
        if result is None:
            continue
        if not isinstance(result, dict) or type(result.get("done")) is not bool:
            raise _blocked("Не подтверждено завершение операции Яндекса")
        if not result["done"]:
            raise InstallWaitingError("Яндекс завершает прежнюю операцию CDN", retry_after=10)
        if not result.get("error") and kind in _KINDS and not state.get(kind + "_id"):
            response = result.get("response")
            if not isinstance(response, dict):
                raise _blocked("Не восстановлен результат создания ресурса Яндекса")
            state[kind + "_id"] = _identifier(response.get("id"))


async def _recover(cloud: _CleanupCloud, state: dict, inventories: dict, domains: set[str], names: set[str]) -> None:
    """Восстанавливает потерянные ответы только по уникальному владельцу."""
    for kind in ("origin_group", "certificate", "resource"):
        if kind == "certificate" and state["provider"] == "vk":
            continue
        if state.get(kind + "_id"):
            _identifier(state[kind + "_id"])
            continue
        claimed = any(
            isinstance(state.get(key), dict) and state[key].get(kind) for key in ("intents", "requests", "operations")
        )
        if not claimed:
            continue
        owned_names = {state.get("origin_group_name", "solo-" + state["nonce"])} if kind == "origin_group" else names
        matches = [item for item in inventories[kind].values() if _owns(kind, item, state, domains, owned_names)]
        if len(matches) != 1:
            raise _blocked("Не удалось однозначно восстановить результат создания CDN: " + kind)
        state[kind + "_id"] = _identifier(matches[0].get("id"))


def _key(provider: str, scope: str, kind: str, identifier: str) -> str:
    """Разделяет одинаковые идентификаторы разных проектов."""
    return ":".join((provider, scope, kind, identifier))


async def _validate(plan: dict, clouds: dict) -> dict[str, dict | None]:
    """Проверяет весь план и чужие ссылки до очередного удаления."""
    resources = {scope: await cloud.items("resource") for scope, cloud in clouds.items()}
    own = {(step["provider"], step["scope"], step["id"]) for step in plan["steps"] if step["kind"] == "resource"}
    groups = {(step["provider"], step["scope"], step["id"]) for step in plan["steps"] if step["kind"] == "origin_group"}
    certificates = {
        (step["provider"], step["scope"], step["id"]) for step in plan["steps"] if step["kind"] == "certificate"
    }
    for (provider, scope), items in resources.items():
        for identifier, item in items.items():
            if (provider, scope, identifier) in own:
                continue
            group = item.get("originGroup" if provider == "vk" else "originGroupId")
            certificate = _certificate_id(item)
            if (provider, scope, str(group)) in groups or (provider, scope, str(certificate)) in certificates:
                raise _blocked("Удаляемая группа или сертификат используется посторонним ресурсом CDN")
    found = {}
    for step in plan["steps"]:
        cloud = clouds[(step["provider"], step["scope"])]
        actual = await cloud.get(step["kind"], step["id"])
        found[step["key"]] = actual
        if actual is None:
            continue
        if step["key"] in plan["done"]:
            raise _blocked("Удалённый объект CDN появился снова")
        if _snapshot(step["kind"], actual) != step["expected"]:
            raise _blocked("Объект CDN изменён после подготовки удаления")
        if step["kind"] == "origin_group" and "resourcesMetadata" in actual:
            metadata = actual["resourcesMetadata"]
            if not isinstance(metadata, list) or any(
                not isinstance(item, dict) or (step["provider"], step["scope"], str(item.get("id"))) not in own
                for item in metadata
            ):
                raise _blocked("Группа источников связана с посторонним ресурсом CDN")
    return found


async def _delete(cloud: _CleanupCloud, step: dict, plan: dict, artifacts: dict, checkpoint) -> None:
    """Сохраняет намерение и подтверждает завершение удаления."""
    provider, scope = step["provider"], step["scope"]
    params = {"folderId": scope} if provider == "yandex" and step["kind"] == "origin_group" else None
    pending = plan.setdefault("pending", {})
    intent = pending.setdefault(step["key"], {"request_id": str(uuid.uuid4())})
    await checkpoint(artifacts)
    should_delete = not intent.get("operation_id")
    if provider == "yandex" and intent.get("operation_id"):
        result = await cloud.request(
            "GET", f"{YANDEX_OPERATIONS}/{intent['operation_id']}", auth=provider, missing=True
        )
        if result is None and await cloud.get(step["kind"], step["id"]) is not None:
            intent.pop("operation_id")
            await checkpoint(artifacts)
            should_delete = True
    if should_delete:
        result = await cloud.request(
            "DELETE",
            _path(provider, step["kind"], step["id"]),
            params=params,
            auth=provider,
            missing=True,
            headers={"Idempotency-Key": intent["request_id"]} if provider == "yandex" else None,
        )
        if provider == "yandex" and result is not None:
            if not isinstance(result, dict) or type(result.get("done")) is not bool:
                raise InstallError("cloud_cleanup", "Яндекс не подтвердил операцию удаления")
            intent["operation_id"] = _identifier(result.get("id"))
            await checkpoint(artifacts)
    if provider == "yandex" and result is not None:
        if not isinstance(result, dict) or type(result.get("done")) is not bool:
            raise InstallError("cloud_cleanup", "Яндекс вернул некорректную операцию удаления")
        if result.get("error"):
            pending.pop(step["key"], None)
            await checkpoint(artifacts)
            raise InstallError("cloud_cleanup", "Операция удаления Яндекса завершилась ошибкой")
        if not result["done"]:
            raise InstallWaitingError("Яндекс удаляет ресурс CDN; операция сохранена", retry_after=10)
    if await cloud.get(step["kind"], step["id"]) is not None:
        raise InstallError("cloud_cleanup", "Облако не подтвердило отсутствие удалённого ресурса")
    plan["done"].append(step["key"])
    pending.pop(step["key"], None)
    await checkpoint(artifacts)


async def cleanup_cloud(
    spec: InstallSpec, secrets: dict, artifacts: dict, checkpoint, log, *, preflight_only=False
) -> None:
    """Удаляет только собственные ресурсы CDN после проверки всех связей."""
    identifier = artifacts.get("install_id")
    if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
        raise _blocked("Не сохранён идентификатор установки для очистки CDN")
    saved = _saved_plan(spec, artifacts)
    if saved and saved.get("completed") is True:
        return
    states = _states(artifacts)
    if not states:
        artifacts["cloud_cleanup"] = {
            "version": 1,
            "install_id": identifier,
            "binding": _binding(spec, artifacts),
            "steps": [],
            "done": [],
            "completed": True,
        }
        await checkpoint(artifacts)
        return
    required = required_cloud_secrets(spec, artifacts, secrets)
    if any(not secrets.get(field) for field in required):
        raise InstallError("cloud_auth", "Для полного удаления введите облачные доступы повторно")
    async with _http_client() as client:
        clouds = {}
        for state in states:
            key = _scope(state)
            if key not in clouds:
                chosen = copy.deepcopy(spec)
                chosen.cloud = {**spec.cloud, "project_id" if key[0] == "vk" else "folder_id": key[1]}
                if key[0] == "vk":
                    chosen.cloud["auth_mode"] = _vk_auth_mode(spec, secrets)
                clouds[key] = _CleanupCloud(client, chosen, secrets, {"cloud": state}, None)
        if saved is None:
            for state in states:
                await _settle_creations(clouds[_scope(state)], state)
            await checkpoint(artifacts)
            inventories = {}
            for key, cloud in clouds.items():
                inventories[key] = {
                    kind: await cloud.items(kind) for kind in _KINDS if kind != "certificate" or key[0] == "yandex"
                }
            domains = _approved_domains(spec, artifacts, states)
            names = {"solo-" + state["nonce"] for state in states}
            for state in states:
                await _recover(clouds[_scope(state)], state, inventories[_scope(state)], domains, names)
            await checkpoint(artifacts)
            entries = {}
            for state in states:
                provider, scope = _scope(state)
                for kind in _KINDS:
                    resource_id = state.get(kind + "_id")
                    if not resource_id:
                        continue
                    if provider == "vk" and kind == "certificate":
                        raise _blocked("Отдельный сертификат VK не принадлежит управляемому CDN")
                    resource_id = _identifier(resource_id)
                    actual = await clouds[(provider, scope)].get(kind, resource_id)
                    if actual is not None and not _owns(kind, actual, state, domains, names):
                        raise _blocked("Не подтверждена принадлежность ресурса CDN: " + kind)
                    key = _key(provider, scope, kind, resource_id)
                    entry = {
                        "key": key,
                        "provider": provider,
                        "scope": scope,
                        "kind": kind,
                        "id": resource_id,
                        "expected": _snapshot(kind, actual) if actual is not None else None,
                    }
                    if key in entries and entries[key] != entry:
                        raise _blocked("Противоречащие снимки одного ресурса CDN")
                    entries[key] = entry
            saved = {
                "version": 1,
                "install_id": identifier,
                "binding": _binding(spec, artifacts),
                "steps": sorted(entries.values(), key=lambda item: (_KINDS.index(item["kind"]), item["key"])),
                "done": [],
            }
            await _validate(saved, clouds)
            artifacts["cloud_cleanup"] = saved
            await checkpoint(artifacts)
        await _validate(saved, clouds)
        if preflight_only:
            return
        for step in saved["steps"]:
            found = await _validate(saved, clouds)
            if step["key"] in saved["done"]:
                continue
            await log("delete:cloud_" + step["kind"])
            pending_operation = (saved.get("pending", {}).get(step["key"]) or {}).get("operation_id")
            if found[step["key"]] is None and not pending_operation:
                saved["done"].append(step["key"])
                await checkpoint(artifacts)
                continue
            await _delete(clouds[(step["provider"], step["scope"])], step, saved, artifacts, checkpoint)
        await _validate(saved, clouds)
        saved["completed"] = True
        await checkpoint(artifacts)

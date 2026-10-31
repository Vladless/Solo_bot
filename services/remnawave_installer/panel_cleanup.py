import asyncio
import copy
import re

from contextlib import AsyncExitStack
from urllib.parse import quote

from .panel import _SQUAD_LOCKS, Checkpoint, InstallerPanel, _approved_specs, _uuid
from .profiles import build_profile
from .types import PANEL_VERSION, InstallError, InstallSpec


def _blocked(message: str) -> InstallError:
    """Останавливает удаление без доказанной принадлежности."""
    return InstallError("panel_cleanup", message + "; запись установки сохранена", retryable=False)


def _ids(items, label: str) -> list[str]:
    """Проверяет уникальные UUID списка объектов."""
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise _blocked("Панель вернула некорректный список: " + label)
    result = [_uuid(item.get("uuid"), label) for item in items]
    if len(result) != len(set(result)):
        raise _blocked("Панель вернула повторные UUID: " + label)
    return result


def _matches(actual: dict, expected: dict) -> bool:
    """Сравнивает управляемые поля объекта."""
    return isinstance(actual, dict) and all(actual.get(key) == value for key, value in expected.items())


async def _inventory(panel: InstallerPanel) -> dict:
    """Читает все ссылки перед изменением панели."""
    profile_data = await panel._call("GET", "/config-profiles")
    squad_data = await panel._call("GET", "/internal-squads")
    for data, key in ((profile_data, "configProfiles"), (squad_data, "internalSquads")):
        if (
            not isinstance(data, dict)
            or not isinstance(data.get(key), list)
            or type(data.get("total")) is not int
            or data["total"] != len(data[key])
        ):
            raise _blocked("Панель вернула неполный список объектов")
    profiles = profile_data["configProfiles"]
    nodes = await panel._call("GET", "/nodes")
    hosts = await panel._call("GET", "/hosts")
    squads = squad_data["internalSquads"]
    result = {}
    for key, items in (("profiles", profiles), ("nodes", nodes), ("hosts", hosts), ("squads", squads)):
        identifiers = _ids(items, key)
        result[key] = dict(zip(identifiers, items, strict=True))
    for profile in profiles:
        _ids(profile.get("inbounds"), "инбаунды профиля")
        if any(
            item.get("profileUuid") != profile["uuid"] or not isinstance(item.get("tag"), str)
            for item in profile["inbounds"]
        ):
            raise _blocked("Панель вернула некорректные связи инбаундов профиля")
    return result


async def _users(panel: InstallerPanel) -> list[dict]:
    """Ищет технического пользователя даже после смены имени."""
    result = []
    start = 0
    total = None
    while total is None or start < total:
        response = await panel._call("GET", f"/users?start={start}&size=1000")
        if not isinstance(response, dict) or type(response.get("total")) is not int or response["total"] < 0:
            raise _blocked("Панель вернула некорректный список пользователей")
        if total is not None and response["total"] != total:
            raise _blocked("Состав пользователей изменился во время проверки")
        total = response["total"]
        page = response.get("users")
        if (
            not isinstance(page, list)
            or any(not isinstance(item, dict) or type(item.get("id")) is not int for item in page)
            or len(page) != min(1000, max(0, total - start))
        ):
            raise _blocked("Панель вернула неполную страницу пользователей")
        result.extend(page)
        start += len(page)
    if len({item["id"] for item in result}) != len(result):
        raise _blocked("Панель вернула повторных пользователей")
    return result


def _specs(spec: InstallSpec, artifacts: dict) -> list[InstallSpec]:
    """Собирает подтверждённые варианты параметров установки."""
    result = _approved_specs(artifacts, spec, spec)
    after = (artifacts.get("edit") or {}).get("after")
    if isinstance(after, dict):
        try:
            candidate = InstallSpec.from_dict(after)
        except (ValueError, TypeError):
            pass
        else:
            if (candidate.panel_ref, candidate.address, candidate.role) == (spec.panel_ref, spec.address, spec.role):
                result.append(candidate)
    return result


def _configs(specs: list[InstallSpec], artifacts: dict) -> list[dict]:
    """Восстанавливает только подтверждённые конфигурации профиля."""
    result = [artifacts.get("profile_config")]
    result.extend(
        item.get("config")
        for item in InstallerPanel._update_intents(artifacts, "profile", artifacts.get("profile_uuid"))
    )
    for spec in specs:
        try:
            result.append(build_profile(spec, artifacts))
        except (InstallError, KeyError, ValueError, TypeError):
            continue
    return [item for item in result if isinstance(item, dict)]


def _entry(kind: str, panel_ref: str, identifier, expected: dict, **extra: object) -> dict:
    """Создаёт сохраняемый шаг плана удаления."""
    return {
        "key": f"{panel_ref}:{kind}:{identifier}",
        "kind": kind,
        "panel_ref": panel_ref,
        "id": identifier,
        "expected": copy.deepcopy(expected),
        **extra,
    }


def _route_states(specs: list[InstallSpec], artifacts: dict) -> list[tuple[list[InstallSpec], dict]]:
    """Собирает действующие и сохранённые направления установки."""
    result = [([item.cascade_specs()[0] for item in specs], artifacts)]
    children = [*(artifacts.get("cascade_routes") or {}).items()]
    children.extend((item.get("exit_install_id"), item) for item in artifacts.get("retired_cascade_routes", []))
    for identifier, child in children:
        if not isinstance(child, dict):
            raise _blocked("Некорректные артефакты направления каскада")
        choices = [route for item in specs for route in item.cascade_specs()[1:] if route.exit_install_id == identifier]
        if isinstance(child.get("params"), dict):
            try:
                saved = InstallSpec.from_dict(child["params"])
            except (ValueError, TypeError) as error:
                raise _blocked("Некорректные сохранённые параметры направления") from error
            if saved.exit_install_id != identifier or saved.address != specs[0].address or saved.role != specs[0].role:
                raise _blocked("Сохранённое направление принадлежит другой установке")
            choices.append(saved)
        if not choices and any(child.get(key) for key in ("host_uuid", "inbound_uuid", "cascade", "haproxy")):
            raise _blocked("Не сохранены параметры собственного направления каскада")
        if choices:
            result.append((choices, child))
    return result


def _host_plan(choices: list[InstallSpec], scoped: dict, inventory: dict) -> tuple[list[dict], list[str]]:
    """Доказывает принадлежность одного хоста направления."""
    if scoped.get("host_removed"):
        if scoped.get("host_uuid") in inventory["hosts"]:
            raise _blocked("Удалённый хост направления появился снова")
        return [], []
    bodies = [InstallerPanel._host_body(item, scoped) for item in choices]
    host_id = _uuid(scoped["host_uuid"], "хост") if scoped.get("host_uuid") else None
    if host_id:
        host = inventory["hosts"].get(host_id)
        bodies.extend(InstallerPanel._update_intents(scoped, "host", host_id))
    else:
        candidates = [
            item
            for item in inventory["hosts"].values()
            if any(item.get("address") == body["address"] and item.get("port") == body["port"] for body in bodies)
        ]
        if len(candidates) > 1:
            raise _blocked("Найдены несколько хостов одного направления")
        host = candidates[0] if candidates else None
        host_id = host.get("uuid") if host else None
    if not host:
        return [], []
    approved = [body for body in bodies if _matches(host, body)]
    if not approved or host.get("nodes", []):
        raise _blocked("Параметры или связи хоста изменены вне установщика")
    return [_entry("host", choices[0].panel_ref, host_id, {**approved[0], "uuid": host_id})], [host_id]


def _main_plan(spec: InstallSpec, artifacts: dict, inventory: dict) -> tuple[list[dict], dict]:
    """Доказывает принадлежность профиля, ноды и хостов."""
    specs = _specs(spec, artifacts)
    states = _route_states(specs, artifacts)
    install_id = artifacts.get("install_id")
    explicit = any(
        child.get(key) for _, child in states for key in ("profile_uuid", "node_uuid", "host_uuid", "inbound_uuid")
    )
    if not isinstance(install_id, str) or not re.fullmatch(r"[a-fA-F0-9-]{32,36}", install_id):
        if explicit:
            raise _blocked("Не сохранён идентификатор владельца объектов панели")
        return [], {}
    if spec.role == "haproxy_entry":
        if any(
            child.get(key) for _, child in states for key in ("profile_uuid", "inbound_uuid", "node_uuid", "squad_uuid")
        ):
            raise _blocked("Вход HAProxy не должен владеть объектами выходной ноды")
        entries = []
        for choices, child in states:
            if child.get("haproxy") or child.get("host_uuid"):
                owned, _ = _host_plan(choices, child, inventory)
                entries.extend(owned)
        return entries, {}
    configs = _configs(specs, artifacts)
    profiles = inventory["profiles"]
    identifier = artifacts.get("profile_uuid")
    if identifier:
        identifier = _uuid(identifier, "профиль")
        profile = profiles.get(identifier)
    else:
        candidates = []
        for item in profiles.values():
            inbounds = item.get("inbounds")
            if not isinstance(inbounds, list):
                raise _blocked("Панель не вернула инбаунды профиля")
            if any(
                isinstance(inbound, dict)
                and isinstance(inbound.get("tag"), str)
                and inbound["tag"].endswith("_" + install_id[:10])
                for inbound in inbounds
            ):
                candidates.append(item)
        if len(candidates) > 1:
            raise _blocked("Найдены несколько профилей с отметкой этой установки")
        profile = candidates[0] if candidates else None
        identifier = profile.get("uuid") if profile else None
        if profile is None and any(
            item.get("name") in {candidate.profile_name for candidate in specs} for item in profiles.values()
        ):
            raise _blocked("Профиль с указанным именем найден без отметки владельца установки")
    entries = []
    inbound_ids = []
    inbound_tags = {}
    if profile:
        names = {item.profile_name for item in specs}
        names.update(item.get("name") for item in InstallerPanel._update_intents(artifacts, "profile", identifier))
        metadata_ids = _ids(profile.get("inbounds"), "инбаунды профиля")
        configured = profile.get("config", {}).get("inbounds")
        if not isinstance(configured, list) or not configured or len(configured) != len(metadata_ids):
            raise _blocked("Состав собственного профиля изменён вне установщика")
        configured_tags = [item.get("tag") for item in configured if isinstance(item, dict)]
        saved_tags = {}
        for _, child in states:
            if child.get("inbound_tag"):
                saved_tags.setdefault(child["inbound_tag"], set())
                if child.get("inbound_uuid"):
                    saved_tags[child["inbound_tag"]].add(child["inbound_uuid"])
        for inbound in profile["inbounds"]:
            tag = inbound.get("tag")
            if (
                not isinstance(tag, str)
                or not tag.endswith("_" + install_id[:10])
                or tag not in configured_tags
                or inbound.get("profileUuid") != identifier
                or (saved_tags and tag not in saved_tags)
                or (saved_tags.get(tag) and inbound["uuid"] not in saved_tags[tag])
            ):
                raise _blocked("Принадлежность или конфигурация профиля не подтверждена")
            inbound_ids.append(inbound["uuid"])
            inbound_tags[inbound["uuid"]] = tag
        if (
            len(set(configured_tags)) != len(configured)
            or set(configured_tags) != set(inbound_tags.values())
            or profile.get("name") not in names
            or profile.get("config") not in configs
        ):
            raise _blocked("Принадлежность или конфигурация профиля не подтверждена")
        entries.append(
            _entry(
                "profile",
                spec.panel_ref,
                identifier,
                {"uuid": identifier, "name": profile["name"], "config": profile["config"]},
                inbound_uuid=inbound_ids[0],
                inbound_tag=inbound_tags[inbound_ids[0]],
                inbound_tags=inbound_tags,
            )
        )
    elif not identifier and explicit:
        raise _blocked("Нельзя определить собственный профиль панели")
    node_id = artifacts.get("node_uuid")
    if node_id:
        node_id = _uuid(node_id, "нода")
        node = inventory["nodes"].get(node_id)
    else:
        candidates = [
            item
            for item in inventory["nodes"].values()
            if identifier and (item.get("configProfile") or {}).get("activeConfigProfileUuid") == identifier
        ]
        if len(candidates) > 1:
            raise _blocked("Собственный профиль используется несколькими нодами")
        node = candidates[0] if candidates else None
        node_id = node.get("uuid") if node else None
    if node:
        choices = [{"name": item.name, "address": item.address, "port": item.node_port} for item in specs]
        choices.extend(InstallerPanel._update_intents(artifacts, "node", node_id))
        binding = node.get("configProfile") or {}
        active_ids = _ids(binding.get("activeInbounds"), "инбаунды ноды")
        known_ids = set(inbound_ids) | {child.get("inbound_uuid") for _, child in states if child.get("inbound_uuid")}
        if (
            not any(_matches(node, choice) for choice in choices)
            or binding.get("activeConfigProfileUuid") != identifier
            or not active_ids
            or any(item not in known_ids for item in active_ids)
        ):
            raise _blocked("Параметры ноды изменены вне установщика")
        entries.append(_entry("node", spec.panel_ref, node_id, _node_snapshot(node)))
    host_ids = []
    for choices, child in states:
        scoped = {"profile_uuid": identifier, **child}
        if not scoped.get("inbound_uuid"):
            recovered = [identifier for identifier, tag in inbound_tags.items() if tag == child.get("inbound_tag")]
            if len(recovered) == 1:
                scoped["inbound_uuid"] = recovered[0]
        if scoped.get("inbound_uuid") or scoped.get("host_uuid"):
            owned, ids = _host_plan(choices, scoped, inventory)
            entries.extend(owned)
            host_ids.extend(ids)
    allowed_squads = {item.squad_uuid for choices, _ in states for item in choices if item.squad_uuid}
    allowed_squads.update(child["squad_uuid"] for _, child in states if child.get("squad_uuid"))
    detach_ids = set(inbound_ids) | {child["inbound_uuid"] for _, child in states if child.get("inbound_uuid")}
    for squad in inventory["squads"].values():
        ids = _ids(squad.get("inbounds"), "инбаунды сквада")
        owned = [item for item in ids if item in detach_ids]
        if not owned:
            continue
        if squad["uuid"] not in allowed_squads:
            raise _blocked("Инбаунд используется дополнительным сквадом вне установщика")
        entries.append(
            _entry(
                "detach",
                spec.panel_ref,
                squad["uuid"],
                {},
                before=ids,
                after=[item for item in ids if item not in detach_ids],
                inbound_uuid=owned[0],
            )
        )
    metadata = {
        "panel_ref": spec.panel_ref,
        "profile_uuid": identifier,
        "inbound_uuid": artifacts.get("inbound_uuid") or (inbound_ids[0] if inbound_ids else None),
        "inbound_uuids": inbound_ids,
        "node_uuid": node_id,
        "host_uuid": artifacts.get("host_uuid"),
        "host_uuids": host_ids,
        "allowed_squads": sorted(allowed_squads),
    }
    return entries, metadata


def _cascade_context(cascade: dict, artifacts: dict) -> dict:
    """Проверяет сохранённое происхождение приватного каскада."""
    nonce = cascade.get("nonce")
    if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{22}", nonce):
        raise _blocked("Не сохранена отметка владельца технического каскада")
    context = (artifacts.get("cleanup_cascade_contexts") or {}).get(nonce) or {}
    values = {key: cascade.get(key) or context.get(key) for key in ("panel_ref", "exit_install_id", "inbound_uuid")}
    if (
        not isinstance(values["panel_ref"], str)
        or not values["panel_ref"]
        or not isinstance(values["exit_install_id"], str)
        or not re.fullmatch(r"[a-fA-F0-9-]{32,36}", values["exit_install_id"])
    ):
        raise _blocked("Панель выхода старого каскада не установлена однозначно")
    values["inbound_uuid"] = _uuid(values["inbound_uuid"], "инбаунд выхода каскада")
    for key in values:
        if cascade.get(key) and context.get(key) and cascade[key] != context[key]:
            raise _blocked("Сведения о владельце каскада противоречат сохранённой установке")
    return values


async def _cascade_plan(panel: InstallerPanel, cascade: dict, context: dict, inventory: dict) -> list[dict]:
    """Проверяет пользователя и приватный сквад на выходной панели."""
    name = "cascade_" + cascade["nonce"]
    credential = _uuid(cascade.get("uuid"), "VLESS технического каскада")
    users = [
        item for item in await _users(panel) if item.get("vlessUuid") == credential or item.get("username") == name
    ]
    if len(users) > 1:
        raise _blocked("Технический пользователь каскада не определён однозначно")
    user = users[0] if users else None
    user_id = cascade.get("user_id")
    if user_id is not None and (type(user_id) is not int or user_id < 1):
        raise _blocked("Некорректный идентификатор технического пользователя")
    if user_id is not None:
        saved = await panel._call("GET", f"/users/{user_id}", missing=True)
        if saved is not None and (not user or saved.get("id") != user.get("id")):
            raise _blocked("Идентификатор технического пользователя относится к другому объекту")
        if saved is None and user is not None:
            raise _blocked("Технический пользователь был заменён вне установщика")
    squad_id = cascade.get("squad_uuid")
    if squad_id:
        squad_id = _uuid(squad_id, "приватный сквад")
        squad = inventory["squads"].get(squad_id)
    else:
        matches = [item for item in inventory["squads"].values() if item.get("name") == name]
        if len(matches) > 1:
            raise _blocked("Приватный сквад каскада не определён однозначно")
        squad = matches[0] if matches else None
        squad_id = squad.get("uuid") if squad else None
        if user:
            memberships = _ids(user.get("activeInternalSquads"), "сквады технического пользователя")
            if len(memberships) == 1 and memberships[0] != squad_id:
                raise _blocked("Приватный сквад технического пользователя переименован или заменён")
    entries = []
    if user:
        memberships = _ids(user.get("activeInternalSquads"), "сквады технического пользователя")
        expected = {
            "id": user["id"],
            "username": name,
            "vlessUuid": credential,
            "status": "ACTIVE",
            "trafficLimitBytes": 0,
            "trafficLimitStrategy": "NO_RESET",
            "description": "Remnawave node installer: private cascade",
        }
        if (
            not squad
            or not _matches(user, expected)
            or memberships != [squad_id]
            or user.get("externalSquadUuid")
            or user.get("expireAt") not in {"2099-01-01T00:00:00.000Z", "2099-01-01T00:00:00Z"}
        ):
            raise _blocked("Технический пользователь изменён или его доступ расширен вне установщика")
        entries.append(
            _entry(
                "user",
                context["panel_ref"],
                user["id"],
                expected,
                squad_uuid=squad_id,
                expire_at=user["expireAt"],
            )
        )
    if squad:
        ids = _ids(squad.get("inbounds"), "инбаунды приватного сквада")
        if squad.get("name") != name or ids != _private_inbounds(context, inventory):
            raise _blocked("Приватный сквад каскада изменён вне установщика")
        members = (squad.get("info") or {}).get("membersCount")
        if type(members) is not int or members != int(user is not None):
            raise _blocked("Приватным сквадом пользуется другой пользователь")
        entries.append(
            _entry(
                "private_squad",
                context["panel_ref"],
                squad_id,
                {"uuid": squad_id, "name": name},
                inbound_uuid=context["inbound_uuid"],
                exit_install_id=context["exit_install_id"],
                user_id=user["id"] if user else None,
            )
        )
    return entries


def _private_inbounds(context: dict, inventory: dict) -> list[str]:
    """Подтверждает инбаунд выхода или его полное отсутствие."""
    matches = [
        item
        for profile in inventory["profiles"].values()
        for item in profile["inbounds"]
        if item["uuid"] == context["inbound_uuid"]
    ]
    if not matches:
        return []
    if len(matches) != 1 or not matches[0]["tag"].endswith("_" + context["exit_install_id"][:10]):
        raise _blocked("Принадлежность выходного инбаунда каскада не подтверждена")
    return [context["inbound_uuid"]]


def _references(plan: dict, inventories: dict) -> None:
    """Запрещает затрагивать объекты с чужими связями."""
    main = plan["main"]
    if not main:
        return
    inventory = inventories[main["panel_ref"]]
    profile_id = main["profile_uuid"]
    inbound_ids = set(main.get("inbound_uuids", [main["inbound_uuid"]]))
    node_id = main["node_uuid"]
    host_ids = set(main.get("host_uuids", [main["host_uuid"]]))
    for node in inventory["nodes"].values():
        profile = node.get("configProfile")
        if not isinstance(profile, dict):
            raise _blocked("Панель не вернула привязки ноды к профилю")
        ids = _ids(profile.get("activeInbounds"), "инбаунды ноды")
        if node.get("uuid") != node_id and (
            (profile_id and profile.get("activeConfigProfileUuid") == profile_id) or bool(inbound_ids.intersection(ids))
        ):
            raise _blocked("Собственный профиль используется чужой нодой")
    for host in inventory["hosts"].values():
        binding = host.get("inbound")
        if not isinstance(binding, dict) or not isinstance(host.get("nodes", []), list):
            raise _blocked("Панель не вернула привязки хоста")
        if host.get("uuid") not in host_ids and (
            (profile_id and binding.get("configProfileUuid") == profile_id)
            or binding.get("configProfileInboundUuid") in inbound_ids
            or (node_id and node_id in host.get("nodes", []))
        ):
            raise _blocked("Нода или инбаунд используются чужим хостом")
    for squad in inventory["squads"].values():
        ids = _ids(squad.get("inbounds"), "инбаунды сквада")
        if inbound_ids.intersection(ids) and squad["uuid"] not in main["allowed_squads"]:
            raise _blocked("Инбаунд добавлен в другой сквад вне установщика")
    profile = inventory["profiles"].get(profile_id)
    if profile and any(item != node_id for item in _ids(profile.get("nodes"), "ноды профиля")):
        raise _blocked("Профиль связан с дополнительной нодой")


def _node_snapshot(node: dict) -> dict:
    """Выделяет устойчивые управляемые параметры ноды."""
    profile = node.get("configProfile") or {}
    return {
        "uuid": node.get("uuid"),
        "name": node.get("name"),
        "address": node.get("address"),
        "port": node.get("port"),
        "configProfile": {
            "activeConfigProfileUuid": profile.get("activeConfigProfileUuid"),
            "activeInbounds": [
                {"uuid": identifier} for identifier in sorted(_ids(profile.get("activeInbounds"), "инбаунды ноды"))
            ],
        },
    }


async def _validate(plan: dict, panels: dict) -> dict:
    """Проверяет весь план до следующей необратимой операции."""
    inventories = {key: await _inventory(panel) for key, panel in panels.items()}
    _references(plan, inventories)
    found = {}
    collections = {
        "host": "hosts",
        "node": "nodes",
        "profile": "profiles",
        "detach": "squads",
        "private_squad": "squads",
    }
    for entry in plan["steps"]:
        panel = panels[entry["panel_ref"]]
        kind = entry["kind"]
        if kind == "user":
            actual = await panel._call("GET", f"/users/{entry['id']}", missing=True)
        else:
            actual = inventories[entry["panel_ref"]][collections[kind]].get(entry["id"])
        found[entry["key"]] = actual
        if actual is None:
            continue
        if entry["key"] in plan["done"] and kind != "detach":
            raise _blocked("Удалённый объект появился снова; повторное удаление запрещено")
        if kind == "node":
            valid = _node_snapshot(actual) == entry["expected"]
        else:
            valid = _matches(actual, entry["expected"])
        if not valid:
            raise _blocked("Сохранённый объект изменён вне установщика: " + kind)
        if kind == "profile":
            ids = _ids(actual.get("inbounds"), "инбаунды профиля")
            tags = entry.get("inbound_tags", {entry["inbound_uuid"]: entry["inbound_tag"]})
            if set(ids) != set(tags) or any(item.get("tag") != tags.get(item["uuid"]) for item in actual["inbounds"]):
                raise _blocked("Состав удаляемого профиля изменён вне установщика")
        elif kind == "host" and actual.get("nodes", []):
            raise _blocked("Связи удаляемого хоста изменены вне установщика")
        elif kind == "detach":
            current = _ids(actual.get("inbounds"), "инбаунды сквада")
            allowed = (entry["after"],) if entry["key"] in plan["done"] else (entry["before"], entry["after"])
            if current not in allowed:
                raise _blocked("Состав общего сквада изменился после подготовки удаления")
        elif kind == "user":
            if (
                _ids(actual.get("activeInternalSquads"), "сквады пользователя") != [entry["squad_uuid"]]
                or actual.get("externalSquadUuid")
                or actual.get("expireAt") != entry["expire_at"]
            ):
                raise _blocked("Доступ технического пользователя изменён вне установщика")
        elif kind == "private_squad":
            ids = _ids(actual.get("inbounds"), "инбаунды приватного сквада")
            user_id = entry["user_id"]
            user = await panel._call("GET", f"/users/{user_id}", missing=True) if user_id else None
            members = (actual.get("info") or {}).get("membersCount")
            expected_ids = _private_inbounds(entry, inventories[entry["panel_ref"]])
            if ids != expected_ids or type(members) is not int or members != int(user is not None):
                raise _blocked("Приватный сквад изменён или используется другим пользователем")
    return found


async def _delete(panel: InstallerPanel, path: str) -> None:
    """Удаляет объект по контракту HTTP 204 и проверяет отсутствие."""
    try:
        response = await panel.api._request("DELETE", path)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        raise InstallError("panel_cleanup", "Ответ удаления не получен; повторите очистку записи") from error
    if response.status_code not in {204, 404}:
        raise InstallError("panel_cleanup", f"Панель отклонила удаление: HTTP {response.status_code}")
    if await panel._call("GET", path, missing=True) is not None:
        raise InstallError("panel_cleanup", "Панель не подтвердила удаление объекта")


async def cleanup_panel(
    spec: InstallSpec,
    artifacts: dict,
    checkpoint: Checkpoint,
    log,
    *,
    preflight_only: bool = False,
    cascade_only: bool = False,
) -> None:
    """Удаляет только доказанно собственные объекты панели."""
    cascades = [item for item in [artifacts.get("cascade"), *artifacts.get("retained_cascades", [])] if item]
    children = [*(artifacts.get("cascade_routes") or {}).values()]
    children.extend(
        child
        for child in artifacts.get("retired_cascade_routes", [])
        if not (child.get("host_removed") and (child.get("panel_cleanup") or {}).get("completed"))
    )
    for child in children:
        cascades.extend(item for item in [child.get("cascade"), *child.get("retained_cascades", [])] if item)
    if any(not isinstance(item, dict) for item in cascades):
        raise _blocked("Некорректная история технических каскадов")
    contexts = [_cascade_context(item, artifacts) for item in cascades]
    refs = {spec.panel_ref, *(item["panel_ref"] for item in contexts)}
    saved = artifacts.get("panel_cleanup")
    if saved:
        if (
            not isinstance(saved, dict)
            or saved.get("version") != PANEL_VERSION
            or saved.get("install_id") != artifacts.get("install_id")
            or saved.get("panel_ref") != spec.panel_ref
            or bool(saved.get("cascade_only")) != cascade_only
            or not isinstance(saved.get("steps"), list)
            or not isinstance(saved.get("done"), list)
        ):
            raise _blocked("Сохранённый план удаления не соответствует установке")
        refs.update(item["panel_ref"] for item in saved["steps"])
    async with AsyncExitStack() as stack:
        panels = {}
        for ref in sorted(refs):
            panel = await stack.enter_async_context(InstallerPanel(InstallSpec(panel_ref=ref)))
            await panel.ensure_version()
            panels[ref] = panel
        if saved is None:
            inventories = {key: await _inventory(panel) for key, panel in panels.items()}
            entries, main = ([], {}) if cascade_only else _main_plan(spec, artifacts, inventories[spec.panel_ref])
            for cascade, context in zip(cascades, contexts, strict=True):
                entries.extend(
                    await _cascade_plan(
                        panels[context["panel_ref"]], cascade, context, inventories[context["panel_ref"]]
                    )
                )
            ordered = {"host": 0, "node": 1, "detach": 2, "profile": 3, "user": 4, "private_squad": 5}
            unique = {}
            for entry in entries:
                if entry["key"] in unique and unique[entry["key"]] != entry:
                    raise _blocked("Объект встречается в противоречащих планах удаления")
                unique[entry["key"]] = entry
            saved = {
                "version": PANEL_VERSION,
                "install_id": artifacts.get("install_id"),
                "panel_ref": spec.panel_ref,
                "cascade_only": cascade_only,
                "main": main,
                "steps": sorted(unique.values(), key=lambda item: (ordered[item["kind"]], item["key"])),
                "done": [],
            }
            _references(saved, inventories)
            artifacts["panel_cleanup"] = saved
            await checkpoint(artifacts)
        locks = sorted({
            (str(getattr(panels[item["panel_ref"]].api, "base_url", item["panel_ref"])).rstrip("/"), item["id"])
            for item in saved["steps"]
            if item["kind"] in {"detach", "private_squad"}
        })
        for key in locks:
            await stack.enter_async_context(_SQUAD_LOCKS.setdefault(key, asyncio.Lock()))
        await _validate(saved, panels)
        if preflight_only:
            return
        paths = {
            "host": "hosts",
            "node": "nodes",
            "profile": "config-profiles",
            "user": "users",
            "private_squad": "internal-squads",
        }
        for entry in saved["steps"]:
            found = await _validate(saved, panels)
            actual = found[entry["key"]]
            if entry["key"] in saved["done"]:
                if actual is not None and entry["kind"] != "detach":
                    raise _blocked("Удалённый объект появился снова; повторное удаление запрещено")
                continue
            kind = entry["kind"]
            await log("delete:panel_" + kind)
            panel = panels[entry["panel_ref"]]
            if actual is not None:
                if kind == "detach":
                    ids = _ids(actual.get("inbounds"), "инбаунды общего сквада")
                    if ids != entry["after"]:
                        await panel._call(
                            "PATCH", "/internal-squads", {"uuid": entry["id"], "inbounds": entry["after"]}
                        )
                    confirmed = await panel._call(
                        "GET", f"/internal-squads/{quote(entry['id'], safe='')}", missing=True
                    )
                    if (
                        confirmed is not None
                        and _ids(confirmed.get("inbounds"), "инбаунды общего сквада") != entry["after"]
                    ):
                        raise InstallError("panel_cleanup", "Удаление своего инбаунда из сквада не подтверждено")
                else:
                    await _delete(panel, f"/{paths[kind]}/{entry['id']}")
            saved["done"].append(entry["key"])
            await checkpoint(artifacts)
        await _validate(saved, panels)
        saved["completed"] = True
        await checkpoint(artifacts)

from __future__ import annotations

import asyncio
import re
import uuid

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from urllib.parse import quote

from .panel import _uuid
from .panel_cleanup import _delete
from .types import InstallError


if TYPE_CHECKING:
    from .panel import Checkpoint, InstallerPanel


_QUOTA = 10 * 1024 * 1024


def _blocked(message: str) -> InstallError:
    """Останавливает проверку без изменения чужого доступа."""
    return InstallError("chain_probe", message, retryable=False)


def _expiry(value: object) -> datetime:
    """Проверяет срок действия технического доступа."""
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else None
    except ValueError:
        result = None
    if result is None or result.tzinfo is None:
        raise _blocked("Не подтверждён срок действия проверочного доступа")
    return result.astimezone(timezone.utc)


def _state(panel: InstallerPanel, inbound_uuid: str, artifacts: dict) -> dict | None:
    """Проверяет принадлежность сохранённого проверочного доступа."""
    install_id = artifacts.get("install_id")
    if not isinstance(install_id, str) or re.fullmatch(r"[a-f0-9]{32}", install_id) is None:
        raise _blocked("Не сохранён идентификатор проверяемой установки")
    _uuid(inbound_uuid, "проверяемый инбаунд")
    state = artifacts.get("chain_probe")
    if state is None:
        return None
    if (
        not isinstance(state, dict)
        or state.get("install_id") != install_id
        or state.get("panel_ref") != panel.spec.panel_ref
        or not isinstance(state.get("nonce"), str)
        or re.fullmatch(r"[a-f0-9]{22}", state["nonce"]) is None
    ):
        raise _blocked("Проверочный доступ принадлежит другой установке или панели")
    _uuid(state.get("inbound_uuid"), "прежний проверяемый инбаунд")
    _uuid(state.get("uuid"), "проверочный VLESS")
    _expiry(state.get("expire_at"))
    if state.get("squad_uuid") is not None:
        _uuid(state["squad_uuid"], "проверочный сквад")
    if state.get("user_id") is not None and (type(state["user_id"]) is not int or state["user_id"] < 1):
        raise _blocked("Не подтверждён идентификатор проверочного пользователя")
    return state


def _name(state: dict) -> str:
    """Возвращает уникальное имя проверочного доступа."""
    return "probe_" + state["nonce"]


def _description(state: dict) -> str:
    """Связывает технического пользователя с установкой."""
    return "Solo_bot chain probe " + state["install_id"] + ":" + state["nonce"]


def _memberships(value: object) -> list[str]:
    """Проверяет точный список привязок технического объекта."""
    if not isinstance(value, list):
        raise _blocked("Панель не подтвердила привязки проверочного доступа")
    result = [_uuid(item.get("uuid") if isinstance(item, dict) else item, "привязка проверки") for item in value]
    if len(result) != len(set(result)):
        raise _blocked("Панель вернула повторяющиеся привязки проверочного доступа")
    return result


async def _squad(panel: InstallerPanel, state: dict) -> dict | None:
    """Находит только собственный изолированный проверочный сквад."""
    matches = [item for item in await panel.list_squads() if item.get("name") == _name(state)]
    if len(matches) > 1:
        raise _blocked("Проверочный сквад не определён однозначно")
    identifier = state.get("squad_uuid") or (matches[0].get("uuid") if matches else None)
    if identifier is None:
        return None
    identifier = _uuid(identifier, "проверочный сквад")
    found = await panel._call("GET", f"/internal-squads/{identifier}", missing=True)
    if found is None:
        if matches:
            raise _blocked("Проверочный сквад заменён вне установщика")
        return None
    if (
        not isinstance(found, dict)
        or found.get("uuid") != identifier
        or found.get("name") != _name(state)
        or len(matches) != 1
        or matches[0].get("uuid") != identifier
        or _memberships(found.get("inbounds")) != [state["inbound_uuid"]]
    ):
        raise _blocked("Проверочный сквад изменён или его доступ расширен вне установщика")
    return found


async def _user(panel: InstallerPanel, state: dict) -> dict | None:
    """Находит проверочного пользователя по имени и сохранённому ID."""
    found = await panel._call("GET", "/users/by-username/" + quote(_name(state), safe=""), missing=True)
    if state.get("user_id") is not None:
        saved = await panel._call("GET", f"/users/{state['user_id']}", missing=True)
        if (
            (saved is None) != (found is None)
            or saved is not None
            and (not isinstance(saved, dict) or not isinstance(found, dict) or saved.get("id") != found.get("id"))
        ):
            raise _blocked("Проверочный пользователь переименован или заменён вне установщика")
        found = saved
    if found is None:
        return None
    if (
        not isinstance(found, dict)
        or type(found.get("id")) is not int
        or found["id"] < 1
        or state.get("user_id") not in {None, found["id"]}
        or found.get("username") != _name(state)
        or found.get("vlessUuid") != state["uuid"]
        or found.get("description") != _description(state)
        or type(found.get("trafficLimitBytes")) is not int
        or found["trafficLimitBytes"] != _QUOTA
        or found.get("trafficLimitStrategy") != "NO_RESET"
        or _memberships(found.get("activeInternalSquads")) != [state.get("squad_uuid")]
        or found.get("externalSquadUuid")
        or _expiry(found.get("expireAt")) != _expiry(state["expire_at"])
    ):
        raise _blocked("Проверочный пользователь изменён или его доступ расширен вне установщика")
    return found


def _member_count(squad: dict | None, expected: int) -> None:
    """Запрещает удалять сквад с посторонними пользователями."""
    if squad is not None:
        info = squad.get("info")
        count = info.get("membersCount") if isinstance(info, dict) else None
        if type(count) is not int or count != expected:
            raise _blocked("Приватным проверочным сквадом пользуется другой пользователь")


async def _cleanup(panel: InstallerPanel, state: dict, artifacts: dict, checkpoint: Checkpoint | None) -> None:
    """Удаляет только подтверждённые проверочные объекты."""
    squad = await _squad(panel, state)
    if squad is not None:
        state["squad_uuid"] = squad["uuid"]
    user = await _user(panel, state)
    _member_count(squad, int(user is not None))
    if user is not None:
        if squad is None:
            raise _blocked("Не подтверждён приватный сквад проверочного пользователя")
        state["user_id"] = user["id"]
    await panel._save(artifacts, checkpoint)
    if user is not None:
        await _delete(panel, f"/users/{user['id']}")
    squad = await _squad(panel, state)
    _member_count(squad, 0)
    if squad is not None:
        await _delete(panel, f"/internal-squads/{squad['uuid']}")
    state["cleaned"] = True
    await panel._save(artifacts, checkpoint)


async def _create(panel: InstallerPanel, state: dict, artifacts: dict, checkpoint: Checkpoint | None) -> None:
    """Создаёт ограниченный доступ к одному инбаунду."""
    if await _squad(panel, state) is not None or await _user(panel, state) is not None:
        raise _blocked("Имя нового проверочного доступа уже используется")
    await panel._save(artifacts, checkpoint)
    await panel._call("POST", "/internal-squads", {"name": _name(state), "inbounds": [state["inbound_uuid"]]})
    squad = await _squad(panel, state)
    if squad is None:
        raise _blocked("Панель не подтвердила создание проверочного сквада")
    _member_count(squad, 0)
    state["squad_uuid"] = squad["uuid"]
    await panel._save(artifacts, checkpoint)
    await panel._call(
        "POST",
        "/users",
        {
            "username": _name(state),
            "vlessUuid": state["uuid"],
            "expireAt": state["expire_at"],
            "status": "ACTIVE",
            "trafficLimitBytes": _QUOTA,
            "trafficLimitStrategy": "NO_RESET",
            "activeInternalSquads": [state["squad_uuid"]],
            "description": _description(state),
        },
    )
    user = await _user(panel, state)
    if user is None or user.get("status") != "ACTIVE" or _expiry(state["expire_at"]) <= datetime.now(timezone.utc):
        raise _blocked("Панель не подтвердила готовность проверочного пользователя")
    state["user_id"] = user["id"]
    await panel._save(artifacts, checkpoint)


@asynccontextmanager
async def managed_probe_access(
    panel: InstallerPanel, inbound_uuid: str, artifacts: dict, checkpoint: Checkpoint | None = None
) -> AsyncIterator[dict[str, str]]:
    """Выдаёт временный доступ и подтверждает его очистку."""
    previous = _state(panel, inbound_uuid, artifacts)
    await panel._ready()
    if previous is not None:
        await _cleanup(panel, previous, artifacts, checkpoint)
    state = {
        "install_id": artifacts["install_id"],
        "panel_ref": panel.spec.panel_ref,
        "inbound_uuid": inbound_uuid,
        "nonce": uuid.uuid4().hex[:22],
        "uuid": str(uuid.uuid4()),
        "expire_at": (datetime.now(timezone.utc) + timedelta(minutes=15))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
    }
    artifacts["chain_probe"] = state
    await panel._save(artifacts, checkpoint)
    try:
        await _create(panel, state, artifacts, checkpoint)
        yield {"uuid": state["uuid"]}
    finally:
        cleanup = asyncio.create_task(_cleanup(panel, state, artifacts, checkpoint))
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await asyncio.shield(cleanup)
            raise

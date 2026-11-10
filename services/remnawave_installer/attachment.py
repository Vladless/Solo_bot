import asyncio
import copy
import re
import uuid

from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import select

from .types import AccessRecoveryError, InstallError, InstallSpec


def public_attachment(artifacts: dict) -> dict:
    """Возвращает безопасные параметры автоматического подключения."""
    plan = artifacts.get("extra_exit_attachment") or {}
    return {
        key: copy.deepcopy(plan[key])
        for key in ("parent_id", "entry_address", "client_port", "status", "error", "token")
        if key in plan
    }


def attachment_child_id(artifacts: dict) -> str | None:
    """Находит связанный выход в текущем или завершённом изменении."""
    edit = artifacts.get("edit") or artifacts.get("last_edit") or {}
    value = edit.get("extra_exit_child")
    return value if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{32}", value) else None


async def synchronize_attachment(session, parent, child_id, core):
    """Сохраняет итог входа в карточке нового выхода той же транзакцией."""
    jobs = SimpleNamespace(**core)

    if parent.status not in {"succeeded", "warning", "failed", "paused", "canceled"}:
        return
    child = (
        await session.execute(select(jobs.RemnawaveInstall).where(jobs.RemnawaveInstall.id == child_id))
    ).scalar_one_or_none()
    if child is None:
        return
    if child.owner_id != parent.owner_id:
        raise InstallError("permission", "Связанный выход принадлежит другому администратору", retryable=False)
    artifacts = jobs.vault.open(child.id, child.encrypted_artifacts)
    plan = artifacts.get("extra_exit_attachment") or {}
    if plan.get("parent_id") != parent.id or plan.get("status") not in {"queued", "failed"}:
        return
    spec = InstallSpec.from_dict(parent.params)
    matching = any(
        route.exit_install_id == child.id and route.client_port == plan.get("client_port")
        for route in spec.cascade_specs()
    )
    if parent.status in {"succeeded", "warning"} and matching:
        plan.update(status="succeeded")
        plan.pop("error", None)
    elif parent.status in {"failed", "paused", "canceled"}:
        plan.update(status="failed", error=parent.error or "Откройте карточку входной ноды")
    else:
        return
    child.encrypted_artifacts = jobs.vault.seal(child.id, artifacts)
    child.updated_at = datetime.utcnow()


async def _rows(session, child, parent_id, core):
    """Блокирует вход, новый и прежние выходы в общем порядке."""
    jobs = SimpleNamespace(**core)

    parent = await jobs._row(session, parent_id, child.owner_id)
    identifiers = {child.id, parent_id, *jobs._exit_ids(parent.params)}
    result = await session.execute(
        select(jobs.RemnawaveInstall)
        .where(jobs.RemnawaveInstall.id.in_(identifiers))
        .order_by(jobs.RemnawaveInstall.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    locked = {row.id: row for row in result.scalars().all()}
    if identifiers != set(locked) or any(row.owner_id != child.owner_id for row in locked.values()):
        raise ValueError("Связь с выходами изменилась; откройте сводку нового выхода повторно")
    parent = locked[parent_id]
    if {child.id, parent_id, *jobs._exit_ids(parent.params)} != identifiers:
        raise ValueError("Состав каскада изменился; откройте сводку нового выхода повторно")
    return locked[child.id], parent


def _idle(parent, artifacts, core):
    """Проверяет готовность работающего входа принять направление."""
    jobs = SimpleNamespace(**core)

    jobs._editable(parent)
    if parent.status not in {"succeeded", "warning"} or artifacts.get("edit") or not artifacts.get("installed"):
        raise ValueError("Сначала завершите текущую настройку входной ноды")


async def _access(session, parent, core):
    """Запрашивает SSH входа и прежних выходов до установки."""
    jobs = SimpleNamespace(**core)

    jobs.vault.open(parent.id, parent.encrypted_artifacts)
    secrets = jobs.vault.open(parent.id, parent.encrypted_secrets)
    if (
        not (secrets.get("ssh_password") or secrets.get("ssh_private_key"))
        or parent.secrets_expire_at
        and parent.secrets_expire_at <= datetime.utcnow()
    ):
        raise AccessRecoveryError(parent.id)
    await jobs._require_cascade_access(session, parent, InstallSpec.from_dict(parent.params))


def _port(spec, occupied=()):
    """Выбирает отдельный клиентский порт до подтверждения запуска."""
    occupied = {spec.ssh_port, spec.node_port, *spec.client_ports(), *occupied}
    for port in [444, *range(8443, 64444, 1000), *range(20000, 65536)]:
        if port not in occupied:
            return port
    raise ValueError("Для нового направления не найден свободный клиентский порт")


async def prepare_attachment(job_id: str, owner_id: int, core) -> dict:
    """Сохраняет согласуемый план нового направления без сетевых изменений."""
    jobs = SimpleNamespace(**core)

    await jobs._authorize(owner_id)
    async with jobs.async_session_maker() as session:
        child = await jobs._row(session, job_id, owner_id)
        artifacts = jobs.vault.open(child.id, child.encrypted_artifacts)
        parent_id = artifacts.get("extra_exit_for")
        pending = (
            artifacts.get("installed") and (artifacts.get("extra_exit_attachment") or {}).get("status") == "pending"
        )
        if not parent_id or child.status != "draft" and not pending:
            raise ValueError("Автоматическое подключение выбирается в черновике нового выхода")
        child, parent = await _rows(session, child, parent_id, core)
        artifacts = jobs.vault.open(child.id, child.encrypted_artifacts)
        jobs._editable(child)
        if artifacts.get("edit"):
            raise ValueError("Сначала завершите изменение настроек выходной ноды")
        parent_artifacts = jobs.vault.open(parent.id, parent.encrypted_artifacts)
        _idle(parent, parent_artifacts, core)
        await _access(session, parent, core)
        entry = InstallSpec.from_dict(parent.params)
        if entry.role != "haproxy_entry":
            raise ValueError("Новый выход подключается к существующему входу HAProxy")
        artifacts["extra_exit_attachment"] = {
            "parent_id": parent.id,
            "parent_fingerprint": parent.fingerprint,
            "entry_address": entry.address,
            "client_port": _port(entry),
            "status": "planned",
            "token": uuid.uuid4().hex[:12],
        }
        child.encrypted_artifacts = jobs.vault.seal(child.id, artifacts)
        await session.commit()
        return jobs._view(child)


async def validate_attachment_start(session, child, spec, core, *, bind=False, expected_token=None):
    """Перепроверяет согласованный вход и доступы перед запуском выхода."""
    jobs = SimpleNamespace(**core)

    artifacts = jobs.vault.open(child.id, child.encrypted_artifacts)
    plan = artifacts.get("extra_exit_attachment")
    if not plan:
        return
    if expected_token is not None and plan.get("token") != expected_token:
        raise ValueError("План подключения обновился; подтвердите новый адрес в сводке")
    if plan.get("parent_id") != artifacts.get("extra_exit_for") or plan.get("status") != "planned":
        raise ValueError("План подключения изменился; откройте сводку нового выхода повторно")
    parent = await jobs._row(session, plan["parent_id"], child.owner_id)
    parent_artifacts = jobs.vault.open(parent.id, parent.encrypted_artifacts)
    _idle(parent, parent_artifacts, core)
    entry = InstallSpec.from_dict(parent.params)
    if (
        parent.fingerprint != plan.get("parent_fingerprint")
        or entry.role != "haproxy_entry"
        or entry.address != plan.get("entry_address")
        or type(plan.get("client_port")) is not int
        or plan["client_port"] in {entry.ssh_port, entry.node_port, *entry.client_ports()}
    ):
        raise ValueError("Вход или клиентский порт изменился; откройте добавление нового выхода повторно")
    await _access(session, parent, core)
    if bind:
        plan.update(child_fingerprint=spec.fingerprint(), host_remark=spec.host_remark, status="authorized")
        child.encrypted_artifacts = jobs.vault.seal(child.id, artifacts)


async def require_attachment_token(job_id, owner_id, token, core):
    """Связывает запуск с показанным клиентским адресом в сводке."""
    jobs = SimpleNamespace(**core)
    async with jobs.async_session_maker() as session:
        child = await jobs._row(session, job_id, owner_id)
        artifacts = jobs.vault.open(child.id, child.encrypted_artifacts)
        plan = artifacts.get("extra_exit_attachment") or {}
        if plan and token != plan.get("token"):
            raise ValueError("План подключения обновился; откройте сводку и подтвердите указанный там адрес")


async def validate_attachment_retry(session, child, artifacts, core):
    """Запрашивает доступ входа до повтора отложенного подключения."""
    jobs = SimpleNamespace(**core)

    plan = artifacts.get("extra_exit_attachment") or {}
    if plan.get("status") != "pending" or not artifacts.get("installed"):
        return False
    parent = await jobs._row(session, plan["parent_id"], child.owner_id)
    await _access(session, parent, core)
    await _access(session, child, core)
    return True


async def preflight_attachment_start(job_id: str, owner_id: int, core) -> None:
    """Проверяет SSH и фактическую свободу порта входа до установки выхода."""
    from .remote import RemoteServer, preflight

    jobs = SimpleNamespace(**core)
    async with jobs.async_session_maker() as session:
        child = await jobs._row(session, job_id, owner_id)
        artifacts = jobs.vault.open(child.id, child.encrypted_artifacts)
        plan = artifacts.get("extra_exit_attachment")
        if not plan:
            return
        parent = await jobs._row(session, plan["parent_id"], owner_id)
        parent_artifacts = jobs.vault.open(parent.id, parent.encrypted_artifacts)
        spec = InstallSpec.from_dict({
            **parent.params,
            "cascade_routes": [
                *parent.params.get("cascade_routes", []),
                {"exit_install_id": child.id, "client_port": plan["client_port"], "host_remark": "New exit"},
            ],
        })
        spec._owner_id = parent_artifacts.get("remote_owner_id")
        secrets = jobs.vault.open(parent.id, parent.encrypted_secrets)
    async with RemoteServer(spec, secrets) as remote:
        occupied = await remote.run(f"ss -tlnH 'sport = :{plan['client_port']}'", step="preflight")
        if occupied.strip():
            listeners = await remote.run("ss -tlnH", step="preflight")
            ports = {
                int(line.split()[3].rsplit(":", 1)[-1])
                for line in listeners.splitlines()
                if len(line.split()) >= 4 and line.split()[3].rsplit(":", 1)[-1].isdigit()
            }
            replacement = _port(spec, ports)
            async with jobs.async_session_maker() as session:
                child = await jobs._row(session, job_id, owner_id)
                child, parent = await _rows(session, child, plan["parent_id"], core)
                current = jobs.vault.open(child.id, child.encrypted_artifacts)
                if current.get("extra_exit_attachment") != plan or parent.fingerprint != plan["parent_fingerprint"]:
                    raise ValueError("Состояние входа изменилось; обновите план подключения")
                current["extra_exit_attachment"]["client_port"] = replacement
                current["extra_exit_attachment"]["token"] = uuid.uuid4().hex[:12]
                child.encrypted_artifacts = jobs.vault.seal(child.id, current)
                await session.commit()
            raise ValueError(
                f"Порт {plan['client_port']} занят на входе. Предложен порт {replacement}; проверьте сводку и подтвердите запуск повторно"
            )
        await preflight(remote, spec)


async def start_prepared_attachment_retry(job_id: str, owner_id: int, core, token):
    """Подтверждает обновлённый план уже установленного выхода."""
    jobs = SimpleNamespace(**core)
    async with jobs.async_session_maker() as session:
        child = await jobs._row(session, job_id, owner_id)
        artifacts = jobs.vault.open(child.id, child.encrypted_artifacts)
        plan = artifacts.get("extra_exit_attachment") or {}
        if not artifacts.get("installed") or plan.get("status") != "planned":
            return None
        await validate_attachment_start(session, child, InstallSpec.from_dict(child.params), core, expected_token=token)
        await _access(session, child, core)
    await preflight_attachment_start(job_id, owner_id, core)
    async with jobs.async_session_maker() as session:
        child = await jobs._row(session, job_id, owner_id)
        child, _parent = await _rows(session, child, plan["parent_id"], core)
        artifacts = jobs.vault.open(child.id, child.encrypted_artifacts)
        if not artifacts.get("installed") or artifacts.get("extra_exit_attachment") != plan:
            raise ValueError("План подключения изменился; откройте сводку повторно")
        await validate_attachment_start(
            session, child, InstallSpec.from_dict(child.params), core, bind=True, expected_token=token
        )
        child.status, child.stage, child.error, child.retryable = "queued", "attachment", None, True
        child.pause_requested, child.next_retry_at = False, datetime.utcnow()
        await session.commit()
        return jobs._view(child)


async def finish_attachment(job_id: str, token: str, artifacts: dict, values: dict, core) -> None:
    """Атомарно завершает выход и ставит подключение входа в очередь."""
    jobs = SimpleNamespace(**core)
    from .changes import build_change_plan

    async with jobs.async_session_maker() as session:
        child = await jobs._row(session, job_id)
        plan = artifacts["extra_exit_attachment"]
        child, parent = await _rows(session, child, plan["parent_id"], core)
        if child.lease_token != token or child.status == "cancel_requested" or child.pause_requested:
            raise asyncio.CancelledError
        child.status = "warning" if artifacts.get("warnings") else "succeeded"
        child.stage, child.next_retry_at, child.error = "done", None, None
        for key, value in values.items():
            setattr(child, key, value)
        child.encrypted_artifacts = jobs.vault.seal(child.id, {k: v for k, v in artifacts.items() if k != "secrets"})
        await session.flush()
        try:
            parent_artifacts = jobs.vault.open(parent.id, parent.encrypted_artifacts)
            await jobs._guard_extra_exit_configuration(session, child, InstallSpec.from_dict(child.params))
            if plan.get("child_fingerprint") != child.fingerprint:
                raise ValueError("Параметры выхода изменились после подтверждения подключения")
            existing = next(
                (
                    route
                    for route in parent.params.get("cascade_routes", [])
                    if route.get("exit_install_id") == child.id
                ),
                None,
            )
            expected = {
                "exit_install_id": child.id,
                "client_port": plan["client_port"],
                "host_remark": plan["host_remark"],
            }
            edit = parent_artifacts.get("edit") or {}
            if existing == expected and not edit:
                plan.update(status="succeeded")
            elif edit.get("extra_exit_child") == child.id:
                plan.update(status="queued")
            else:
                _idle(parent, parent_artifacts, core)
                if parent.fingerprint != plan.get("parent_fingerprint"):
                    raise ValueError(
                        "Вход изменился после подтверждения; откройте настройки направления во входной ноде"
                    )
                old = InstallSpec.from_dict(parent.params)
                new = InstallSpec.from_dict({
                    **parent.params,
                    "cascade_routes": [*parent.params.get("cascade_routes", []), expected],
                })
                await _access(session, parent, core)
                await jobs._guard_exit_reference(session, parent, new)
                change = build_change_plan(old, new, parent_artifacts)
                parent_artifacts["edit"] = {
                    "id": uuid.uuid4().hex,
                    "before": copy.deepcopy(parent.params),
                    "after": new.to_dict(),
                    "previous_target": None,
                    "changed": change["changed"],
                    "operations": change["operations"],
                    "done": [],
                    "applied_specs": {},
                    "requires_replacement": change["requires_replacement"],
                    "resume_install": False,
                    "extra_exit_child": child.id,
                }
                parent_artifacts.pop("edit_preview", None)
                parent_artifacts.pop("dns_wait", None)
                parent.encrypted_artifacts = jobs.vault.seal(parent.id, parent_artifacts)
                parent.status, parent.stage, parent.error, parent.retryable = "queued", "edit", None, True
                parent.pause_requested, parent.next_retry_at = False, datetime.utcnow()
                plan.update(status="queued")
            plan.pop("error", None)
        except (ValueError, InstallError) as error:
            plan.update(status="pending", error=str(error)[:400])
            child.status, child.stage, child.retryable = "warning", "attachment", True
            child.error = "Выход установлен. Подключение к входу не завершено: " + str(error)[:350]
        child.encrypted_artifacts = jobs.vault.seal(child.id, {k: v for k, v in artifacts.items() if k != "secrets"})
        child.updated_at = datetime.utcnow()
        await session.commit()


async def enrich_attachment(session, view, artifacts, owner_id, core):
    """Показывает итог автоматической настройки входной ноды."""
    jobs = SimpleNamespace(**core)

    plan = artifacts.get("extra_exit_attachment") or {}
    if plan.get("status") != "queued":
        return view
    parent = await jobs._row(session, plan["parent_id"], owner_id)
    parent_artifacts = jobs.vault.open(parent.id, parent.encrypted_artifacts)
    entry = InstallSpec.from_dict(parent.params)
    matching = any(
        route.exit_install_id == view["id"] and route.client_port == plan["client_port"]
        for route in entry.cascade_specs()
    )
    if parent.status in {"succeeded", "warning"} and not parent_artifacts.get("edit") and matching:
        view["extra_exit_attachment"]["status"] = "succeeded"
    elif parent.status in {"failed", "paused", "canceled"}:
        view["extra_exit_attachment"].update(status="failed", error=parent.error or "Откройте карточку входной ноды")
    return view

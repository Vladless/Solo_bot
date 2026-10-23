import asyncio
import copy
import hashlib
import hmac
import html
import ipaddress
import json
import re
import secrets as token_secrets
import time
import uuid

from contextlib import AsyncExitStack, suppress
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from database.db import async_session_maker
from database.models import RemnawaveInstall

from .progress import build_progress, job_actions, job_status_text
from .types import DELETABLE_STATUSES, NODE_IMAGE, InstallError, InstallSpec, InstallWaitingError
from .vault import vault


_SECRET_FIELDS = {
    "ssh_password",
    "ssh_private_key",
    "ssh_key_passphrase",
    "vk_token",
    "vk_username",
    "vk_password",
    "vk_user_domain",
    "yandex_key",
    "psiphon_config",
}
_ACTIVE = {"queued", "running", "waiting_dns", "cancel_requested"}
_STAGES = (
    "keys",
    "panel_profile",
    "panel_node",
    "docker",
    "addons",
    "verify",
    "cdn",
    "panel_squad",
    "panel_host",
)
_LEASE_SECONDS = 120
_DELETION_KEY = token_secrets.token_bytes(32)


async def _authorize(owner_id: int) -> None:
    """Проверяет действующие права без кеша старого администратора."""
    from filters.admin import get_admin_context
    from filters.permissions import PERM_SETTINGS

    is_admin, is_super, permissions = await get_admin_context(owner_id, fresh=True)
    if not is_admin or not (is_super or PERM_SETTINGS in permissions):
        raise InstallError("permission", "Нет доступа к настройкам Remnawave", retryable=False)


async def _row(session, job_id: str, owner_id: int | None = None, *, lock: bool = False):
    """Загружает задачу только её владельцу."""
    if not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise ValueError("Некорректный номер установки")
    query = select(RemnawaveInstall).where(RemnawaveInstall.id == job_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = (await session.execute(query)).scalar_one_or_none()
    if row is None or (owner_id is not None and row.owner_id != owner_id):
        raise InstallError("permission", "Установка не найдена или принадлежит другому администратору", retryable=False)
    return row


def _view(row, *, include_credentials: bool = True) -> dict:
    """Отдаёт интерфейсу только несекретные поля."""
    pending = None
    artifacts = {}
    artifacts_known = not bool(row.encrypted_artifacts)
    credential_fields = []
    with suppress(InstallError):
        if row.encrypted_artifacts:
            artifacts = vault.open(row.id, row.encrypted_artifacts)
            artifacts_known = True
            pending = artifacts.get("edit", {}).get("after")
    if (
        include_credentials
        and row.encrypted_secrets
        and (not row.secrets_expire_at or row.secrets_expire_at > datetime.utcnow())
    ):
        with suppress(InstallError):
            secrets = vault.open(row.id, row.encrypted_secrets)
            credential_fields = sorted(name for name in _SECRET_FIELDS if secrets.get(name))
    return {
        "id": row.id,
        "status": "pause_requested" if row.pause_requested else row.status,
        "stage": row.stage,
        "params": copy.deepcopy(row.params or {}),
        "pending_params": copy.deepcopy(pending),
        "deletion": _deleting(row, artifacts),
        "can_delete": _idle_deletable(row),
        "has_panel_changes": _has_panel_changes(row, artifacts, unknown=not artifacts_known),
        "resources": {
            **{key: artifacts[key] for key in ("profile_uuid", "node_uuid", "host_uuid") if artifacts.get(key)},
            "cdn": {
                key: artifacts.get("cloud", {}).get(key)
                for key in (
                    "provider",
                    "domain",
                    "origin_domain",
                    "resource_id",
                    "origin_group_id",
                    "certificate_id",
                    "project_id",
                    "ready",
                )
                if key in artifacts.get("cloud", {})
            },
        },
        "retained_resources": [
            {
                key: item[key]
                for key in (
                    "kind",
                    "provider",
                    "domain",
                    "origin_domain",
                    "resource_id",
                    "origin_group_id",
                    "certificate_id",
                    "folder_id",
                    "project_id",
                    "status",
                )
                if key in item
            }
            for item in artifacts.get("retained_resources", [])
        ],
        "logs": list(row.logs or []),
        "dns_records": copy.deepcopy(row.dns_records or []),
        "error": row.error,
        "retryable": row.retryable,
        "progress_message_id": row.progress_message_id,
        "progress": build_progress(
            "pause_requested" if row.pause_requested else row.status,
            row.stage,
            artifacts,
            _STAGES,
            row.params or {},
        ),
        "has_credentials": bool(credential_fields),
        "credential_fields": credential_fields,
    }


def _secrets(value: dict) -> dict:
    """Ограничивает секретные поля и размер загружаемых ключей."""
    if not isinstance(value, dict) or set(value) - _SECRET_FIELDS:
        raise ValueError("Неизвестное секретное поле")
    result = {}
    for key, item in value.items():
        if key in {"yandex_key", "psiphon_config"} and isinstance(item, dict):
            item = json.dumps(item)
        if not isinstance(item, str) or len(item.encode()) > 128_000:
            raise ValueError("Секрет должен быть текстом размером до 128 КБ")
        if key in {"vk_username", "vk_user_domain"}:
            item = item.strip()
            if not item or len(item) > 255 or any(ord(char) < 32 or ord(char) == 127 for char in item):
                raise ValueError("Логин и домен пользователя VK должны быть строками до 255 символов")
        if key in {"vk_password", "vk_token"} and item and not item.strip():
            raise ValueError("Пароль или токен VK Cloud не должен состоять только из пробелов")
        result[key] = item
    return result


def _redact(value: str, secrets: dict, artifacts: dict) -> str:
    """Удаляет значения ключей и управляющие символы из статуса."""
    value = str(value)
    sensitive = []

    def collect(obj, private=False):
        if isinstance(obj, dict):
            for key, item in obj.items():
                collect(
                    item,
                    private
                    or any(
                        word in str(key).lower()
                        for word in ("secret", "token", "password", "private", "credential", "uuid")
                    ),
                )
        elif isinstance(obj, list):
            for item in obj:
                collect(item, private)
        elif private and isinstance(obj, str) and obj:
            sensitive.extend([obj, *obj.splitlines()])
            try:
                nested = json.loads(obj)
            except (ValueError, TypeError):
                return
            if isinstance(nested, dict | list):
                collect(nested, True)

    collect(secrets, True)
    collect(artifacts)
    for secret in sorted(set(sensitive) - {""}, key=len, reverse=True):
        value = value.replace(secret, "[скрыто]")
    value = re.sub(r"(?i)(bearer\s+|(?:password|token|secret|private_key)\s*[:=]\s*)\S+", r"\1[скрыто]", value)
    return "".join(char for char in value if char >= " " or char == "\n")[:512]


async def create_draft(owner_id: int, chat_id: int, panel_ref: str = "") -> dict:
    """Создаёт черновик установки без сетевых изменений."""
    await _authorize(owner_id)
    if panel_ref:
        defaults = {"panel_ref": panel_ref}
    else:
        from .panel import get_install_defaults

        defaults = await get_install_defaults()
        if (
            not isinstance(defaults, dict)
            or not isinstance(defaults.get("panel_ref"), str)
            or not defaults["panel_ref"]
        ):
            raise ValueError("Не удалось определить настроенную панель Remnawave")
    job_id = uuid.uuid4().hex
    params = InstallSpec.from_dict(defaults, complete=False).to_dict()
    async with async_session_maker() as session:
        row = RemnawaveInstall(id=job_id, owner_id=owner_id, chat_id=chat_id, params=params, logs=[], dns_records=[])
        session.add(row)
        await session.flush()
        result = _view(row)
        await session.commit()
    return result


async def update_draft(job_id: str, owner_id: int, *, params: dict | None = None, secrets: dict | None = None) -> dict:
    """Сохраняет параметры черновика и отдельно шифрует секреты."""
    await _authorize(owner_id)
    async with async_session_maker() as session:
        row = await _assignment_row(session, job_id, owner_id, params)
        _not_deleting(row)
        if params is not None:
            if row.status != "draft":
                raise ValueError("После запуска используйте «Изменить настройки» и предварительный просмотр")
            spec = InstallSpec.from_dict({**row.params, **params}, complete=False)
            await _guard_exit_reference(session, row, spec)
            row.params, row.address = spec.to_dict(), spec.address
        if secrets is not None:
            if row.status in {"running", "cancel_requested", "canceled"}:
                raise ValueError("Дождитесь остановки операции перед обновлением ключей")
            previous = vault.open(row.id, row.encrypted_secrets) if row.encrypted_secrets else {}
            previous.update(_secrets(secrets))
            row.encrypted_secrets = vault.seal(row.id, previous)
            row.secrets_expire_at = datetime.utcnow() + timedelta(days=30 if row.fingerprint else 1)
        row.updated_at = datetime.utcnow()
        await session.commit()
        return _view(row)


async def get_job(job_id: str, owner_id: int) -> dict:
    """Возвращает безопасную карточку установки."""
    await _authorize(owner_id)
    async with async_session_maker() as session:
        return _view(await _row(session, job_id, owner_id))


async def bind_progress_message(
    job_id: str, owner_id: int, message_id: int | None, *, chat_id: int | None = None
) -> dict:
    """Привязывает фоновое обновление к открытой карточке установки."""
    await _authorize(owner_id)
    if message_id is not None and (type(message_id) is not int or message_id <= 0):
        raise ValueError("Некорректный номер сообщения установки")
    if chat_id is not None and (type(chat_id) is not int or chat_id == 0):
        raise ValueError("Некорректный номер чата установки")
    async with async_session_maker() as session:
        row = await _row(session, job_id, owner_id, lock=True)
        row.progress_message_id = message_id
        if message_id is not None and chat_id is not None:
            row.chat_id = chat_id
        await session.commit()
        return _view(row)


async def list_jobs(owner_id: int) -> list[dict]:
    """Показывает последние установки текущего администратора."""
    await _authorize(owner_id)
    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(RemnawaveInstall)
                .where(RemnawaveInstall.owner_id == owner_id)
                .order_by(RemnawaveInstall.created_at.desc())
                .limit(30)
            )
        ).scalars()
        return [_view(row) for row in rows]


async def _snapshot(job_id: str, owner_id: int | None = None, *, include_secrets: bool = True) -> dict:
    """Читает закрытый снимок вне длительной транзакции."""
    async with async_session_maker() as session:
        row = await _row(session, job_id, owner_id)
        secrets = vault.open(row.id, row.encrypted_secrets) if include_secrets else {}
        if row.secrets_expire_at and row.secrets_expire_at <= datetime.utcnow():
            secrets = {}
        return {
            **_view(row, include_credentials=include_secrets),
            "owner_id": row.owner_id,
            "chat_id": row.chat_id,
            "fingerprint": row.fingerprint,
            "secrets": secrets,
            "artifacts": vault.open(row.id, row.encrypted_artifacts),
        }


def _ssh_state(row) -> dict:
    """Фиксирует состояние, запрещающее устаревшее сохранение SSH-ключа."""
    return {
        name: copy.deepcopy(getattr(row, name))
        for name in (
            "params",
            "owner_id",
            "status",
            "lease_token",
            "lease_until",
            "pause_requested",
            "fingerprint",
            "encrypted_artifacts",
            "encrypted_secrets",
            "secrets_expire_at",
        )
    }


def _ssh_setup_snapshot(row, *, include_secrets: bool = True) -> dict:
    """Снимает параметры и доступы перед обращением к SSH."""
    secrets = {}
    if include_secrets and (not row.secrets_expire_at or row.secrets_expire_at > datetime.utcnow()):
        secrets = vault.open(row.id, row.encrypted_secrets)
    return {
        "guard": _ssh_state(row),
        "params": copy.deepcopy(row.params),
        "artifacts": vault.open(row.id, row.encrypted_artifacts),
        "secrets": secrets,
    }


async def _read_ssh_setup(job_id: str, owner_id: int, *, include_secrets: bool = True) -> dict:
    """Читает остановленную установку без длительной блокировки."""
    async with async_session_maker() as session:
        row = await _row(session, job_id, owner_id)
        _editable(row)
        return _ssh_setup_snapshot(row, include_secrets=include_secrets)


def _ssh_endpoint(spec: InstallSpec) -> tuple[str, int]:
    """Приводит адрес SSH к единому виду."""
    try:
        return str(ipaddress.ip_address(spec.address.strip())), spec.ssh_port
    except (ValueError, AttributeError) as error:
        raise ValueError("Сначала укажите IP сервера") from error


def _ssh_setup_spec(snapshot: dict, params: dict | None, *, complete: bool = False, reset: bool = False) -> InstallSpec:
    """Сохраняет доверие к известному ключу того же сервера."""
    visible = snapshot["artifacts"].get("edit", {}).get("after") or snapshot["params"]
    spec = InstallSpec.from_dict({**visible, **(params or {})}, complete=complete)
    spec.address, spec.ssh_port = _ssh_endpoint(spec)
    if snapshot["artifacts"].get("profile_uuid") and spec.address != snapshot["params"].get("address"):
        raise ValueError("Перенос на другой IP выполняется отдельной установкой")
    if not reset and not spec.host_fingerprint:
        for source in (visible, snapshot["params"]):
            previous = InstallSpec.from_dict(source, complete=False)
            if previous.address:
                with suppress(ValueError):
                    if _ssh_endpoint(previous) == _ssh_endpoint(spec) and previous.host_fingerprint:
                        spec.host_fingerprint = previous.host_fingerprint
                        break
    return spec


async def _resolve_ssh_pin(spec: InstallSpec, *, verify: bool = False, reset: bool = False) -> None:
    """Запоминает первый ключ и проверяет ранее сохранённый до авторизации."""
    from .remote import RemoteServer

    if spec.host_fingerprint and not verify and not reset:
        return
    if spec.host_fingerprint and verify and not reset:
        fingerprint = await RemoteServer.fingerprint(spec.address, spec.ssh_port, expected=spec.host_fingerprint)
    else:
        fingerprint = await RemoteServer.fingerprint(spec.address, spec.ssh_port)
    if not isinstance(fingerprint, str) or not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", fingerprint):
        raise InstallError("ssh", "Не удалось получить корректный SSH-ключ сервера")
    if not reset and spec.host_fingerprint and spec.host_fingerprint != fingerprint:
        raise InstallError(
            "ssh_host_key", "SSH-ключ сервера изменился. Проверьте сервер и подтвердите замену ключа", retryable=False
        )
    spec.host_fingerprint = fingerprint


def _check_ssh_setup(row, snapshot: dict) -> None:
    """Отклоняет результат проверки после изменения установки."""
    _editable(row)
    if _ssh_state(row) != snapshot["guard"]:
        raise ValueError("Параметры или состояние установки изменились. Повторите проверку SSH")


def _save_draft_ssh_pin(row, spec: InstallSpec) -> None:
    """Сохраняет ключ только у текущего адреса черновика."""
    if (
        row.status == "draft"
        and row.params.get("address")
        and _ssh_endpoint(InstallSpec.from_dict(row.params, complete=False)) == _ssh_endpoint(spec)
    ):
        row.params = {**row.params, "host_fingerprint": spec.host_fingerprint}
        row.updated_at = datetime.utcnow()


async def inspect_host_key(job_id: str, owner_id: int, *, params: dict | None = None) -> dict:
    """Показывает ключ сервера без авторизации и сохранения."""
    await _authorize(owner_id)
    snapshot = await _read_ssh_setup(job_id, owner_id, include_secrets=False)
    spec = _ssh_setup_spec(snapshot, params)
    previous = spec.host_fingerprint
    try:
        await _resolve_ssh_pin(spec, verify=True)
    except InstallError as error:
        if error.step != "ssh_host_key":
            raise
        await _resolve_ssh_pin(spec, reset=True)
    await _authorize(owner_id)
    async with async_session_maker() as session:
        row = await _row(session, job_id, owner_id, lock=True)
        _check_ssh_setup(row, snapshot)
    return {"fingerprint": spec.host_fingerprint, "previous_fingerprint": previous, "params": spec.to_dict()}


def _save_confirmed_ssh_pin(row, spec: InstallSpec, artifacts: dict) -> bool:
    """Сохраняет подтверждённый ключ только для прежнего адреса SSH."""
    if not row.params.get("address") or _ssh_endpoint(
        InstallSpec.from_dict(row.params, complete=False)
    ) != _ssh_endpoint(spec):
        return False
    if row.fingerprint and InstallSpec.from_dict(row.params).fingerprint() != row.fingerprint:
        raise InstallError("snapshot", "Параметры установки не совпадают с сохранённым снимком", retryable=False)
    row.params = {**row.params, "host_fingerprint": spec.host_fingerprint}
    if row.fingerprint:
        row.fingerprint = InstallSpec.from_dict(row.params).fingerprint()
    edit = artifacts.get("edit")
    if isinstance(edit, dict):
        changed = False
        for name in ("before", "after"):
            params = edit.get(name)
            if not isinstance(params, dict):
                continue
            with suppress(ValueError):
                if _ssh_endpoint(InstallSpec.from_dict(params, complete=False)) == _ssh_endpoint(spec):
                    edit[name] = {**params, "host_fingerprint": spec.host_fingerprint}
                    changed = True
        if changed:
            row.encrypted_artifacts = vault.seal(row.id, artifacts)
    row.updated_at = datetime.utcnow()
    return True


async def reset_host_key(job_id: str, owner_id: int, *, fingerprint: str, params: dict | None = None) -> dict:
    """Закрепляет показанный владельцу ключ после повторной проверки."""
    await _authorize(owner_id)
    if not isinstance(fingerprint, str) or not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", fingerprint):
        raise InstallError("ssh_host_key", "Сначала заново откройте подтверждение SSH-ключа сервера", retryable=False)
    snapshot = await _read_ssh_setup(job_id, owner_id, include_secrets=False)
    spec = _ssh_setup_spec(snapshot, params, reset=True)
    spec.host_fingerprint = fingerprint
    try:
        await _resolve_ssh_pin(spec, verify=True)
    except InstallError as error:
        if error.step != "ssh_host_key":
            raise
        raise InstallError(
            "ssh_host_key",
            "SSH-ключ изменился после показа. Проверьте сервер и откройте подтверждение заново",
            retryable=False,
        ) from error
    await _authorize(owner_id)
    async with async_session_maker() as session:
        row = await _row(session, job_id, owner_id, lock=True)
        _check_ssh_setup(row, snapshot)
        persisted = _save_confirmed_ssh_pin(row, spec, snapshot["artifacts"])
        await session.commit()
    return {
        "fingerprint": spec.host_fingerprint,
        "trusted": True,
        "authenticated": False,
        "params": spec.to_dict(),
        "persisted": persisted,
    }


async def probe_host(job_id: str, owner_id: int, *, params: dict | None = None) -> dict:
    """Запоминает первый ключ и проверяет доступ к серверу."""
    from .remote import (
        RemoteServer,
        owner_id as remote_owner_id,
        preflight,
    )

    await _authorize(owner_id)
    snapshot = await _read_ssh_setup(job_id, owner_id)
    spec = _ssh_setup_spec(snapshot, params)
    if snapshot["artifacts"].get("profile_uuid") or snapshot["artifacts"].get("remote_deployed"):
        spec._owner_id = snapshot["artifacts"].get("remote_owner_id") or remote_owner_id(
            InstallSpec.from_dict(snapshot["params"], complete=False)
        )
    await _resolve_ssh_pin(spec, verify=True)
    await _authorize(owner_id)
    async with async_session_maker() as session:
        row = await _row(session, job_id, owner_id, lock=True)
        _check_ssh_setup(row, snapshot)
        _save_draft_ssh_pin(row, spec)
        await session.commit()
    result = {"fingerprint": spec.host_fingerprint, "trusted": True, "authenticated": False, "params": spec.to_dict()}
    if snapshot["secrets"].get("ssh_password") or snapshot["secrets"].get("ssh_private_key"):
        async with RemoteServer(spec, snapshot["secrets"]) as remote:
            result.update(await preflight(remote, spec))
        result["authenticated"] = True
    return result


async def start_job(job_id: str, owner_id: int, message_id: int | None = None) -> dict:
    """Ставит подтверждённую установку в устойчивую очередь."""
    await _authorize(owner_id)
    async with async_session_maker() as session:
        row = await _row(session, job_id, owner_id)
        _not_deleting(row)
        if row.status != "draft":
            return _view(row)
        _editable(row)
        snapshot = _ssh_setup_snapshot(row)
        spec = InstallSpec.from_dict(row.params)
        _require_credentials(spec, snapshot["secrets"])
        if row.secrets_expire_at and row.secrets_expire_at <= datetime.utcnow():
            raise ValueError("Срок хранения ключей истёк. Введите их повторно")
    await _resolve_ssh_pin(spec)
    await _authorize(owner_id)
    async with async_session_maker() as session:
        row = await _row(session, job_id, owner_id, lock=True)
        _check_ssh_setup(row, snapshot)
        row.params, row.address, row.fingerprint = spec.to_dict(), spec.address, spec.fingerprint()
        row.status, row.stage = "queued", "preflight"
        row.progress_message_id = message_id
        row.secrets_expire_at = datetime.utcnow() + timedelta(days=30)
        row.next_retry_at = datetime.utcnow()
        try:
            await session.commit()
        except IntegrityError as error:
            await session.rollback()
            raise ValueError("На этом сервере уже выполняется установка. Дождитесь её завершения") from error
        return _view(row)


def _require_credentials(spec: InstallSpec, secrets: dict) -> None:
    """Проверяет наличие секретов до любых изменений сервера."""
    if not (secrets.get("ssh_password") or secrets.get("ssh_private_key")):
        raise ValueError("Введите SSH-пароль или приватный ключ")
    if spec.transport == "cdn_vk":
        if not spec.cloud.get("project_id"):
            raise ValueError("Введите ID проекта VK Cloud")
        if spec.cloud.get("auth_mode", "token") == "service_account":
            for field, label in (
                ("vk_user_domain", "домен пользователя VK Cloud (OS_USER_DOMAIN_NAME)"),
                ("vk_username", "логин сервисной учётной записи VK Cloud"),
                ("vk_password", "пароль сервисной учётной записи VK Cloud"),
            ):
                if not isinstance(secrets.get(field), str) or not secrets[field].strip():
                    raise ValueError("Введите " + label)
        elif not isinstance(secrets.get("vk_token"), str) or not secrets["vk_token"].strip():
            raise ValueError("Введите токен VK Cloud")
    if spec.transport == "cdn_yandex":
        try:
            key = json.loads(secrets.get("yandex_key", ""))
            if not all(key.get(name) for name in ("id", "service_account_id", "private_key")):
                raise ValueError
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("Введите JSON авторизованного ключа сервисного аккаунта Яндекс") from error
    if "psiphon" in spec.addons:
        from .addons import psiphon_config

        psiphon_config(secrets)


def _require_change_credentials(spec: InstallSpec, secrets: dict, operations: list[str]) -> bool:
    """Требует доступы только к затрагиваемым системам."""
    remote = bool(set(operations) & {"connection", "prepare", "remote"})
    cloud = "cloud" in operations and spec.transport.startswith("cdn_")
    if remote and not (secrets.get("ssh_password") or secrets.get("ssh_private_key")):
        raise ValueError("Для этих изменений введите SSH-пароль или ключ")
    if cloud:
        cloud_spec = copy.deepcopy(spec)
        cloud_spec.addons = []
        _require_credentials(cloud_spec, {**secrets, "ssh_password": "unused"})
    return remote or cloud


async def retry_job(job_id: str, owner_id: int) -> dict:
    """Продолжает ту же установку с сохранёнными идентификаторами."""
    await _authorize(owner_id)
    async with async_session_maker() as session:
        row = await _row(session, job_id, owner_id, lock=True)
        if _deleting(row):
            if not _idle_deletable(row):
                raise ValueError("Дождитесь остановки удаления и освобождения текущего этапа")
            await _guard_dependents(session, row)
            artifacts = vault.open(row.id, row.encrypted_artifacts)
            if not isinstance(artifacts.get("deletion"), dict):
                raise InstallError(
                    "panel_cleanup", "План удаления недоступен; можно удалить только запись из бота", retryable=False
                )
            row.status, row.error, row.next_retry_at = "queued", None, datetime.utcnow()
            row.stage, row.retryable, row.pause_requested = "delete:panel_cleanup", True, False
            row.lease_token, row.lease_until = None, None
            await session.commit()
            return _view(row, include_credentials=False)
        if row.status not in {"failed", "waiting_dns", "warning", "paused"}:
            return _view(row)
        if not row.retryable and row.status not in {"failed", "paused"}:
            raise ValueError("Исправьте параметры через «Изменить настройки», затем примените изменения")
        spec = InstallSpec.from_dict(row.params)
        if spec.fingerprint() != row.fingerprint:
            raise InstallError("snapshot", "Параметры установки не совпадают с сохранённым снимком", retryable=False)
        artifacts = vault.open(row.id, row.encrypted_artifacts)
        secrets = vault.open(row.id, row.encrypted_secrets)
        required = True
        if artifacts.get("edit"):
            edit = artifacts["edit"]
            required = _require_change_credentials(InstallSpec.from_dict(edit["after"]), secrets, edit["operations"])
        else:
            _require_credentials(spec, secrets)
        if required and row.secrets_expire_at and row.secrets_expire_at <= datetime.utcnow():
            raise ValueError("Срок хранения ключей истёк. Введите их повторно")
        artifacts.pop("dns_wait", None)
        if row.status == "warning":
            artifacts["done"] = [stage for stage in artifacts.get("done", []) if stage != "verify"]
            artifacts.pop("warnings", None)
        if row.stage == "verify":
            artifacts.pop("verify_started_at", None)
        if artifacts.get("edit"):
            artifacts["edit"].pop("verify_started_at", None)
        row.encrypted_artifacts = vault.seal(row.id, artifacts)
        row.status, row.error, row.next_retry_at = "queued", None, datetime.utcnow()
        row.retryable = True
        row.pause_requested = False
        try:
            await session.commit()
        except IntegrityError as error:
            await session.rollback()
            raise ValueError("На этом сервере уже выполняется другая установка") from error
        return _view(row)


async def cancel_job(job_id: str, owner_id: int) -> dict:
    """Останавливает работу и удаляет сохранённые SSH-доступы."""
    await _authorize(owner_id)
    async with async_session_maker() as session:
        row = await _row(session, job_id, owner_id, lock=True)
        row.status = "cancel_requested" if row.lease_token else "canceled"
        row.pause_requested = False
        row.next_retry_at = None
        row.encrypted_secrets, row.secrets_expire_at = None, None
        await session.commit()
        return _view(row)


async def pause_job(job_id: str, owner_id: int) -> dict:
    """Приостанавливает ввод-вывод, сохраняя доступы и контрольные точки."""
    await _authorize(owner_id)
    async with async_session_maker() as session:
        row = await _row(session, job_id, owner_id, lock=True)
        if row.status in {"canceled", "cancel_requested"}:
            raise ValueError("Отменённую установку нельзя приостановить")
        if row.status in {"succeeded", "warning", "draft", "failed", "paused"} and not row.lease_token:
            return _view(row)
        if row.lease_token:
            row.pause_requested = True
        else:
            row.status, row.next_retry_at = "paused", None
            row.pause_requested = False
        await session.commit()
        return _view(row)


def _editable(row) -> None:
    """Не допускает изменение параметров одновременно с вводом-выводом."""
    _not_deleting(row)
    if (
        row.lease_token
        or row.pause_requested
        or row.status not in {"draft", "paused", "failed", "waiting_dns", "succeeded", "warning"}
    ):
        raise ValueError("Сначала приостановите установку и дождитесь освобождения текущего этапа")


async def preview_changes(job_id: str, owner_id: int, *, params: dict) -> dict:
    """Сохраняет проверяемый план выбранных изменений на пятнадцать минут."""
    from .changes import STEP_LABELS, build_change_plan

    await _authorize(owner_id)
    snapshot = await _read_ssh_setup(job_id, owner_id)
    new = _ssh_setup_spec(snapshot, params, complete=True)
    await _resolve_ssh_pin(new)
    await _authorize(owner_id)
    async with async_session_maker() as session:
        row = await _row(session, job_id, owner_id, lock=True)
        _check_ssh_setup(row, snapshot)
        artifacts = vault.open(row.id, row.encrypted_artifacts)
        edit = artifacts.get("edit") or {}
        old = (
            InstallSpec.from_dict(row.params) if row.fingerprint else InstallSpec.from_dict(row.params, complete=False)
        )
        visible = InstallSpec.from_dict(edit.get("after") or row.params, complete=bool(row.fingerprint))
        if old.role == "exit":
            dependent = (
                (
                    await session.execute(
                        select(RemnawaveInstall.id).where(
                            RemnawaveInstall.params["exit_install_id"].as_string() == job_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            artifacts["dependent_entries"] = list(dependent)
        plan = build_change_plan(old, new, artifacts, visible=visible)
        if not plan["changes"]:
            raise ValueError("Параметры не изменились")
        if edit:
            applied = set(edit.get("changed", []))
            effective = dict(artifacts)
            previous = InstallSpec.from_dict(edit["after"])
            previous_plan = build_change_plan(previous, new, effective, visible=visible)
            plan["operations"] = [
                step for step in STEP_LABELS if step in {*plan["operations"], *previous_plan["operations"]}
            ]
            plan["steps"] = [STEP_LABELS[step] for step in plan["operations"]]
            plan["changed"] = sorted(set(plan["changed"]) | applied | set(previous_plan["changed"]))
            plan["requires_replacement"] = plan["requires_replacement"] or previous_plan["requires_replacement"]
            plan["warnings"] = list(dict.fromkeys([*plan["warnings"], *previous_plan["warnings"]]))
        token = uuid.uuid4().hex
        artifacts["edit_preview"] = {
            "token": token,
            "before": row.params,
            "after": new.to_dict(),
            "edit_id": edit.get("id"),
            "plan": plan,
            "expires_at": time.time() + 900,
        }
        row.encrypted_artifacts = vault.seal(row.id, artifacts)
        if row.status == "waiting_dns":
            row.status, row.next_retry_at = "paused", None
        await session.commit()
        return {**plan, "token": token, "params": new.to_dict()}


async def apply_changes(job_id: str, owner_id: int, *, params: dict, token: str, message_id: int | None = None) -> dict:
    """Ставит только подтверждённые изменения в устойчивую очередь."""
    from .changes import is_installed
    from .remote import owner_id as remote_owner_id

    await _authorize(owner_id)
    async with async_session_maker() as session:
        row = await _assignment_row(session, job_id, owner_id, params)
        _editable(row)
        artifacts = vault.open(row.id, row.encrypted_artifacts)
        preview, previous_edit = artifacts.get("edit_preview") or {}, artifacts.get("edit") or {}
        visible = previous_edit.get("after") or row.params
        spec = InstallSpec.from_dict({**visible, **params})
        if (
            not token
            or token != preview.get("token")
            or preview.get("expires_at", 0) < time.time()
            or preview.get("before") != row.params
            or preview.get("after") != spec.to_dict()
            or preview.get("edit_id") != previous_edit.get("id")
        ):
            raise ValueError("План изменений устарел. Откройте предварительный просмотр заново")
        await _guard_exit_reference(session, row, spec)
        if row.status == "draft":
            row.params, row.address = spec.to_dict(), spec.address
            artifacts.pop("edit_preview", None)
            row.encrypted_artifacts = vault.seal(row.id, artifacts)
            await session.commit()
            return _view(row)
        plan = preview["plan"]
        secrets = vault.open(row.id, row.encrypted_secrets)
        required = _require_change_credentials(spec, secrets, plan["operations"])
        if required and row.secrets_expire_at and row.secrets_expire_at <= datetime.utcnow():
            raise ValueError("Срок хранения SSH-доступов истёк; введите их повторно")
        old = InstallSpec.from_dict(row.params)
        artifacts.setdefault("remote_owner_id", remote_owner_id(old))
        old_config = artifacts.get("profile_config", {})
        if not artifacts.get("inbound_tag") and old_config.get("inbounds"):
            artifacts["inbound_tag"] = old_config["inbounds"][0].get("tag")
        artifacts["edit"] = {
            "id": uuid.uuid4().hex,
            "before": row.params,
            "after": spec.to_dict(),
            "previous_target": previous_edit.get("after"),
            "changed": plan["changed"],
            "operations": plan["operations"],
            "done": [],
            "applied_specs": previous_edit.get("applied_specs", {}),
            "requires_replacement": plan["requires_replacement"],
            "resume_install": previous_edit.get("resume_install", not is_installed(artifacts)),
        }
        artifacts.pop("edit_preview", None)
        artifacts.pop("dns_wait", None)
        row.encrypted_artifacts = vault.seal(row.id, artifacts)
        row.status, row.stage, row.error, row.retryable = "queued", "edit", None, True
        row.pause_requested, row.next_retry_at = False, datetime.utcnow()
        if message_id:
            row.progress_message_id = message_id
        try:
            await session.commit()
        except IntegrityError as error:
            await session.rollback()
            raise ValueError("На этом сервере уже выполняется другая операция") from error
        return _view(row)


def _deleting(row, artifacts: dict | None = None) -> bool:
    """Распознаёт удаление даже при недоступном зашифрованном плане."""
    if (row.stage or "").startswith("delete:"):
        return True
    if artifacts is None:
        with suppress(InstallError):
            artifacts = vault.open(row.id, row.encrypted_artifacts)
    return isinstance(artifacts, dict) and isinstance(artifacts.get("deletion"), dict)


def _not_deleting(row) -> None:
    """Запрещает устаревшим действиям возобновлять установку при удалении."""
    if _deleting(row):
        raise ValueError("Для этой записи уже выбрано удаление. Доступны повтор удаления или удаление только из бота")


def _idle_deletable(row) -> bool:
    """Разрешает удаление только полностью остановленной записи."""
    expired_cleanup = _deleting(row) and row.lease_until and row.lease_until <= datetime.utcnow()
    return bool(
        row.status in DELETABLE_STATUSES
        and not row.pause_requested
        and ((not row.lease_token and not row.lease_until) or expired_cleanup)
    )


def _has_panel_changes(row, artifacts: dict, *, unknown: bool = False) -> bool:
    """Учитывает подтверждённые объекты и потерянные ответы панели."""
    if unknown:
        return True
    if any(
        artifacts.get(key)
        for key in (
            "profile_uuid",
            "inbound_uuid",
            "node_uuid",
            "host_uuid",
            "cascade",
            "retained_cascades",
            "panel_updates",
            "panel_cleanup",
            "profile_create_intent",
            "node_create_intent",
            "host_create_intent",
        )
    ):
        return True
    stages = {"panel_profile", "panel_node", "panel_squad", "panel_host"}
    if stages.intersection(artifacts.get("done", [])):
        return True
    stage = (row.stage or "").removeprefix("edit:").removeprefix("delete:")
    return (
        stage == "panel"
        or stage.startswith("panel_")
        or bool(set((artifacts.get("edit") or {}).get("done", [])) & {"profile", "node", "squad", "host"})
    )


def _approved_specs(row, artifacts: dict) -> list[dict]:
    """Собирает сохранённые варианты параметров без сетевых запросов."""
    result = [row.params or {}]
    edit = artifacts.get("edit") or {}
    result.extend(edit.get(key) for key in ("before", "after", "previous_target"))
    result.extend((edit.get("applied_specs") or {}).values())
    return [item for item in result if isinstance(item, dict)]


async def _guard_exit_reference(session, row, spec: InstallSpec) -> None:
    """Фиксирует новую связь с выходом до удаления его записи."""
    if spec.role != "entry" or not spec.exit_install_id:
        return
    if spec.exit_install_id == row.id:
        raise ValueError("Входная нода не может ссылаться на себя")
    artifacts = vault.open(row.id, row.encrypted_artifacts)
    if any(item.get("exit_install_id") == spec.exit_install_id for item in _approved_specs(row, artifacts)):
        return
    candidate = await _row(session, spec.exit_install_id, row.owner_id)
    if (candidate.params or {}).get("role") != "exit":
        raise ValueError("Выберите существующую выходную ноду этого администратора")
    candidate = await _row(session, spec.exit_install_id, row.owner_id, lock=True)
    if (candidate.params or {}).get("role") != "exit" or _deleting(candidate):
        raise ValueError("Выходная нода удаляется или больше недоступна для каскада")


async def _assignment_row(session, job_id: str, owner_id: int, params: dict | None):
    """Блокирует новую связь входа с выходом в едином порядке идентификаторов."""
    row = await _row(session, job_id, owner_id)

    def target(value):
        artifacts = vault.open(value.id, value.encrypted_artifacts) if params is not None else {}
        visible = (artifacts.get("edit") or {}).get("after") or value.params or {}
        proposed = {**visible, **(params or {})}
        return proposed.get("exit_install_id") if params is not None and proposed.get("role") == "entry" else None

    exit_id = target(row)
    identifiers = {job_id, exit_id} if isinstance(exit_id, str) else {job_id}
    locked = await session.execute(
        select(RemnawaveInstall)
        .where(RemnawaveInstall.id.in_(identifiers))
        .order_by(RemnawaveInstall.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    locked.scalars().all()
    row = await _row(session, job_id, owner_id)
    if target(row) != exit_id:
        raise ValueError("Параметры выхода изменились. Повторите действие")
    return row


async def _guard_dependents(session, row) -> None:
    """Сохраняет выход, пока на него ссылается любая запись каскада."""
    own = {}
    with suppress(InstallError):
        own = vault.open(row.id, row.encrypted_artifacts)
    own_specs = _approved_specs(row, own)
    is_exit = any(item.get("role") == "exit" for item in own_specs)
    addresses = {item.get("address") for item in own_specs if item.get("address")}
    others = (await session.execute(select(RemnawaveInstall).where(RemnawaveInstall.id != row.id))).scalars()
    for other in others:
        try:
            artifacts = vault.open(other.id, other.encrypted_artifacts)
        except InstallError as error:
            if (other.params or {}).get("exit_install_id") == row.id or is_exit:
                raise ValueError(
                    "Нельзя проверить связи каскада: сохранённые параметры другой установки недоступны"
                ) from error
            continue
        if any(item.get("exit_install_id") == row.id for item in _approved_specs(other, artifacts)):
            raise ValueError(
                "Выходная нода используется другой установкой каскада. Сначала удалите или измените входную ноду"
            )
        cascades = [artifacts.get("cascade"), *artifacts.get("retained_cascades", [])]
        contexts = (artifacts.get("cleanup_cascade_contexts") or {}).values()
        if any(isinstance(item, dict) and item.get("exit_install_id") == row.id for item in [*cascades, *contexts]):
            raise ValueError("Выходная нода используется сохранённым техническим каскадом")
        if is_exit and any(isinstance(item, dict) and item.get("address") in addresses for item in cascades):
            raise ValueError("Выходная нода используется техническим каскадом без полного идентификатора")


def _deletion_digest(row) -> str:
    """Привязывает подтверждение к состоянию записи, исключая привязку меню."""
    value = {
        key: getattr(row, key)
        for key in (
            "id",
            "owner_id",
            "params",
            "address",
            "status",
            "stage",
            "fingerprint",
            "encrypted_artifacts",
            "encrypted_secrets",
            "secrets_expire_at",
            "lease_token",
            "lease_until",
            "pause_requested",
            "retryable",
        )
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def _deletion_token(row) -> str:
    """Выдаёт подтверждение удаления сроком на пятнадцать минут."""
    payload = f"{int(time.time()) + 900}.{_deletion_digest(row)}"
    return payload + "." + hmac.new(_DELETION_KEY, payload.encode(), hashlib.sha256).hexdigest()


def _check_deletion_token(row, token: str) -> None:
    """Отказывает при устаревшем или изменённом подтверждении."""
    try:
        expires, digest, signature = token.split(".")
        payload = expires + "." + digest
        valid = (
            int(expires) >= time.time()
            and hmac.compare_digest(digest, _deletion_digest(row))
            and hmac.compare_digest(signature, hmac.new(_DELETION_KEY, payload.encode(), hashlib.sha256).hexdigest())
        )
    except (ValueError, AttributeError, TypeError):
        valid = False
    if not valid:
        raise ValueError("Подтверждение удаления устарело. Откройте его заново")


async def prepare_deletion(job_id: str, owner_id: int) -> dict:
    """Проверяет локальное удаление без SSH, панели и расшифровки доступов."""
    await _authorize(owner_id)
    async with async_session_maker() as session:
        row = await _row(session, job_id, owner_id, lock=True)
        if not _idle_deletable(row):
            raise ValueError("Сначала остановите установку и дождитесь освобождения текущего этапа")
        await _guard_dependents(session, row)
        artifacts, known = {}, not bool(row.encrypted_artifacts)
        with suppress(InstallError):
            artifacts = vault.open(row.id, row.encrypted_artifacts)
            known = True
        return {
            "token": _deletion_token(row),
            "has_panel_changes": _has_panel_changes(row, artifacts, unknown=not known),
            "resources": {
                key: artifacts[key] for key in ("profile_uuid", "node_uuid", "host_uuid") if artifacts.get(key)
            },
        }


async def delete_job(
    job_id: str, owner_id: int, *, remove_panel: bool = False, token: str, message_id: int | None = None
) -> dict | None:
    """Удаляет запись либо ставит подтверждённую очистку панели в очередь."""
    await _authorize(owner_id)
    if type(remove_panel) is not bool or (message_id is not None and (type(message_id) is not int or message_id <= 0)):
        raise ValueError("Некорректные параметры удаления")
    async with async_session_maker() as session:
        row = await _row(session, job_id, owner_id, lock=True)
        if not _idle_deletable(row):
            raise ValueError("Сначала остановите установку и дождитесь освобождения текущего этапа")
        _check_deletion_token(row, token)
        await _guard_dependents(session, row)
        if not remove_panel:
            await session.delete(row)
            await session.commit()
            return None
        artifacts = vault.open(row.id, row.encrypted_artifacts)
        InstallSpec.from_dict(row.params, complete=False)
        artifacts.setdefault("deletion", {"operations": ["panel_cleanup", "record"], "done": []})
        row.encrypted_artifacts = vault.seal(row.id, artifacts)
        row.status, row.stage, row.error, row.retryable = "queued", "delete:panel_cleanup", None, True
        row.lease_token, row.lease_until = None, None
        row.next_retry_at = datetime.utcnow()
        if message_id is not None:
            row.progress_message_id = message_id
        try:
            await session.commit()
        except IntegrityError as error:
            await session.rollback()
            raise ValueError("На этом сервере уже выполняется другая операция") from error
        return _view(row, include_credentials=False)


async def list_panel_options(panel_ref: str) -> dict:
    """Возвращает доступные сквады выбранной панели."""
    from .panel import InstallerPanel

    async with InstallerPanel(InstallSpec(panel_ref=panel_ref)) as panel:
        await panel.ensure_version()
        return {"squads": await panel.list_squads()}


async def manage_node(job_id: str, owner_id: int, action: str, params: dict | None = None) -> dict:
    """Применяет отдельное изменение дополнения через общий план."""
    from .types import ADDONS

    params = params or {}
    if action not in {"shaping", "addon"}:
        raise ValueError("Неизвестная операция управления нодой")
    job = await get_job(job_id, owner_id)
    current = copy.deepcopy(job.get("pending_params") or job["params"])
    if action == "shaping":
        current["shaping"] = params
    else:
        name, enabled = params.get("name"), params.get("enabled")
        if name not in ADDONS or type(enabled) is not bool:
            raise ValueError("Выберите дополнение и действие")
        current["addons"] = [item for item in current.get("addons", []) if item != name]
        if enabled:
            current["addons"].append(name)
    preview = await preview_changes(job_id, owner_id, params=current)
    return await apply_changes(job_id, owner_id, params=current, token=preview["token"])


async def reinstall_job(
    job_id: str, owner_id: int, *, addons: list[str] | None = None, shaping: dict | None = None
) -> dict:
    """Повторяет установку собственных ресурсов с выбранными дополнениями."""
    await _authorize(owner_id)
    async with async_session_maker() as session:
        row = await _row(session, job_id, owner_id, lock=True)
        _not_deleting(row)
        if row.status not in {"succeeded", "warning"}:
            raise ValueError("Переустановка доступна только после завершения установки")
        params = copy.deepcopy(row.params)
        if addons is not None:
            if not isinstance(addons, list) or not set(params.get("addons", [])).issubset(set(addons)):
                raise ValueError("Для удаления дополнений используйте отдельное действие в карточке ноды")
            params["addons"] = addons
        if shaping is not None:
            params["shaping"] = shaping
        spec = InstallSpec.from_dict(params)
        _require_credentials(spec, vault.open(row.id, row.encrypted_secrets))
        if row.secrets_expire_at and row.secrets_expire_at <= datetime.utcnow():
            raise ValueError("Перед переустановкой введите SSH- и облачные доступы повторно")
        artifacts = vault.open(row.id, row.encrypted_artifacts)
        if not all(artifacts.get(key) for key in ("profile_uuid", "inbound_uuid", "node_uuid", "node_secret")):
            raise ValueError("В снимке отсутствуют подтверждённые ресурсы установленной ноды")
        artifacts["done"] = ["keys", "panel_profile", "panel_node"]
        artifacts.pop("verify_started_at", None)
        artifacts.pop("management", None)
        artifacts.pop("warnings", None)
        if isinstance(artifacts.get("last_edit"), dict):
            artifacts["last_edit"].pop("progress", None)
        row.params, row.fingerprint = spec.to_dict(), spec.fingerprint()
        row.encrypted_artifacts = vault.seal(row.id, artifacts)
        row.status, row.stage, row.error, row.retryable = "queued", "docker", None, True
        row.next_retry_at = datetime.utcnow()
        try:
            await session.commit()
        except IntegrityError as error:
            await session.rollback()
            raise ValueError("На сервере выполняется другая операция") from error
        return _view(row)


async def _save(job_id: str, token: str, *, artifacts=None, log=None, **values: Any) -> bool:
    """Сохраняет этап только действующему владельцу аренды."""
    async with async_session_maker() as session:
        row = await _row(session, job_id, lock=True)
        if row.lease_token != token or row.status == "cancel_requested" or row.pause_requested:
            raise asyncio.CancelledError
        if artifacts is not None:
            row.encrypted_artifacts = vault.seal(row.id, artifacts)
        if log:
            row.logs = [*(row.logs or []), log][-30:]
        if values.get("status") == "failed" and _deleting(row):
            values.update(lease_token=None, lease_until=None)
        for name, value in values.items():
            if name == "error" and isinstance(value, str):
                value = value[:512]
            setattr(row, name, value)
        row.updated_at = datetime.utcnow()
        await session.commit()
        return True


async def _heartbeat(job_id: str, token: str, task: asyncio.Task, bot=None) -> None:
    """Продлевает аренду, обновляет карточку и передаёт отмену."""
    while True:
        await asyncio.sleep(5)
        try:
            async with async_session_maker() as session:
                row = await _row(session, job_id, lock=True)
                if row.lease_token != token or row.status == "cancel_requested" or row.pause_requested:
                    task.cancel()
                    return
                if row.status != "running":
                    return
                row.lease_until = datetime.utcnow() + timedelta(seconds=_LEASE_SECONDS)
                refresh = bot is not None and row.progress_message_id is not None
                await session.commit()
            if refresh:
                await _notify(bot, job_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            task.cancel()
            return


async def _notify(bot, job_id: str) -> None:
    """Обновляет только привязанную карточку, сохраняя очередь меню."""
    if bot is None:
        return
    try:
        async with async_session_maker() as session:
            row = await _row(session, job_id, lock=True)
            if row.progress_message_id is None:
                return
            view, chat_id = _view(row, include_credentials=not _deleting(row)), row.chat_id
            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=text, callback_data=f"rwi:{action}:{job_id}:{value}")]
                    for text, action, value in job_actions(view)
                ]
            )
            await bot.edit_message_text(
                job_status_text(view),
                chat_id=chat_id,
                message_id=view["progress_message_id"],
                reply_markup=keyboard,
                parse_mode="HTML",
                request_timeout=10,
            )
    except Exception:
        return


async def _execute_changes(snapshot: dict, checkpoint, log) -> dict:
    """Применяет только подтверждённые изменения существующих ресурсов."""
    from .changes import STEP_LABELS, changed_fields
    from .checks import check_origin_dns
    from .cloud import update_cdn
    from .panel import InstallerPanel
    from .profiles import build_profile
    from .remote import RemoteServer, apply_remote_changes, owner_id, preflight, prepare_remote_changes

    artifacts, secrets = snapshot["artifacts"], snapshot["secrets"]
    edit = artifacts["edit"]
    old, new = InstallSpec.from_dict(edit["before"]), InstallSpec.from_dict(edit["after"])
    remote_owner = artifacts.setdefault("remote_owner_id", owner_id(old))
    old._owner_id = new._owner_id = remote_owner
    changed = set(edit["changed"])
    operations = edit["operations"]
    panel_needed = bool(set(operations) & {"cascade", "profile", "node", "cloud", "verify", "squad", "host"})
    remote_needed = bool(set(operations) & {"connection", "prepare", "remote"})
    async with AsyncExitStack() as stack:
        panel = await stack.enter_async_context(InstallerPanel(old, checkpoint=checkpoint)) if panel_needed else None
        if panel:
            await panel.ensure_version()
        remote = await stack.enter_async_context(RemoteServer(new, secrets)) if remote_needed else None
        if remote and set(operations) & {"prepare", "remote"}:
            checking = copy.deepcopy(new)
            checking.addons = [addon for addon in new.addons if addon not in old.addons]
            checking.shaping = new.shaping if "shaping" in changed else {}
            await preflight(remote, checking)
        for step in operations:
            if step in edit["done"]:
                continue
            await log(STEP_LABELS[step], stage="edit:" + step)
            previous = InstallSpec.from_dict(edit.get("applied_specs", {}).get(step) or edit["before"])
            previous._owner_id = remote_owner
            if step == "connection":
                await log("Изменённый SSH-доступ подтверждён")
            elif step == "prepare":
                if new.transport in {"vless_tcp_selfsteal", "cdn_vk", "cdn_yandex"} and changed & {
                    "domain",
                    "transport",
                }:
                    await check_origin_dns(new)
                await prepare_remote_changes(remote, previous, new, artifacts, changed, checkpoint=checkpoint)
            elif step == "cascade":
                exit_snapshot = await _snapshot(new.exit_install_id, snapshot["owner_id"])
                exit_spec = InstallSpec.from_dict(exit_snapshot["params"])
                if (
                    exit_snapshot["status"] not in {"succeeded", "warning"}
                    or exit_spec.role != "exit"
                    or exit_spec.address == new.address
                ):
                    raise InstallError("cascade", "Выберите установленный выход каскада на другом сервере")
                if not edit.get("cascade_reset"):
                    if artifacts.get("cascade"):
                        artifacts.setdefault("retained_cascades", []).append(copy.deepcopy(artifacts["cascade"]))
                    artifacts.pop("cascade", None)
                    edit["cascade_reset"] = True
                    await checkpoint(artifacts)
                artifacts["cascade"] = await panel.ensure_cascade(exit_spec, exit_snapshot["artifacts"], artifacts)
            elif step == "profile":
                await panel.update_profile(new, artifacts, build_profile(new, artifacts), previous_spec=previous)
            elif step == "remote":
                await apply_remote_changes(remote, previous, new, artifacts, changed, log, checkpoint=checkpoint)
                if "addons" in changed and artifacts.get("profile_uuid"):
                    await panel.update_profile(new, artifacts, build_profile(new, artifacts), previous_spec=new)
            elif step == "node":
                await panel.update_node(previous, new, artifacts)
            elif step == "cloud":
                await update_cdn(
                    previous, new, secrets, artifacts, checkpoint, allow_recreate=edit["requires_replacement"]
                )
            elif step == "verify":
                if not await panel.check_online(new, artifacts):
                    started = edit.setdefault("verify_started_at", time.time())
                    await checkpoint(artifacts)
                    if time.time() - started < 180:
                        raise InstallWaitingError("Ожидание подключения ноды после изменения настроек", retry_after=30)
                    raise InstallError("verify", "Изменения применены, но панель ещё не подтвердила подключение ноды")
            elif step == "squad":
                await panel.move_to_squad(previous, new, artifacts)
            elif step == "host":
                await panel.update_host(previous, new, artifacts)
            edit["done"].append(step)
            edit.setdefault("applied_specs", {})[step] = new.to_dict()
            await checkpoint(artifacts)
    if edit["resume_install"]:
        done = set(artifacts.get("done", []))
        if not artifacts.get("profile_uuid"):
            done -= {"keys", "panel_profile"}
            if "transport" in changed:
                artifacts.pop("keys_generated", None)
        if not artifacts.get("node_uuid"):
            done.discard("panel_node")
        if not artifacts.get("remote_deployed"):
            done -= {"docker", "addons", "verify", "cdn", "panel_squad", "panel_host"}
        for operation, stage in {
            "cloud": "cdn",
            "verify": "verify",
            "squad": "panel_squad",
            "host": "panel_host",
        }.items():
            if operation in edit["done"]:
                done.add(stage)
        artifacts["done"] = [stage for stage in _STAGES if stage in done]
    return {"params": new.to_dict(), "fingerprint": new.fingerprint(), "resume_install": edit["resume_install"]}


async def _execute(snapshot: dict, checkpoint, log) -> dict | None:
    """Выполняет установку по сохранённым контрольным точкам."""
    from .addons import install_addons, validate_addons
    from .checks import check_origin_dns
    from .cloud import configure_cdn
    from .panel import InstallerPanel
    from .profiles import build_profile, generate_keys
    from .remote import RemoteServer, deploy_node, preflight, prepare_encryption, prepare_exit_ip

    spec = InstallSpec.from_dict(snapshot["params"])
    if spec.fingerprint() != snapshot["fingerprint"]:
        raise InstallError("snapshot", "Параметры установки были изменены после подтверждения", retryable=False)
    secrets, artifacts = snapshot["secrets"], snapshot["artifacts"]
    if artifacts.get("edit"):
        artifacts["secrets"] = secrets
        return await _execute_changes(snapshot, checkpoint, log)
    from .remote import owner_id

    spec._owner_id = artifacts.setdefault("remote_owner_id", owner_id(spec))
    _require_credentials(spec, secrets)
    validate_addons(spec, secrets)
    artifacts.setdefault("install_id", snapshot["id"])
    artifacts.setdefault("done", [])
    artifacts["secrets"] = secrets
    async with InstallerPanel(spec, checkpoint=checkpoint) as panel:
        await panel.ensure_version()
        async with RemoteServer(spec, secrets) as remote:
            await preflight(remote, spec)
            await check_origin_dns(spec)
            await prepare_exit_ip(remote, spec, artifacts, checkpoint=checkpoint)
            if spec.role == "entry":
                exit_snapshot = await _snapshot(spec.exit_install_id, snapshot["owner_id"])
                exit_spec = InstallSpec.from_dict(exit_snapshot["params"])
                if exit_snapshot["status"] not in {"succeeded", "warning"} or exit_spec.role != "exit":
                    raise InstallError("cascade", "Выбранная выходная нода ещё не установлена", retryable=False)
                if exit_spec.address == spec.address:
                    raise InstallError(
                        "cascade", "Вход и выход каскада должны находиться на разных серверах", retryable=False
                    )
                artifacts["cascade"] = await panel.ensure_cascade(exit_spec, exit_snapshot["artifacts"], artifacts)
                await checkpoint(artifacts)
            confirmed_runtime = "docker" in artifacts["done"]
            previous_update = artifacts.get("node_image_update") or {}
            resuming_runtime = bool(
                previous_update.get("target_image") == NODE_IMAGE
                and previous_update.get("params_fingerprint") == snapshot["fingerprint"]
                and previous_update.get("runtime_only") is True
            )
            runtime_upgrade = bool(
                (confirmed_runtime or resuming_runtime or artifacts.get("remote_deployed"))
                and artifacts.get("deployed_node_image") != NODE_IMAGE
            )
            if runtime_upgrade:
                confirmed_runtime = confirmed_runtime or resuming_runtime
                artifacts["node_image_update"] = {
                    "target_image": NODE_IMAGE,
                    "params_fingerprint": snapshot["fingerprint"],
                    "runtime_only": confirmed_runtime,
                }
                artifacts["done"] = [stage for stage in artifacts["done"] if stage not in {"docker", "verify"}]
                artifacts.pop("verify_started_at", None)
                await checkpoint(artifacts)
            verified_now = False
            for stage in _STAGES:
                if stage in artifacts["done"]:
                    continue
                await log("Этап: " + stage, stage=stage)
                if stage == "keys":
                    if not artifacts.get("keys_generated"):
                        artifacts.update(generate_keys(spec))
                        artifacts["keys_generated"] = True
                        await checkpoint(artifacts)
                    if spec.transport == "vless_encryption":
                        await prepare_encryption(remote, spec, artifacts, checkpoint)
                elif stage == "panel_profile":
                    await panel.ensure_profile(spec, artifacts, build_profile(spec, artifacts))
                elif stage == "panel_node":
                    await panel.ensure_node(spec, artifacts)
                    await panel.fetch_node_secret(artifacts)
                elif stage == "docker":
                    await deploy_node(
                        remote,
                        spec,
                        artifacts,
                        log,
                        checkpoint=checkpoint,
                        runtime_only=runtime_upgrade and confirmed_runtime,
                    )
                    artifacts["deployed_node_image"] = NODE_IMAGE
                    artifacts.pop("node_image_update", None)
                elif stage == "addons":
                    await install_addons(remote, spec, artifacts, log, checkpoint=checkpoint)
                    await panel.update_profile(spec, artifacts, build_profile(spec, artifacts))
                elif stage == "cdn" and spec.transport.startswith("cdn_"):
                    await configure_cdn(spec, secrets, artifacts, checkpoint)
                elif stage == "panel_squad":
                    await panel.add_to_squad(spec, artifacts)
                elif stage == "panel_host" and spec.publish_host:
                    if not verified_now and not await panel.check_online(spec, artifacts):
                        raise InstallError("verify", "Нода отключилась во время ожидания DNS; хост ещё не опубликован")
                    await panel.ensure_host(spec, artifacts)
                elif stage == "verify":
                    if not await panel.check_online(spec, artifacts):
                        started = artifacts.setdefault("verify_started_at", time.time())
                        await checkpoint(artifacts)
                        if time.time() - started < 180:
                            raise InstallWaitingError("Ожидание подтверждения подключения ноды панелью", retry_after=30)
                        raise InstallError(
                            "verify",
                            "Панель ещё не подтвердила подключение ноды. Проверьте порт управления, адрес панели и журнал контейнера",
                        )
                    verified_now = True
                artifacts["done"].append(stage)
                await checkpoint(artifacts)
            artifacts["installed"] = True
            await checkpoint(artifacts)


async def _cleanup_cascade_contexts(snapshot: dict, checkpoint) -> None:
    """Восстанавливает старые связи каскада только по однозначному снимку выхода."""
    artifacts = snapshot["artifacts"]
    cascades = [item for item in [artifacts.get("cascade"), *artifacts.get("retained_cascades", [])] if item]
    unresolved = [
        item
        for item in cascades
        if isinstance(item, dict) and not all(item.get(key) for key in ("panel_ref", "exit_install_id", "inbound_uuid"))
    ]
    if not unresolved:
        return
    candidates = []
    async with async_session_maker() as session:
        rows = (
            await session.execute(select(RemnawaveInstall).where(RemnawaveInstall.owner_id == snapshot["owner_id"]))
        ).scalars()
        for row in rows:
            if row.id == snapshot["id"]:
                continue
            try:
                saved = vault.open(row.id, row.encrypted_artifacts)
            except InstallError:
                continue
            if saved.get("install_id") != row.id or not saved.get("inbound_uuid"):
                continue
            for params in _approved_specs(row, saved):
                if params.get("role") != "exit":
                    continue
                candidates.append({
                    "address": params.get("address"),
                    "port": params.get("client_port", 443),
                    "public_key": saved.get("public_key"),
                    "panel_ref": params.get("panel_ref"),
                    "exit_install_id": row.id,
                    "inbound_uuid": saved["inbound_uuid"],
                    **{key: saved[key] for key in ("profile_uuid", "node_uuid") if saved.get(key)},
                })
    contexts = artifacts.setdefault("cleanup_cascade_contexts", {})
    edit = artifacts.get("edit") or {}
    entry_specs = [
        snapshot["params"],
        *[edit.get(key) for key in ("before", "after", "previous_target")],
        *(edit.get("applied_specs") or {}).values(),
    ]
    exit_ids = {
        item.get("exit_install_id") for item in entry_specs if isinstance(item, dict) and item.get("exit_install_id")
    }
    for cascade in unresolved:
        nonce = cascade.get("nonce")
        if not isinstance(nonce, str) or not nonce:
            raise InstallError(
                "panel_cleanup",
                "У старого каскада отсутствует идентификатор владельца; запись сохранена",
                retryable=False,
            )
        complete = all(cascade.get(key) for key in ("address", "port", "public_key"))
        matches = {
            json.dumps(
                {key: value for key, value in item.items() if key not in {"address", "port", "public_key"}},
                sort_keys=True,
            )
            for item in candidates
            if all(cascade.get(key) is None or item[key] == cascade[key] for key in ("address", "port", "public_key"))
            and (complete or (len(exit_ids) == 1 and item["exit_install_id"] in exit_ids))
        }
        if len(matches) != 1:
            raise InstallError(
                "panel_cleanup",
                "Не удалось однозначно определить собственную выходную ноду старого каскада; запись сохранена",
                retryable=False,
            )
        context = json.loads(matches.pop())
        if contexts.get(nonce) not in (None, context):
            raise InstallError(
                "panel_cleanup",
                "Сохранённая связь каскада противоречит выходной ноде; запись сохранена",
                retryable=False,
            )
        contexts[nonce] = context
    await checkpoint(artifacts)


async def _finish_deletion(job_id: str, token: str) -> tuple[int, int | None]:
    """Удаляет запись после очистки и сохраняет адрес открытого меню."""
    async with async_session_maker() as session:
        row = await _row(session, job_id, lock=True)
        if row.lease_token != token or row.status != "running" or row.pause_requested:
            raise asyncio.CancelledError
        await _authorize(row.owner_id)
        await _guard_dependents(session, row)
        artifacts = vault.open(row.id, row.encrypted_artifacts)
        if not (artifacts.get("panel_cleanup") or {}).get("completed"):
            raise InstallError("panel_cleanup", "Очистка панели не подтверждена; запись сохранена", retryable=False)
        anchor = row.chat_id, row.progress_message_id
        await session.delete(row)
        await session.commit()
        return anchor


async def _notify_deleted(bot, anchor: tuple[int, int | None]) -> None:
    """Показывает успех только после фиксации удаления записи."""
    if bot is None or anchor[1] is None:
        return
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    with suppress(Exception):
        await bot.edit_message_text(
            "Запись и объекты панели удалены",
            chat_id=anchor[0],
            message_id=anchor[1],
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="Все установки", callback_data="rwi:list::")]]
            ),
            request_timeout=10,
        )


async def _execute_deletion(snapshot: dict, checkpoint, log) -> None:
    """Очищает панель без обращения к SSH и сохранённым паролям."""
    from .panel_cleanup import cleanup_panel

    artifacts = snapshot["artifacts"]
    if not isinstance(artifacts.get("deletion"), dict):
        raise InstallError("panel_cleanup", "План удаления недоступен; запись сохранена", retryable=False)
    identifier = artifacts.get("install_id")
    if identifier and identifier != snapshot["id"]:
        raise InstallError(
            "panel_cleanup", "Снимок объектов относится к другой установке; запись сохранена", retryable=False
        )
    if not identifier:
        if any(
            artifacts.get(key)
            for key in ("profile_uuid", "inbound_uuid", "node_uuid", "host_uuid", "cascade", "retained_cascades")
        ):
            raise InstallError(
                "panel_cleanup",
                "В снимке объектов отсутствует идентификатор установки; запись сохранена",
                retryable=False,
            )
        artifacts["install_id"] = snapshot["id"]
        await checkpoint(artifacts)
    await _cleanup_cascade_contexts(snapshot, checkpoint)

    async def panel_log(stage):
        label = build_progress("running", stage, artifacts, _STAGES, snapshot["params"])["current"]
        await log(label, stage=stage)

    await cleanup_panel(InstallSpec.from_dict(snapshot["params"], complete=False), artifacts, checkpoint, panel_log)
    if not (artifacts.get("panel_cleanup") or {}).get("completed"):
        raise InstallError(
            "panel_cleanup", "Панель не подтвердила завершение очистки; запись сохранена", retryable=False
        )
    artifacts["deletion"]["done"] = ["panel_cleanup"]
    await checkpoint(artifacts)
    await log("Удаление записи из бота", stage="delete:record")


async def process_job(job_id: str, bot=None) -> None:
    """Захватывает задачу, выполняет ввод-вывод и закрывает аренду."""
    token, now = uuid.uuid4().hex, datetime.utcnow()
    deletion, removed = False, False
    async with async_session_maker() as session:
        row = await _row(session, job_id, lock=True)
        if row.status not in _ACTIVE or (row.lease_until and row.lease_until > now):
            return
        if row.pause_requested:
            row.status, row.pause_requested, row.lease_token, row.lease_until = "paused", False, None, None
            row.next_retry_at = None
            await session.commit()
            return
        if row.status == "cancel_requested":
            row.status, row.lease_token, row.lease_until = "canceled", None, None
            await session.commit()
            return
        deletion = _deleting(row)
        row.status, row.lease_token, row.lease_until = "running", token, now + timedelta(seconds=_LEASE_SECONDS)
        row.attempt_count += 1
        row.error, row.next_retry_at = None, None
        await session.commit()
    snapshot, heartbeat = None, None
    try:
        snapshot = await _snapshot(job_id, include_secrets=False) if deletion else await _snapshot(job_id)
        await _authorize(snapshot["owner_id"])
        artifacts, secrets = snapshot["artifacts"], snapshot["secrets"]

        async def checkpoint(value):
            cleaned = {key: item for key, item in value.items() if key != "secrets"}
            await _save(job_id, token, artifacts=cleaned)

        async def log(message, *, stage=None):
            values = {"stage": stage} if stage else {}
            cleaned = {key: item for key, item in artifacts.items() if key != "secrets"}
            await _save(job_id, token, artifacts=cleaned, log=_redact(message, secrets, artifacts), **values)

        heartbeat = asyncio.create_task(_heartbeat(job_id, token, asyncio.current_task(), bot=bot))
        if deletion:
            async with async_session_maker() as session:
                current = await _row(session, job_id, lock=True)
                if current.lease_token != token or current.pause_requested or current.status != "running":
                    raise asyncio.CancelledError
                await _guard_dependents(session, current)
            await _execute_deletion(snapshot, checkpoint, log)
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
            heartbeat = None
            anchor = await _finish_deletion(job_id, token)
            removed = True
            await _notify_deleted(bot, anchor)
            return
        result = await _execute(snapshot, checkpoint, log)
        warnings = snapshot["artifacts"].get("warnings", [])
        values = {}
        if result:
            edit = artifacts.pop("edit")
            artifacts["last_edit"] = {"fields": edit["changed"], "finished_at": time.time()}
            if not result["resume_install"]:
                artifacts["last_edit"]["progress"] = {
                    "operations": list(edit.get("operations", [])),
                    "done": list(edit.get("done", [])),
                }
            artifacts.pop("dns_wait", None)
            values.update(
                params=result["params"], fingerprint=result["fingerprint"], address=result["params"]["address"]
            )
        resume = bool(result and result["resume_install"])
        await _save(
            job_id,
            token,
            artifacts={key: value for key, value in artifacts.items() if key != "secrets"},
            status="queued" if resume else "warning" if warnings else "succeeded",
            stage="preflight" if resume else "done",
            next_retry_at=datetime.utcnow() if resume else None,
            dns_records=[],
            error=_redact("; ".join(warnings), secrets, artifacts) if warnings else None,
            **values,
        )
    except InstallWaitingError as error:
        safe = _redact(
            error.message, snapshot["secrets"] if snapshot else {}, snapshot["artifacts"] if snapshot else {}
        )
        next_retry = datetime.utcnow() + timedelta(seconds=max(30, error.retry_after))
        if error.records and snapshot:
            marker = json.dumps(error.records, sort_keys=True)
            waiting = snapshot["artifacts"].get("dns_wait", {})
            if waiting.get("records") != marker:
                waiting = {"records": marker, "started_at": time.time()}
                snapshot["artifacts"]["dns_wait"] = waiting
            if time.time() - waiting["started_at"] >= 3600:
                next_retry = None
                safe += ". Автопроверка приостановлена; нажмите «Проверить» после изменения DNS"
            await checkpoint(snapshot["artifacts"])
        await _save(
            job_id,
            token,
            status="waiting_dns",
            error=safe,
            dns_records=error.records,
            next_retry_at=next_retry,
        )
    except (InstallError, ValueError) as error:
        safe = _redact(str(error), snapshot["secrets"] if snapshot else {}, snapshot["artifacts"] if snapshot else {})
        await _save(
            job_id,
            token,
            status="failed",
            error=safe,
            retryable=getattr(error, "retryable", True),
            stage=("delete:" if deletion else "") + getattr(error, "step", "validation").removeprefix("delete:"),
        )
    except asyncio.CancelledError:
        if removed:
            raise
        async with async_session_maker() as session:
            row = await _row(session, job_id, lock=True)
            if row.lease_token == token:
                if row.status == "cancel_requested":
                    row.status, row.stage = "canceled", "delete:stopped" if deletion else "stopped"
                elif row.pause_requested:
                    row.status, row.pause_requested = "paused", False
                    row.next_retry_at = None
                else:
                    row.status, row.error = (
                        "failed",
                        "Работа прервана. Проверьте состояние сервера и нажмите «Повторить»",
                    )
                await session.commit()
        raise
    except Exception:
        await _save(
            job_id,
            token,
            status="failed",
            error="Непредвиденная ошибка установщика. Сохранённые этапы доступны для повторного запуска",
            **({"stage": "delete:panel_cleanup"} if deletion else {}),
        )
    finally:
        if heartbeat:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
        if not removed:
            async with async_session_maker() as session:
                row = (
                    await session.execute(
                        select(RemnawaveInstall).where(RemnawaveInstall.id == job_id).with_for_update()
                    )
                ).scalar_one_or_none()
                if row is not None and row.lease_token == token:
                    if row.status == "cancel_requested":
                        row.status, row.stage = "canceled", "delete:stopped" if deletion else "stopped"
                    elif row.pause_requested:
                        row.status, row.pause_requested, row.next_retry_at = "paused", False, None
                    row.lease_token, row.lease_until = None, None
                await session.commit()
            await _notify(bot, job_id)


async def installer_loop(bot, sessionmaker=None) -> None:
    """Возобновляет устойчивую очередь и удаляет просроченные доступы."""
    from logger import logger

    active: dict[str, asyncio.Task] = {}
    try:
        while True:
            for job_id, task in list(active.items()):
                if task.done():
                    with suppress(asyncio.CancelledError, Exception):
                        task.result()
                    del active[job_id]
            try:
                now = datetime.utcnow()
                async with async_session_maker() as session:
                    await session.execute(
                        update(RemnawaveInstall)
                        .where(
                            RemnawaveInstall.secrets_expire_at <= now,
                            RemnawaveInstall.status.not_in(["running", "cancel_requested"]),
                        )
                        .values(encrypted_secrets=None, secrets_expire_at=None)
                    )
                    due = (
                        (
                            await session.execute(
                                select(RemnawaveInstall.id)
                                .where(
                                    RemnawaveInstall.status.in_(_ACTIVE),
                                    or_(
                                        RemnawaveInstall.status != "waiting_dns",
                                        RemnawaveInstall.next_retry_at.is_not(None),
                                    ),
                                    or_(RemnawaveInstall.lease_until.is_(None), RemnawaveInstall.lease_until <= now),
                                    or_(
                                        RemnawaveInstall.next_retry_at.is_(None), RemnawaveInstall.next_retry_at <= now
                                    ),
                                )
                                .order_by(RemnawaveInstall.created_at)
                                .limit(10)
                            )
                        )
                        .scalars()
                        .all()
                    )
                    await session.commit()
                for job_id in due:
                    if len(active) >= 2:
                        break
                    if job_id not in active:
                        active[job_id] = asyncio.create_task(
                            process_job(job_id, bot), name="remnawave-install:" + job_id
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("[RemnawaveInstaller] Очередь временно недоступна")
            await asyncio.sleep(5)
    finally:
        for task in active.values():
            task.cancel()
        await asyncio.gather(*active.values(), return_exceptions=True)

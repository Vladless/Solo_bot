import hashlib
import io
import ipaddress
import json
import logging
import re
import unicodedata

from html import escape
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.telegram_sensitivity import register_sensitive_states
from middlewares.session import release_session_early
from services.remnawave_installer import jobs
from services.remnawave_installer.naming import node_name_from_host, node_name_length
from services.remnawave_installer.progress import job_actions, job_status_text
from services.remnawave_installer.types import (
    ADDONS,
    CERTIFICATE_TRANSPORTS,
    DELETABLE_STATUSES,
    ROLES,
    TRANSPORTS,
    InstallError,
    InstallSpec,
)

from ..panel.keyboard import AdminPanelCallback


router = Router(name="admin_settings_remnawave_install")
logger = logging.getLogger(__name__)


class RemnaInstallState(StatesGroup):
    value = State()
    secret = State()


register_sensitive_states(RemnaInstallState.secret)


class InstallCallback(CallbackData, prefix="rwi"):
    action: str
    job: str = ""
    value: str = ""


FIELDS = {
    "nm": ("name", "Название ноды", "3–30 символов, включая кириллицу; без управляющих символов."),
    "ip": ("address", "IP сервера", "Укажите IPv4 или IPv6 сервера."),
    "us": ("ssh_username", "SSH-логин", "Например: root."),
    "sp": ("ssh_port", "SSH-порт", "Целое число от 1 до 65535."),
    "np": ("node_port", "Порт управления нодой", "Обычно 2222/TCP; должен отличаться от SSH-порта."),
    "ps": (
        "panel_sources",
        "Адреса панели",
        "Необязательно: IP/CIDR панели через пробел для ограничения доступа к управлению. «-» убирает ограничение по IP; проверка сертификата панели сохраняется.",
    ),
    "ss": (
        "trusted_ssh_sources",
        "Доверенные адреса SSH",
        "IP/CIDR через пробел; «-» разрешает SSH без ограничения по источнику.",
    ),
    "hs": (
        "haproxy_sources",
        "IP входных серверов HAProxy",
        "Точные IPv4/IPv6 входных серверов через пробел. Порт выхода будет доступен только этим адресам; сети и открытый доступ запрещены.",
    ),
    "cp": ("client_port", "Порт клиентов", "Обычно 443; Hysteria2 использует UDP, остальные транспорты — TCP."),
    "rd": ("reality_domain", "Домен Reality", "Домен сайта-донора без протокола и пути."),
    "dm": ("domain", "Домен сайта / origin", "Домен без протокола и пути; его DNS должен указывать на IP сервера."),
    "cd": ("cdn_domain", "Домен CDN", "Отдельный домен для CDN, отличный от origin."),
    "em": ("email", "Email сертификата", "Адрес для Let's Encrypt."),
    "pn": (
        "profile_name",
        "Имя Config Profile",
        "2–29 символов: латиница, цифры, пробел, дефис и подчёркивание; «-» создаст имя автоматически.",
    ),
    "hr": (
        "host_remark",
        "Название хоста",
        "До 100 символов; «-» использует название ноды. Имя новой ноды создаётся из названия хоста без эмодзи и сокращается до 30 символов.",
    ),
    "ei": ("exit_ip", "Выходной IP", "Дополнительный IP сервера; «-» использует основной адрес."),
    "ef": ("exit_interface", "Интерфейс выходного IP", "Например: eth0; «-» для автоматического определения."),
    "fd": ("folder_id", "Каталог Яндекс Cloud", "ID каталога; «-» выберет первый доступный каталог."),
    "vp": (
        "project_id",
        "ID проекта VK Cloud",
        "Скопируйте ID проекта из личного кабинета VK Cloud. Это не токен доступа.",
    ),
}
SECRET_FIELDS = {
    "pw": ("ssh_password", "Пароль SSH", "Отправьте пароль одним сообщением."),
    "pk": (
        "ssh_private_key",
        "Ключ для входа по SSH",
        "Отправьте приватный ключ для входа или документ размером до 64 КБ. Это доступ к учётной записи; ключ самого сервера бот проверяет отдельно.",
    ),
    "pp": ("ssh_key_passphrase", "Пароль приватного ключа", "Отправьте пароль ключа; «-» если ключ без пароля."),
    "vk": ("vk_token", "Токен VK Cloud", "Отправьте токен доступа к CDN VK Cloud."),
    "vd": (
        "vk_user_domain",
        "Домен пользователя VK Cloud",
        "Создайте пользователя в VK Cloud: «Управление доступами → Сервисные пользователи», роль — администратор проекта. Отправьте точное значение OS_USER_DOMAIN_NAME из OpenStack RC. Не подставляйте default вместо значения из файла.",
    ),
    "vu": (
        "vk_username",
        "Логин сервисной учётной записи VK",
        "Отправьте логин созданного сервисного пользователя VK Cloud.",
    ),
    "vw": ("vk_password", "Пароль сервисной учётной записи VK", "Отправьте пароль сервисного пользователя VK Cloud."),
    "ya": (
        "yandex_key",
        "Ключ Яндекс Cloud",
        "Отправьте файл .json, скачанный при создании авторизованного ключа сервисного аккаунта Яндекс Cloud (до 64 КБ). Можно также вставить содержимое JSON текстом.",
    ),
    "pc": (
        "psiphon_config",
        "Собственная конфигурация Psiphon",
        "Необязательно: отправьте JSON-конфигурацию Psiphon с PropagationChannelId, SponsorId и данными серверов. Можно документом до 64 КБ. «-» возвращает автоматическую настройку.",
    ),
}
SHAPING_FIELDS = {
    "sc": ("ceiling_mbps", "Потолок, Мбит/с", 200),
    "sr": ("penalty_mbps", "Скорость штрафа, Мбит/с", 100),
    "st": ("threshold_seconds", "Время у потолка, секунд", 30),
    "sd": ("penalty_seconds", "Длительность штрафа, секунд", 300),
    "sv": ("volume_gb", "Лимит объёма, ГБ (0 — выключен)", 0),
    "sw": ("volume_window_hours", "Окно объёма, часов", 1),
}
STATUSES = {
    "draft": "Черновик",
    "queued": "В очереди",
    "running": "Установка",
    "waiting_dns": "Ожидание DNS / облака",
    "failed": "Ошибка",
    "interrupted": "Прервана",
    "succeeded": "Готово",
    "warning": "Готово с предупреждением",
    "canceled": "Отменена",
    "cancel_requested": "Останавливается",
    "paused": "Приостановлена для изменения настроек",
    "pause_requested": "Останавливается с сохранением доступа",
}
BASIC_STEPS = {"1": "Сервер", "2": "Транспорт / CDN", "3": "Сквад и хост", "4": "Дополнения"}
STEP_NAMES = {
    **BASIC_STEPS,
    "type": "Схема установки",
    "cascade_type": "Тип каскада",
    "routes": "Выходы и страны",
    "cascade_target": "Нода 2 — выход HAProxy",
    "advanced": "Дополнительные настройки",
    "ssh": "Настройки SSH",
    "server": "Доступ и название",
    "network": "Порты и доступ",
    "profile": "Профиль",
    "cascade": "Каскад и выходной IP",
}
EDITABLE_STATUSES = {"draft"}
SETTINGS_EDIT_STATUSES = {"draft", "failed", "waiting_dns", "paused", "succeeded", "warning"}
SECRET_EDIT_STATUSES = SETTINGS_EDIT_STATUSES


def _button(builder, text: str, action: str, job: str = "", value: str = "") -> None:
    """Добавляет короткую кнопку действия установщика."""
    builder.row(
        InlineKeyboardButton(text=text, callback_data=InstallCallback(action=action, job=job, value=value).pack())
    )


def _params(job: dict) -> dict:
    """Дополняет безопасные параметры значениями по умолчанию."""
    return {**InstallSpec().to_dict(), **(job.get("pending_params") or job.get("params") or {})}


def _display(value) -> str:
    """Показывает непустое значение без HTML-разметки."""
    if type(value) is bool:
        return "да" if value else "нет"
    if value == 0:
        return "0"
    if isinstance(value, list):
        value = ", ".join(str(item) for item in value)
    return escape(str(value or "не задано"))


def _copyable(value) -> str:
    """Выделяет заполненное значение для копирования."""
    return f"<code>{_display(value)}</code>" if value or value == 0 else _display(value)


def _endpoint(params: dict) -> str:
    """Связывает подтверждение ключа с адресом подключения."""
    value = json.dumps([params.get("address"), params.get("ssh_port")])
    return hashlib.sha256(value.encode()).hexdigest()


def _missing_screen(error: TelegramBadRequest) -> bool:
    """Распознаёт потерянное или недоступное для изменения меню."""
    value = str(error).lower()
    return any(
        part in value for part in ("message to edit not found", "message can't be edited", "message can not be edited")
    )


async def _send(event, text: str, builder: InlineKeyboardBuilder, *, state: FSMContext) -> Message | None:
    """Обновляет единственное меню после кнопки или текстового ввода."""
    callback = isinstance(event, CallbackQuery)
    source = event.message if callback else event
    data = await state.get_data()
    chat_id = source.chat.id
    message_id = source.message_id if callback else data.get("_screen_message_id")
    if not callback and data.get("_screen_chat_id") != chat_id:
        message_id = None
    result = None
    if message_id:
        try:
            if callback:
                result = await source.edit_text(text, reply_markup=builder.as_markup())
            else:
                result = await source.bot.edit_message_text(
                    text, chat_id=chat_id, message_id=message_id, reply_markup=builder.as_markup(), parse_mode="HTML"
                )
        except TelegramBadRequest as error:
            if "message is not modified" in str(error).lower():
                result = source if callback else None
            elif _missing_screen(error):
                message_id = None
            else:
                raise
    if not message_id:
        result = await source.answer(text, reply_markup=builder.as_markup())
        message_id = result.message_id
    await state.update_data(_screen_chat_id=chat_id, _screen_message_id=message_id)
    return result


async def _clear_input(state: FSMContext) -> None:
    """Сбрасывает ввод, сохраняя адрес единственного меню."""
    data = await state.get_data()
    context = {key: data[key] for key in ("_screen_chat_id", "_screen_message_id", "_progress_job_id") if key in data}
    await state.clear()
    await state.update_data(**context)


async def _detach_progress(state: FSMContext, owner: int, job_id: str = "") -> None:
    """Освобождает меню от фонового обновления перед навигацией."""
    data = await state.get_data()
    for current in dict.fromkeys([data.get("_progress_job_id"), job_id]):
        if current:
            try:
                await jobs.bind_progress_message(current, owner, None)
            except InstallError as error:
                if error.step != "permission":
                    raise
    await state.update_data(_progress_job_id=None)


async def _report_error(
    event, state: FSMContext, text: str, job_id: str = "", *, host_key_changed: bool = False, deleting: bool = False
) -> None:
    """Показывает ошибку внутри меню, сохраняя возможность повторить ввод."""
    data = await state.get_data()
    builder = InlineKeyboardBuilder()
    if job_id:
        if host_key_changed:
            _button(builder, "Проверить новый ключ сервера", "resetfp", job_id)
        if not deleting:
            _button(
                builder,
                "← Назад к настройкам",
                "step",
                job_id,
                data.get("step") if data.get("step") in STEP_NAMES else "1",
            )
        _button(builder, "Карточка установки", "show", job_id)
        if not deleting and "порт" in text.lower():
            _button(builder, "Порты и доступ", "step", job_id, "network")
    _button(builder, "Все установки", "list")
    field = data.get("field")
    prompt = SECRET_FIELDS.get(field) or FIELDS.get(field) or SHAPING_FIELDS.get(field)
    lines = [
        "<b>Не удалось удалить установку</b>"
        if deleting
        else "<b>Изменился ключ сервера</b>"
        if host_key_changed
        else "<b>Не удалось сохранить настройку</b>",
        escape(text[:1000]),
    ]
    if prompt and await state.get_state():
        lines.extend(["", "Поле: " + escape(str(prompt[1])), "Отправьте исправленное значение ещё раз."])
    await _send(event, "\n".join(lines), builder, state=state)


def _log_unexpected(error: Exception, action: str, job_id: str = "") -> None:
    """Записывает место сбоя без текста исключения и секретных переменных."""
    action = action if re.fullmatch(r"[a-z]{1,24}", action) else "unknown"
    job_id = job_id if re.fullmatch(r"[a-f0-9]{32}", job_id or "") else "unknown"
    frames = []
    frame = error.__traceback__
    while frame is not None:
        filename = frame.tb_frame.f_code.co_filename.rsplit("/", 1)[-1]
        if re.fullmatch(r"[A-Za-z0-9_.-]+\.py", filename):
            frames.append(f"{filename}:{frame.tb_lineno}")
        frame = frame.tb_next
    logger.error(
        "Remnawave installer error: action=%s job=%s type=%s source=%s",
        action,
        job_id,
        type(error).__name__,
        " > ".join(frames[-6:]),
    )


async def _reset_input(state: FSMContext, **values: Any) -> None:
    """Сбрасывает ввод, сохраняя несекретный черновик изменений."""
    data = await state.get_data()
    context = {
        key: data[key]
        for key in (
            "edit_job",
            "edit_mode",
            "pending_params",
            "preview_token",
            "wizard_step",
            "squad_label",
            "exit_label",
            "server_field",
            "vk_auth_setup",
            "route_exit_id",
            "routes_page",
        )
        if key in data
    }
    await _clear_input(state)
    await state.update_data(**{**context, **values})


async def _effective_job(job: dict, state: FSMContext) -> dict:
    """Подставляет ещё не применённые параметры только своего черновика."""
    job = {key: value for key, value in job.items() if key != "squad_name"}
    data = await state.get_data()
    if data.get("edit_mode") and data.get("edit_job") == job["id"]:
        params = data.get("pending_params") or _params(job)
        job = {**job, "params": params, "pending_params": params}
    label = data.get("squad_label") or {}
    if label.get("job_id") == job["id"] and label.get("uuid") == _params(job)["squad_uuid"]:
        job = {**job, "squad_name": label.get("name")}
    exit_label = data.get("exit_label") or {}
    if exit_label.get("job_id") == job["id"] and exit_label.get("exit_install_id") == _params(job)["exit_install_id"]:
        job = {**job, "haproxy": exit_label}
    pair = job.get("cascade_pair") or {}
    if pair.get("entry_id") == job["id"]:
        target = await jobs.get_job(pair["exit_id"], state.key.user_id)
        params = _params(target)
        job = {
            **job,
            "cascade_exit": target,
            "haproxy": {
                "exit_install_id": target["id"],
                "name": params["name"],
                "address": params["address"],
                "port": params["client_port"],
                "squad_uuid": params["squad_uuid"],
            },
        }
    return job


async def _save(job: dict, owner: int, values: dict, state: FSMContext) -> dict:
    """Сохраняет полные параметры без помещения секретов в FSM."""
    params = {**_params(job), **values}
    if any(key in values and values[key] != _params(job).get(key) for key in ("address", "ssh_port")):
        params["host_fingerprint"] = ""
    if (
        params["transport"].startswith("cdn_")
        or params["transport"] == "hysteria2"
        or params["role"] in {"exit", "haproxy_entry", "haproxy_exit"}
    ):
        params["shaping"] = {}
    data = await state.get_data()
    if job["status"] == "draft" and not data.get("edit_mode") and {"ssh_port", "transport"}.intersection(values):
        reserved = {params["ssh_port"], params["client_port"], 80, 443, 4443, 7443, 8443}
        if params["node_port"] == 2222 and params["ssh_port"] == 2222:
            params["node_port"] = next(port for port in range(2223, 2232) if port not in reserved)
        if (
            params["client_port"] == 443
            and params["ssh_port"] == 443
            and not params["transport"].startswith("cdn_")
            and params["transport"] != "hysteria2"
        ):
            params["client_port"] = next(port for port in (9443, 10443, 11443) if port != params["node_port"])
    if data.get("edit_mode") and data.get("edit_job") == job["id"]:
        params = InstallSpec.from_dict(params, complete=False).to_dict()
        await state.update_data(pending_params=params, preview_token=None)
        return {**job, "params": params, "pending_params": params}
    return await jobs.update_draft(job["id"], owner, params=params)


def _choices(builder, choices: list[tuple[str, str, str]], job_id: str) -> None:
    """Размещает короткие варианты по два в строке."""
    buttons = [
        InlineKeyboardButton(text=title, callback_data=InstallCallback(action=action, job=job_id, value=value).pack())
        for title, action, value in choices
    ]
    for index in range(0, len(buttons), 2):
        builder.row(*buttons[index : index + 2])


def _secret_status(job: dict, field: str) -> str:
    """Показывает наличие секрета без его значения."""
    return "введён" if field in (job.get("credential_fields") or []) else "не введён"


def _vk_auth_fields(params: dict) -> tuple[str, ...]:
    """Выбирает поля доступа VK Cloud для текущего режима."""
    return ("vd", "vu", "vw") if params["cloud"].get("auth_mode", "token") == "service_account" else ("vk",)


def _deletion_secret_codes(job: dict) -> tuple[str, ...]:
    """Разрешает обновить только доступы для сохранённых целей удаления."""
    params = _params(job)
    resources = [(job.get("resources") or {}).get("cdn"), *(job.get("retained_resources") or [])]
    providers = {
        resource["provider"]
        for resource in resources
        if isinstance(resource, dict) and resource.get("provider") in ("vk", "yandex")
    }
    providers.update(provider for provider in job.get("cloud_providers") or [] if provider in ("vk", "yandex"))
    if params["transport"] == "cdn_vk":
        providers.add("vk")
    if params["transport"] == "cdn_yandex":
        providers.add("yandex")
    if job.get("has_cloud_resources") and not providers:
        providers.update({"vk", "yandex"})
    return (
        "pw",
        "pk",
        "pp",
        *(("vk", "vd", "vu", "vw") if "vk" in providers else ()),
        *(("ya",) if "yandex" in providers else ()),
    )


async def _deletion_credentials_screen(event, state: FSMContext, job: dict) -> None:
    """Показывает доступы для очистки без изменения целевых ресурсов."""
    await _clear_input(state)
    await state.update_data(
        job_id=job["id"],
        deletion_credentials=bool(job.get("deletion")),
        deletion_prep_job=None if job.get("deletion") else job["id"],
    )
    builder = InlineKeyboardBuilder()
    for code in _deletion_secret_codes(job):
        _button(builder, SECRET_FIELDS[code][1], "s", job["id"], code)
    _button(builder, "← Карточка установки", "show", job["id"])
    await _send(
        event,
        "<b>Доступы для удаления</b>\n"
        f"Сервер: {_copyable(_params(job)['address'])}\n"
        "Обновите SSH-доступ или учётные данные облака. Целевой сервер и список ресурсов сохранятся. Затем откройте удаление из карточки.",
        builder,
        state=state,
    )


def _vk_missing_auth(job: dict) -> tuple[str, bool]:
    """Находит следующее незаполненное поле доступа VK Cloud."""
    params = _params(job)
    if not params["cloud"].get("project_id"):
        return "vp", False
    credentials = set(job.get("credential_fields") or [])
    for code in _vk_auth_fields(params):
        if SECRET_FIELDS[code][0] not in credentials:
            return code, True
    return "", False


def _vk_auth_choices(builder, params: dict, job_id: str) -> None:
    """Добавляет выбор способа авторизации VK Cloud."""
    current = params["cloud"].get("auth_mode", "token")
    for value, title in (("token", "Готовый токен"), ("service_account", "Сервисная учётная запись")):
        _button(builder, ("✅ " if value == current else "") + title, "vkauth", job_id, value)


async def _vk_auth_setup(event, state: FSMContext, job: dict) -> None:
    """Последовательно запрашивает доступ к проекту VK Cloud."""
    job = await _effective_job(job, state)
    code, secret = _vk_missing_auth(job)
    if code:
        await _reset_input(state, job_id=job["id"], step="2", wizard_step="2", vk_auth_setup=True)
        return await _prompt(event, state, job, code, secret=secret)
    await state.update_data(vk_auth_setup=False)
    data = await state.get_data()
    if job["status"] not in EDITABLE_STATUSES and not data.get("edit_mode"):
        return await _job_screen(event, state, job)
    return await _step(event, state, job, "2")


def _field_button(builder, lines: list[str], params: dict, job_id: str, code: str) -> None:
    """Добавляет текущее значение и кнопку изменения поля."""
    field, title, _ = FIELDS[code]
    if code == "dm" and params["transport"] == "hysteria2":
        title = "Домен Hysteria2"
    value = params["cloud"].get(field) if code in {"fd", "vp"} else params[field]
    lines.append(f"{title}: {_copyable(value)}")
    _button(builder, title, "f", job_id, code)


async def _step(event, state: FSMContext, job: dict, step: str = "1") -> None:
    """Показывает основной шаг или отдельный раздел настроек."""
    job = await _effective_job(job, state)
    params = _params(job)
    pair = job.get("cascade_pair") or {}
    if step == "type":
        if pair:
            return await _step(event, state, job, "1")
        await _reset_input(state, job_id=job["id"], step=step, wizard_step="1", server_field="ip")
        builder = InlineKeyboardBuilder()
        _button(builder, "Одиночная нода", "role", job["id"], "normal")
        _button(builder, "Каскад", "step", job["id"], "cascade_type")
        _button(builder, "← Все установки", "list")
        return await _send(
            event,
            "<b>Установка ноды · Выберите схему</b>\n\n"
            "<b>Одиночная нода</b> — один сервер для подключения клиентов.\n\n"
            "<b>Каскад</b> — два сервера: нода 1 принимает подключения клиентов, "
            "нода 2 подключается к сайтам. Далее выберите HAProxy или VLESS.",
            builder,
            state=state,
        )
    if step == "cascade_type":
        if pair:
            return await _step(event, state, job, "1")
        await _reset_input(state, job_id=job["id"], step=step, wizard_step="1", server_field="ip")
        builder = InlineKeyboardBuilder()
        _button(builder, "HAProxy", "haproxy", job["id"])
        _button(builder, "VLESS", "vless", job["id"])
        _button(builder, "← Выбрать схему установки", "step", job["id"], "type")
        return await _send(
            event,
            "<b>Каскад · Выберите тип</b>\nНода 1 — вход → нода 2 — выход.\n\n"
            "<b>HAProxy</b> — вход передаёт TCP-подключения на выход с сохранением IP клиента. "
            "Мастер запросит оба сервера по порядку.\n\n"
            "<b>VLESS</b> — соединение между входом и выходом через VLESS. "
            "Можно установить вход или выход; для подключения входа понадобится готовый выход.",
            builder,
            state=state,
        )
    if step == "cascade_target":
        if params["role"] != "haproxy_entry":
            return await _step(event, state, job, "2")
        await _reset_input(state, job_id=job["id"], step=step, wizard_step="2", server_field="ip")
        builder = InlineKeyboardBuilder()
        if pair:
            _button(builder, "Настроить ноду 2 — выход", "newexit", job["id"])
        else:
            _button(builder, "Установить новую ноду 2", "newexit", job["id"])
            _button(builder, "Выбрать готовый выход", "exits", job["id"])
        _button(builder, "← Нода 1 — вход", "step", job["id"], "1")
        _button(builder, "Сохранить и закрыть", "show", job["id"])
        return await _send(
            event,
            "<b>Каскад HAProxy · Нода 2 — выход</b>\n"
            f"Нода 1 — вход: {_copyable(params['address'])}.\n\n"
            "Теперь укажите сервер выхода, который будет подключаться к сайтам. "
            "Можно установить новый сервер или выбрать уже готовый выход этой панели.",
            builder,
            state=state,
        )
    if step == "routes":
        if params["role"] not in {"entry", "haproxy_entry"}:
            return await _step(event, state, job, "3")
        data = await state.get_data()
        routes = params.get("cascade_routes") or []
        page = min(max(0, int(data.get("routes_page") or 0)), max(0, (len(routes) - 1) // 5))
        await _reset_input(state, job_id=job["id"], step=step, routes_page=page, route_exit_id=None)
        builder = InlineKeyboardBuilder()
        lines = [
            "<b>Выходы и страны</b>",
            f"Общий вход: {_copyable(params['address'])}",
            f"Основной хост: {_copyable(params['host_remark'] or params['name'])}, порт {params['client_port']}",
            "",
            "Каждое направление использует отдельный порт входа и свой хост в подписке клиента.",
        ]
        for index in range(page * 5, min(len(routes), (page + 1) * 5)):
            route = routes[index]
            _button(builder, f"{route['host_remark'][:45]} · {route['client_port']}", "route", job["id"], str(index))
        if not routes:
            lines.append("Дополнительные выходы пока не добавлены.")
        if page:
            _button(builder, "← Предыдущие страны", "routepage", job["id"], str(page - 1))
        if (page + 1) * 5 < len(routes):
            _button(builder, "Следующие страны →", "routepage", job["id"], str(page + 1))
        _button(builder, "Добавить выход", "routeadd", job["id"])
        _button(builder, "Основной хост и выход", "step", job["id"], "3")
        if data.get("edit_mode"):
            _button(builder, "Показать изменения", "preview", job["id"])
        _button(builder, "← Сквад и хост", "step", job["id"], "3")
        return await _send(event, "\n".join(lines), builder, state=state)
    if step not in BASIC_STEPS:
        return await _advanced(event, state, job, step)
    await _reset_input(state, job_id=job["id"], step=step, wizard_step=step, vk_auth_setup=False)
    data = await state.get_data()
    editing = data.get("edit_mode") and data.get("edit_job") == job["id"]
    if (
        step == "2"
        and job["status"] == "draft"
        and not editing
        and params["role"] == "haproxy_entry"
        and not (params["exit_install_id"] or pair)
    ):
        return await _step(event, state, job, "cascade_target")
    if step == "1" and job["status"] == "draft" and not editing:
        data = await state.get_data()
        return await _server_input(event, state, job, data.get("server_field", "ip"))
    builder = InlineKeyboardBuilder()
    job_id = job["id"]
    lines = [
        f"<b>{'Настройки ноды' if editing else 'Установка ноды'} · {step}/{len(BASIC_STEPS)} · {BASIC_STEPS[step]}</b>"
    ]
    if pair:
        lines.append("Нода 1 — вход HAProxy" if pair["entry_id"] == job_id else "Нода 2 — выход HAProxy")
    if step == "1":
        lines.extend([
            f"IP сервера: {_copyable(params['address'])}",
            f"SSH-порт: {_copyable(params['ssh_port'])}",
            f"SSH-логин: {_copyable(params['ssh_username'])}",
            "Пароль SSH: " + _secret_status(job, "ssh_password"),
        ])
        if "ssh_private_key" in (job.get("credential_fields") or []):
            lines.append("Доступ по SSH-ключу настроен.")
        _button(builder, "IP сервера", "f", job_id, "ip")
        _button(builder, "Пароль SSH", "s", job_id, "pw")
        _button(builder, "⚙️ Настройки SSH", "step", job_id, "ssh")
        lines.append("Укажите IP и пароль сервера. Обычно логин — root, порт — 22. Подключение проверим при установке.")
    elif step == "2":
        lines.append(f"Выбрано: {TRANSPORTS[params['transport']]}")
        if params["role"] in {"haproxy_entry", "haproxy_exit"}:
            lines.append("Роль: " + ROLES[params["role"]])
            lines.append(
                "Каскад через HAProxy использует VLESS TCP Reality и сохраняет IP клиента через PROXY protocol."
            )
            _field_button(builder, lines, params, job_id, "cp")
            if params["role"] == "haproxy_exit":
                _field_button(builder, lines, params, job_id, "hs")
                lines.append(
                    "IP ноды 1 разрешает ей передавать подключения на выход. Выберите клиентский сквад на следующем шаге."
                    if pair
                    else "Укажите IP входных серверов, которым разрешено передавать подключения на выход."
                )
            else:
                lines.append(
                    "HAProxy передаёт соединения на выбранный выход; профиль и ключи Reality берутся с выхода."
                )
        else:
            _choices(
                builder,
                [
                    (("✅ " if transport == params["transport"] else "") + title, "tr", str(index))
                    for index, (transport, title) in enumerate(TRANSPORTS.items())
                ],
                job_id,
            )
        if not pair:
            _button(builder, "Каскад через HAProxy", "haproxy", job_id)
        if params["transport"] in CERTIFICATE_TRANSPORTS:
            for code in ("dm", "em"):
                _field_button(builder, lines, params, job_id, code)
        if params["transport"].startswith("cdn_"):
            _field_button(builder, lines, params, job_id, "cd")
            if params["transport"] == "cdn_vk":
                _field_button(builder, lines, params, job_id, "vp")
                _vk_auth_choices(builder, params, job_id)
                codes = _vk_auth_fields(params)
                if params["cloud"].get("auth_mode", "token") == "service_account":
                    lines.append(
                        "Создайте сервисного пользователя в VK Cloud: «Управление доступами → Сервисные пользователи», роль — администратор проекта. Токен бот получит автоматически."
                    )
            else:
                codes = ("ya",)
            for code in codes:
                lines.append(SECRET_FIELDS[code][1] + ": " + _secret_status(job, SECRET_FIELDS[code][0]))
                _button(builder, SECRET_FIELDS[code][1], "s", job_id, code)
            lines.append(
                "Укажите два домена: origin с A/AAAA на IP сервера и домен CDN. После создания CDN бот покажет нужную запись CNAME и дождётся DNS."
            )
        elif params["transport"] == "hysteria2":
            lines.append(
                "Hysteria2 работает через UDP. Укажите домен с A/AAAA на IP сервера и email для сертификата. Порт 80/TCP нужен для выпуска и продления сертификата."
            )
        elif params["transport"] == "vless_tcp_selfsteal":
            _button(
                builder,
                "Сайт: " + ("Стриминговый" if params["site_template"] == "streaming" else "Обычный"),
                "site",
                job_id,
            )
        else:
            lines.append("Для этого транспорта домен и сертификат вводить не нужно.")
    elif step == "3":
        if params["role"] == "exit":
            lines.append("Для выхода каскада клиентский сквад и хост не создаются. Можно продолжить.")
        elif params["role"] == "haproxy_exit":
            lines.append(f"Сквад: {_copyable(job.get('squad_name') or params['squad_uuid'])}")
            _button(builder, "Выбрать сквад", "squads", job_id)
            lines.append(
                "Выберите клиентский сквад выхода. Вход HAProxy унаследует его и опубликует хост; хост самого выхода не создаётся."
            )
        else:
            lines.append(f"Название хоста: {_copyable(params['host_remark'] or params['name'])}")
            if job.get("status") == "draft" and params["name"]:
                lines.append(f"Имя ноды: {_copyable(node_name_from_host(params['host_remark'], params['name']))}")
            _button(builder, "Название хоста", "f", job_id, "hr")
            if params["role"] == "haproxy_entry":
                target = job.get("haproxy") or {}
                lines.append(f"Выход HAProxy: {_copyable(target.get('name') or params['exit_install_id'])}")
                if target.get("address"):
                    lines.append(f"Адрес выхода: {_copyable(target['address'])}, порт {_copyable(target.get('port'))}")
                if pair:
                    if not editing and job["cascade_exit"]["status"] == "draft":
                        _button(builder, "Настройки ноды 2 — выхода", "step", pair["exit_id"], "2")
                    else:
                        _button(builder, "Карточка ноды 2 — выхода", "partner", job_id)
                else:
                    _button(builder, "Выбрать выход HAProxy", "exits", job_id)
                _field_button(builder, lines, params, job_id, "cp")
                lines.append("Клиентский сквад и настройки Reality наследуются от выбранного выхода.")
            else:
                lines.append(f"Сквад: {_copyable(job.get('squad_name') or params['squad_uuid'])}")
                _button(builder, "Выбрать сквад", "squads", job_id)
                if params["role"] == "entry":
                    lines.append(f"Выходная нода: {_copyable(params['exit_install_id'])}")
                    _button(builder, "Выбрать выход каскада", "exits", job_id)
            _button(builder, ("✅ " if params["publish_host"] else "⬜ ") + "Создать хост", "flag", job_id, "host")
            if params["role"] != "haproxy_entry":
                lines.append(
                    "Выберите сквад, в подписках которого должна появиться нода. Если оставить без сквада, инбаунд можно привязать позже."
                )
            if params["role"] in {"entry", "haproxy_entry"}:
                lines.append(f"Направлений каскада: {1 + len(params.get('cascade_routes') or [])}")
                _button(builder, "Выходы и страны", "routes", job_id)
    else:
        lines.append("Выберите нужные дополнения или продолжите без них.")
        choices = [
            (("✅ " if name in params["addons"] else "⬜ ") + title, "add", name)
            for name, title in ADDONS.items()
            if params["role"] != "haproxy_entry" or name in {"bbr", "fail2ban", "ufw"}
        ]
        shaping_available = (
            not params["transport"].startswith("cdn_")
            and params["transport"] != "hysteria2"
            and params["role"] not in {"exit", "haproxy_entry", "haproxy_exit"}
        )
        shaping_enabled = bool(params["shaping"].get("enabled", bool(params["shaping"])))
        if shaping_available:
            choices.append((("✅ " if shaping_enabled else "⬜ ") + "Шейпинг скорости", "add", "shape"))
        else:
            lines.append("Шейпинг скорости недоступен для Hysteria2, CDN, HAProxy и выхода каскада.")
        _choices(
            builder,
            choices,
            job_id,
        )
        if shaping_available and shaping_enabled:
            _button(builder, "Настроить шейпинг скорости", "shape", job_id)
        if "psiphon" in params["addons"]:
            lines.append(
                "Psiphon: собственная конфигурация."
                if "psiphon_config" in (job.get("credential_fields") or [])
                else "Psiphon настроится автоматически."
            )
    if step == "4":
        _button(
            builder,
            "Показать изменения"
            if editing
            else "Продолжить к ноде 1 →"
            if pair.get("exit_id") == job_id
            else "Проверить и установить",
            "preview" if editing else "entrysetup" if pair.get("exit_id") == job_id else "summary",
            job_id,
        )
    else:
        _button(builder, "Продолжить →", "step", job_id, str(int(step) + 1))
    if step != "1":
        _button(builder, "← Предыдущий шаг", "step", job_id, str(int(step) - 1))
    _button(builder, "⚙️ Дополнительные настройки", "step", job_id, "advanced")
    _button(builder, "← Разделы настроек" if editing else "Сохранить и закрыть", "emenu" if editing else "show", job_id)
    await _send(event, "\n".join(lines), builder, state=state)


async def _advanced(event, state: FSMContext, job: dict, section: str) -> None:
    """Скрывает необязательные параметры от основного сценария."""
    params = _params(job)
    job_id = job["id"]
    await _reset_input(state, job_id=job_id, step=section, vk_auth_setup=False)
    data = await state.get_data()
    builder = InlineKeyboardBuilder()
    lines = [f"<b>{STEP_NAMES[section]}</b>"]
    if section == "advanced":
        lines.append("Обычная установка не требует изменений здесь.")
        for key in ("server", "network", "profile", "cascade"):
            if key == "profile" and params["role"] == "haproxy_entry":
                continue
            _button(builder, STEP_NAMES[key], "step", job_id, key)
        if params["transport"] == "cdn_yandex":
            _field_button(builder, lines, params, job_id, "fd")
        elif params["transport"] == "cdn_vk":
            _field_button(builder, lines, params, job_id, "vp")
        if "psiphon" in params["addons"]:
            _button(builder, "Собственная конфигурация Psiphon", "s", job_id, "pc")
        _button(
            builder, ("✅ " if params["replace_existing"] else "⬜ ") + "Замена прежних ресурсов", "replace", job_id
        )
        _button(builder, "← К установке", "step", job_id, data.get("wizard_step", "1"))
    else:
        if section == "ssh":
            for code in ("us", "sp"):
                _field_button(builder, lines, params, job_id, code)
            _button(builder, "Другие настройки доступа", "step", job_id, "server")
        elif section == "server":
            _field_button(builder, lines, params, job_id, "nm")
            for code in ("pk", "pp"):
                lines.append(SECRET_FIELDS[code][1] + ": " + _secret_status(job, SECRET_FIELDS[code][0]))
                _button(builder, SECRET_FIELDS[code][1], "s", job_id, code)
            _button(builder, "Проверить подключение", "probe", job_id)
            if params.get("host_fingerprint"):
                _button(builder, "Проверить новый ключ сервера", "resetfp", job_id)
                lines.append(
                    "Ключ сервера проверяется отдельно от пароля и ключа для входа. После переустановки VPS здесь можно подтвердить его замену."
                )
        elif section == "network":
            for code in ("cp", "ss") if params["role"] == "haproxy_entry" else ("np", "cp", "ps", "ss"):
                _field_button(builder, lines, params, job_id, code)
            if params["role"] == "haproxy_exit":
                _field_button(builder, lines, params, job_id, "hs")
            if params["role"] != "haproxy_entry" and params["transport"] in {
                "vless_tcp_reality",
                "vless_xhttp_reality",
            }:
                _field_button(builder, lines, params, job_id, "rd")
            if params["role"] == "haproxy_entry":
                lines.append("Клиентский порт принимает TCP и передаёт соединения на выход HAProxy.")
            else:
                lines.append(
                    "Ограничение по IP необязательно. Управление нодой проверяет сертификат панели и токен доступа."
                )
        elif section == "profile":
            if params["role"] == "haproxy_entry":
                lines.append("Вход HAProxy использует профиль выбранного выхода.")
            else:
                _field_button(builder, lines, params, job_id, "pn")
                lines.append("Профиль создаётся автоматически. Здесь можно задать собственное имя.")
        elif section == "cascade":
            lines.append(f"Роль: {ROLES[params['role']]}")
            if params["transport"] == "hysteria2":
                lines.append("Hysteria2 устанавливается как обычная нода.")
            elif not job.get("cascade_pair"):
                _button(builder, "Роль ноды", "roles", job_id)
            if params["role"] in {"entry", "haproxy_entry"}:
                target = job.get("haproxy") or {}
                lines.append(f"Выходная нода: {_copyable(target.get('name') or params['exit_install_id'])}")
                if job.get("cascade_pair"):
                    if not data.get("edit_mode") and job["cascade_exit"]["status"] == "draft":
                        _button(builder, "Настройки ноды 2 — выхода", "step", job["cascade_pair"]["exit_id"], "2")
                    else:
                        _button(builder, "Карточка ноды 2 — выхода", "partner", job_id)
                else:
                    _button(builder, "Выходная нода каскада", "exits", job_id)
            if params["role"] == "haproxy_exit":
                _field_button(builder, lines, params, job_id, "hs")
            if params["role"] != "haproxy_entry":
                for code in ("ei", "ef"):
                    _field_button(builder, lines, params, job_id, code)
        if section == "ssh":
            _button(builder, "← Назад к вводу" if not data.get("edit_mode") else "← Сервер", "step", job_id, "1")
        elif section == "server" and data.get("wizard_step") == "1" and data.get("server_field"):
            _button(builder, "← Настройки SSH", "step", job_id, "ssh")
        else:
            _button(builder, "← Дополнительные настройки", "step", job_id, "advanced")
    if data.get("edit_mode"):
        _button(builder, "Показать изменения", "preview", job_id)
    await _send(event, "\n".join(lines), builder, state=state)


def _summary(job: dict) -> str:
    """Собирает короткую сводку без учётных данных."""
    params = _params(job)
    addons = [ADDONS[name] for name in params["addons"]]
    if params["shaping"].get("enabled", bool(params["shaping"])):
        addons.append("Шейпинг скорости")
    lines = [
        "<b>Нода 1 — вход HAProxy</b>"
        if (job.get("cascade_pair") or {}).get("entry_id") == job["id"]
        else "<b>Нода 2 — выход HAProxy</b>"
        if job.get("cascade_pair")
        else "<b>Всё готово к установке</b>",
        f"Сервер: {_copyable(params['address'])}",
        f"SSH: {_copyable(params['ssh_username'])}, порт {_copyable(params['ssh_port'])}",
        f"Транспорт: {TRANSPORTS[params['transport']]}",
        "Дополнения: " + (", ".join(addons) or "без дополнений"),
    ]
    if params["name"]:
        name = (
            node_name_from_host(params["host_remark"], params["name"])
            if job.get("status") == "draft"
            else params["name"]
        )
        lines.insert(2, f"Имя ноды: {_copyable(name)}")
    if params["role"] != "normal":
        lines.append("Роль: " + ROLES[params["role"]])
    if params["role"] == "haproxy_entry":
        target = job.get("haproxy") or {}
        lines.append(
            f"Каскад: {_copyable(params['name'] or params['address'])}:{params['client_port']} → "
            f"{_copyable(target.get('name') or params['exit_install_id'])}:{_copyable(target.get('port'))}"
        )
        if target.get("address"):
            lines.append("IP выхода: " + _copyable(target["address"]))
    elif params["role"] == "haproxy_exit":
        lines.append("Доверенные входы HAProxy: " + _copyable(params["haproxy_sources"]))
    if params["client_port"] != 443 and not params["transport"].startswith("cdn_"):
        lines.append(f"Порт клиентов: {_copyable(params['client_port'])}")
    if params["transport"] in CERTIFICATE_TRANSPORTS:
        lines.extend([
            f"Домен / origin: {_copyable(params['domain'])}",
            f"Email сертификата: {_copyable(params['email'])}",
        ])
    if params["transport"].startswith("cdn_"):
        lines.extend([
            f"Домен CDN: {_copyable(params['cdn_domain'])}",
            "Ресурсы CDN оплачиваются у провайдера и сохраняются при отмене установки.",
        ])
        if params["transport"] == "cdn_vk":
            lines.append(f"ID проекта VK Cloud: {_copyable(params['cloud'].get('project_id'))}")
            lines.append(
                "Авторизация VK Cloud: "
                + ("сервисная учётная запись" if _vk_auth_fields(params) != ("vk",) else "готовый токен")
            )
    if params["role"] not in {"exit", "haproxy_exit"}:
        lines.append(f"Название хоста: {_copyable(params['host_remark'] or params['name'])}")
        lines.append("Создать хост: " + ("да" if params["publish_host"] else "нет"))
    if params["squad_uuid"]:
        lines.append(f"Сквад: {_copyable(job.get('squad_name') or params['squad_uuid'])}")
    elif params["role"] not in {"exit", "haproxy_exit", "haproxy_entry"}:
        lines.append(
            "Сквад не выбран. После установки добавьте инбаунд в нужный сквад или выберите его на шаге «Сквад и хост»."
        )
    if params.get("cascade_routes"):
        lines.append("Дополнительные хосты на общем входе:")
        lines.extend(
            f"{_display(route['host_remark'])}: {_copyable(params['address'])}:{route['client_port']}"
            for route in params["cascade_routes"][:12]
        )
        if len(params["cascade_routes"]) > 12:
            lines.append(f"Ещё направлений: {len(params['cascade_routes']) - 12}")
    if not job.get("cascade_pair"):
        lines.append(
            "Бот установит HAProxy и свяжет его с выбранным выходом. Ход установки появится в этом сообщении."
            if params["role"] == "haproxy_entry"
            else "Бот подключит ноду к настроенной панели и создаст профиль. Ход установки появится в этом сообщении."
        )
    return "\n".join(lines)


async def _job_screen(event, state: FSMContext, job: dict) -> None:
    """Показывает карточку и привязывает к ней фоновое обновление."""
    await _clear_input(state)
    builder = InlineKeyboardBuilder()
    for title, action, value in job_actions(job):
        _button(builder, title, action, job["id"], value)
    await _send(event, job_status_text(job), builder, state=state)
    data = await state.get_data()
    if data.get("_screen_message_id"):
        try:
            await jobs.bind_progress_message(
                job["id"], event.from_user.id, data["_screen_message_id"], chat_id=data["_screen_chat_id"]
            )
        except InstallError as error:
            if not job.get("deletion") or error.step != "permission":
                raise
            return await _show_jobs(event, state, event.from_user.id)
        await state.update_data(_progress_job_id=job["id"])


async def _edit_menu(event, state: FSMContext, job: dict) -> None:
    """Показывает разделы несекретного черновика изменений."""
    await _reset_input(state, job_id=job["id"], step="1")
    builder = InlineKeyboardBuilder()
    for step, title in BASIC_STEPS.items():
        _button(builder, title, "step", job["id"], step)
    _button(builder, "Дополнительные настройки", "step", job["id"], "advanced")
    _button(builder, "Учётные данные", "auth", job["id"])
    _button(builder, "Показать изменения", "preview", job["id"])
    if job["status"] == "failed":
        _button(builder, "Повторить установку", "retry", job["id"])
    _button(builder, "Отменить изменения", "show", job["id"])
    await _send(
        event,
        "<b>Изменить настройки ноды</b>\nВыберите раздел. Значения сохраняются в черновике до проверки и подтверждения. Применятся только затронутые изменения. Учётные данные обновляются отдельно и хранятся зашифрованными.",
        builder,
        state=state,
    )


async def _resources_screen(event, job: dict, page: str, state: FSMContext) -> None:
    """Показывает только разрешённые идентификаторы ресурсов CDN."""
    current = (job.get("resources") or {}).get("cdn") or {}
    items = [("Текущий ресурс", current)] if current else []
    items.extend(
        ("Сохранённый прежний ресурс", item) for item in job.get("retained_resources") or [] if isinstance(item, dict)
    )
    index = min(int(page), len(items) - 1) if page.isdecimal() and items else 0
    builder = InlineKeyboardBuilder()
    lines = ["<b>Ресурсы CDN</b>"]
    if items:
        title, item = items[index]
        lines.append(f"{title} · {index + 1}/{len(items)}")
        for field, label in (
            ("provider", "Провайдер"),
            ("domain", "Домен CDN"),
            ("origin_domain", "Origin"),
            ("resource_id", "Ресурс"),
            ("origin_group_id", "Группа источников"),
            ("certificate_id", "Сертификат"),
            ("folder_id", "Каталог"),
            ("project_id", "Проект VK Cloud"),
            ("status", "Статус"),
            ("ready", "Готов"),
        ):
            if field in item and item[field] is not None:
                lines.append(f"{label}: <code>{_display(item[field])}</code>")
        if index > 0:
            _button(builder, "← Предыдущий ресурс", "res", job["id"], str(index - 1))
        if index + 1 < len(items):
            _button(builder, "Следующий ресурс →", "res", job["id"], str(index + 1))
        lines.append(
            "Идентификаторы можно скопировать. Откройте консоль указанного провайдера и найдите ресурс по ID и домену. Прежние ресурсы сохраняются и могут оплачиваться; после проверки новой ноды ненужные ресурсы удаляются в консоли провайдера вручную."
        )
    else:
        lines.append("У этой установки пока нет сохранённых ресурсов CDN.")
    _button(builder, "← Установка", "show", job["id"])
    await _send(event, "\n".join(lines), builder, state=state)


async def _changes_screen(event, state: FSMContext, job: dict, owner: int) -> None:
    """Показывает разницу и план перед применением настроек."""
    result = await jobs.preview_changes(job["id"], owner, params=_params(job))
    await state.update_data(preview_token=result["token"])
    if result.get("params"):
        await state.update_data(pending_params=result["params"])
    changes = result.get("changes") or []
    lines = ["<b>Проверка изменений</b>"]
    for change in changes:
        if change.get("field") == "cascade_routes":
            before = {route["exit_install_id"]: route for route in change.get("before") or []}
            after = {route["exit_install_id"]: route for route in change.get("after") or []}
            lines.append(f"Дополнительные направления: {len(before)} → {len(after)}")
            for identifier in list(dict.fromkeys([*before, *after]))[:20]:
                old, new = before.get(identifier), after.get(identifier)
                if old == new:
                    continue
                lines.append(
                    ("Добавить: " if not old else "Убрать: " if not new else "Изменить: ")
                    + (
                        f"{_display(old['host_remark'])}:{old['client_port']} → {_display(new['host_remark'])}:{new['client_port']}"
                        if old and new
                        else f"{_display((new or old)['host_remark'])}:{(new or old)['client_port']}"
                    )
                )
            continue
        lines.append(
            f"{_display(change.get('label') or change.get('field'))}: {_copyable(change.get('before'))} → {_copyable(change.get('after'))}"
        )
    if not changes:
        lines.append("Параметры не изменились.")
    if result.get("steps"):
        lines.append("Затронутые этапы: " + _display(result["steps"]))
    for warning in result.get("warnings") or []:
        lines.append("⚠️ " + _display(warning))
    if result.get("requires_replacement"):
        lines.append(
            "Изменения требуют замены связанных ресурсов. Возможен перерыв подключения; подтвердите замену отдельно кнопкой ниже."
        )
    builder = InlineKeyboardBuilder()
    if changes:
        _button(
            builder,
            "Подтвердить замену и применить" if result.get("requires_replacement") else "Применить изменения",
            "apply",
            job["id"],
        )
    _button(builder, "← Изменить параметры", "emenu", job["id"])
    await _send(event, "\n".join(lines), builder, state=state)


async def _show_jobs(event, state: FSMContext, owner: int) -> None:
    """Показывает установки текущего администратора."""
    await _clear_input(state)
    builder = InlineKeyboardBuilder()
    _button(builder, "➕ Установить ноду (beta)", "new")
    for job in (await jobs.list_jobs(owner))[:30]:
        params = _params(job)
        status = STATUSES.get(job["status"], job["status"])
        if job.get("deletion"):
            status = {
                "queued": "Ожидает удаления",
                "running": "Удаление",
                "failed": "Ошибка удаления",
                "paused": "Удаление приостановлено",
                "canceled": "Удаление остановлено",
            }.get(job["status"], "Удаление")
        title = f"{params['name'] or params['address'] or 'Черновик'} · {status}"
        _button(builder, title[:65], "show", job["id"])
    _button(builder, "← Remnawave", "exit")
    await _send(
        event,
        "<b>Установка нод Remnawave</b>\nЗдесь можно создать ноду, продолжить установку и управлять дополнениями.",
        builder,
        state=state,
    )


async def _shaping(event, state: FSMContext, job: dict, *, manage: bool = False) -> None:
    """Показывает настройки ограничения скорости."""
    data = await state.get_data()
    params = _params(job)
    if (
        params["transport"].startswith("cdn_")
        or params["transport"] == "hysteria2"
        or params["role"] in {"exit", "haproxy_entry", "haproxy_exit"}
    ):
        raise ValueError("Шейпинг недоступен для этого транспорта или роли")
    values = data.get("shaping") if manage else params["shaping"]
    values = values if isinstance(values, dict) else {}
    await _reset_input(state, job_id=job["id"], step="shape", manage=manage, shaping=values)
    builder = InlineKeyboardBuilder()
    enabled = bool(values.get("enabled", bool(values)))
    lines = ["<b>Шейпинг скорости</b>", "Включён" if enabled else "Выключен"]
    _button(builder, "Выключить" if enabled else "Включить", "shon", job["id"], "0" if enabled else "1")
    for code, (field, title, default) in SHAPING_FIELDS.items():
        lines.append(f"{title}: {values.get(field, default)}")
        _button(builder, title, "sf", job["id"], code)
    if manage:
        _button(builder, "Применить изменения", "shapply", job["id"])
        _button(builder, "← Карточка ноды", "show", job["id"])
    else:
        _button(builder, "← Дополнения", "step", job["id"], "4")
        if data.get("edit_mode"):
            _button(builder, "Показать изменения", "preview", job["id"])
    await _send(event, "\n".join(lines), builder, state=state)


def _parse_value(code: str, text: str):
    """Проверяет введённое несекретное значение."""
    value = "" if text.strip() == "-" else text.strip()
    if code in SHAPING_FIELDS:
        if not re.fullmatch(r"\d{1,7}", value):
            raise ValueError("Введите целое неотрицательное число")
        number = int(value)
        if code != "sv" and number == 0:
            raise ValueError("Значение должно быть больше нуля")
        return number
    if code in {"sp", "np", "cp"}:
        if not value.isdecimal() or not 1 <= int(value) <= 65535:
            raise ValueError("Порт должен быть от 1 до 65535")
        return int(value)
    if code in {"ip", "ei"}:
        if code == "ei" and not value:
            return ""
        try:
            return str(ipaddress.ip_address(value))
        except ValueError as error:
            raise ValueError("Некорректный IP-адрес") from error
    if code in {"ps", "ss"}:
        try:
            result = [str(ipaddress.ip_network(item, strict=False)) for item in re.split(r"[\s,]+", value) if item]
        except ValueError as error:
            raise ValueError("Укажите IP/CIDR через пробел") from error
        return result
    if code == "hs":
        try:
            result = []
            for item in re.split(r"[\s,]+", value):
                if not item:
                    continue
                network = ipaddress.ip_network(item, strict=False)
                if network.prefixlen != network.max_prefixlen:
                    raise ValueError
                result.append(str(network))
            if not result:
                raise ValueError
        except ValueError as error:
            raise ValueError("Укажите точные IP входов HAProxy; сети и пустой список запрещены") from error
        return list(dict.fromkeys(result))
    if code == "nm" and (
        not value.strip()
        or any(unicodedata.category(character).startswith("C") for character in value)
        or not 3 <= node_name_length(value) <= 30
    ):
        raise ValueError(FIELDS[code][2])
    if code == "us" and not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", value):
        raise ValueError("Некорректный SSH-логин")
    if code == "pn" and value and not re.fullmatch(r"[A-Za-z0-9 _-]{2,29}", value):
        raise ValueError(FIELDS[code][2])
    if code in {"dm", "cd", "rd"}:
        from services.remnawave_installer.types import _domain

        return _domain(value)
    if code == "em" and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise ValueError("Некорректный email")
    if code == "hr" and len(value) > 100:
        raise ValueError("Название хоста должно быть не длиннее 100 символов")
    if code == "ef" and value and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,32}", value):
        raise ValueError("Некорректное имя интерфейса")
    if code == "fd" and value and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("Некорректный ID каталога")
    if code == "vp" and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise ValueError("Укажите ID проекта VK Cloud: латиница, цифры, дефис и подчёркивание")
    return value


async def _prompt(event, state: FSMContext, job: dict, code: str, *, secret: bool = False) -> None:
    """Запрашивает одно поле мастера."""
    data = await state.get_data()
    if code in SHAPING_FIELDS:
        _, title, _ = SHAPING_FIELDS[code]
        hint = "Введите целое число."
    else:
        _, title, hint = (SECRET_FIELDS if secret else FIELDS)[code]
    params = _params(job)
    if data.get("step") == "routes" and data.get("route_exit_id") and code in {"cp", "hr"}:
        title = "Порт входа для страны" if code == "cp" else "Название хоста страны"
        hint = (
            "Укажите свободный TCP-порт на общем входном сервере. У каждой страны должен быть свой порт."
            if code == "cp"
            else "Название, которое клиент увидит в подписке, например: 🇩🇪 Германия."
        )
    if code == "ip" and params["role"] == "haproxy_exit":
        title = "IP сервера выхода HAProxy"
        hint = (
            "Укажите IPv4 или IPv6 ноды 2 — выходного сервера, обычно за границей. "
            "IP ноды 1 уже добавлен в доверенные входы."
            if job.get("cascade_pair")
            else "Укажите IPv4 или IPv6 выходного сервера, обычно за границей. IP входных серверов будут запрошены отдельно."
        )
    elif code == "ip" and params["role"] == "haproxy_entry":
        title = "IP сервера входа HAProxy"
        hint = (
            "Укажите IPv4 или IPv6 входного сервера, к которому будут подключаться клиенты, например в России. "
            "Затем укажите ноду 2 — выход или выберите уже установленный выход."
        )
    elif code == "ip" and params["role"] in {"entry", "exit"}:
        entry = params["role"] == "entry"
        title = "IP сервера входа VLESS" if entry else "IP сервера выхода VLESS"
        hint = (
            "Укажите IPv4 или IPv6 ноды 1 — сервера, к которому подключаются клиенты. "
            "Готовую ноду 2 — выход выберете на шаге «Сквад и хост»."
            if entry
            else "Укажите IPv4 или IPv6 ноды 2 — выходного сервера, который подключается к сайтам. "
            "После установки его можно выбрать на входной ноде каскада."
        )
    if code == "dm" and params["transport"] == "hysteria2":
        title = "Домен Hysteria2"
    await state.set_state(RemnaInstallState.secret if secret else RemnaInstallState.value)
    await state.update_data(job_id=job["id"], field=code, step=data.get("step", "1"))
    builder = InlineKeyboardBuilder()
    if code in {"ip", "pw"} and data.get("step", "1") == "1" and job["status"] == "draft" and not data.get("edit_mode"):
        await state.update_data(server_field=code)
        lines = [f"<b>Установка ноды · 1/{len(BASIC_STEPS)} · Сервер</b>"]
        if params["role"] != "normal":
            lines.append("Роль: " + ROLES[params["role"]])
            if job.get("cascade_pair") or params["role"] == "haproxy_entry":
                lines.append("Нода 1 — вход" if params["role"] == "haproxy_entry" else "Нода 2 — выход")
        if code == "ip":
            lines.extend(["", f"<b>Введите {escape(title)}</b>", escape(hint)])
            if params["address"]:
                lines.append(f"Текущий IP: {_copyable(params['address'])}")
                _button(builder, "Оставить IP →", "servernext", job["id"], "pw")
            if not job.get("cascade_pair"):
                _button(
                    builder,
                    "← Выбрать тип каскада" if params["role"] != "normal" else "← Выбрать схему установки",
                    "step",
                    job["id"],
                    "cascade_type" if params["role"] != "normal" else "type",
                )
            elif params["role"] == "haproxy_exit":
                _button(builder, "← Нода 1 — вход", "step", job["cascade_pair"]["entry_id"], "cascade_target")
        else:
            lines.extend([f"IP сервера: {_copyable(params['address'])}", "", "<b>Введите пароль SSH</b>", escape(hint)])
            if set(job.get("credential_fields") or []).intersection({"ssh_password", "ssh_private_key"}):
                _button(builder, "Использовать сохранённый доступ →", "servernext", job["id"], "2")
            _button(builder, "← Изменить IP", "f", job["id"], "ip")
        lines.append(f"Подключение: {_copyable(params['ssh_username'])}, порт {_copyable(params['ssh_port'])}.")
        _button(builder, "⚙️ Дополнительные настройки SSH", "step", job["id"], "ssh")
        _button(builder, "Сохранить и закрыть", "show", job["id"])
        if secret:
            lines.append("Сообщение с паролем будет скрыто в журналах и удалено после обработки.")
        return await _send(event, "\n".join(lines), builder, state=state)
    _button(
        builder,
        "← Назад",
        "auth"
        if job.get("deletion")
        else "show"
        if data.get("deletion_prep_job") == job["id"]
        else "shape"
        if data.get("step") == "shape"
        else "step",
        job["id"],
        data.get("step", "1")
        if not job.get("deletion") and not data.get("deletion_prep_job") and data.get("step") != "shape"
        else "",
    )
    text = f"<b>{escape(title)}</b>\n{escape(hint)}"
    if secret:
        text += "\nСообщение будет скрыто в журналах и удалено из чата после сохранения. Не отправляйте секрет в другом разделе."
    await _send(event, text, builder, state=state)


async def _server_input(event, state: FSMContext, job: dict, field: str = "ip") -> None:
    """Запрашивает IP или пароль без промежуточной карточки."""
    field = field if field in {"ip", "pw"} else "ip"
    if field == "pw" and not _params(job)["address"]:
        field = "ip"
    await _reset_input(state, job_id=job["id"], step="1", wizard_step="1", server_field=field)
    await _prompt(event, state, job, field, secret=field == "pw")


async def _missing_input(event, state: FSMContext, job: dict, step: str) -> bool:
    """Запрашивает недостающее поле перед следующим шагом установки."""
    params = _params(job)
    squad_uuid = params["squad_uuid"] or (job.get("haproxy") or {}).get("squad_uuid")
    credentials = set(job.get("credential_fields") or [])
    code, secret = "", False
    if step == "1":
        if not params["address"]:
            code = "ip"
        elif not credentials.intersection({"ssh_password", "ssh_private_key"}):
            code, secret = "pw", True
    elif step == "2":
        if params["transport"] in CERTIFICATE_TRANSPORTS:
            for field, candidate in (("domain", "dm"), ("email", "em")):
                if not params[field]:
                    code = candidate
                    break
        if not code and params["transport"].startswith("cdn_"):
            if not params["cdn_domain"]:
                code = "cd"
            elif params["transport"] == "cdn_vk":
                code, secret = _vk_missing_auth(job)
            else:
                provider, field = ("vk", "vk_token") if params["transport"] == "cdn_vk" else ("ya", "yandex_key")
                if field not in credentials:
                    code, secret = provider, True
        if params["role"] == "haproxy_exit" and not params["haproxy_sources"]:
            code = "hs"
    elif step == "3" and (
        (params["role"] in {"entry", "haproxy_entry"} and not (params["exit_install_id"] or job.get("cascade_exit")))
        or (params["role"] in {"haproxy_entry", "haproxy_exit"} and not squad_uuid)
    ):
        await _step(event, state, job, "3")
        return True
    if not code:
        return False
    if (
        params["transport"] == "cdn_vk"
        and params["cloud"].get("auth_mode") == "service_account"
        and code in {"vp", "vd", "vu", "vw"}
    ):
        await _vk_auth_setup(event, state, job)
        return True
    await _reset_input(state, job_id=job["id"], step=step, wizard_step=step)
    await _prompt(event, state, job, code, secret=secret)
    return True


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_install"))
async def open_installer(callback: CallbackQuery, state: FSMContext, session=None):
    """Открывает список установок администратора."""
    await callback.answer()
    await release_session_early(session)
    await _detach_progress(state, callback.from_user.id)
    await _show_jobs(callback, state, callback.from_user.id)


@router.callback_query(InstallCallback.filter())
async def installer_action(callback: CallbackQuery, callback_data: InstallCallback, state: FSMContext, session=None):
    """Обрабатывает навигацию и команды установки."""
    await callback.answer()
    await release_session_early(session)
    owner = callback.from_user.id
    action, job_id, value = callback_data.action, callback_data.job, callback_data.value
    try:
        await _detach_progress(state, owner, job_id)
        if action == "exit":
            from . import settings_remnawave as remna_settings

            await _clear_input(state)
            markup = remna_settings.build_settings_remnawave_kb(
                remna_settings._node_health_enabled(),
                remna_settings._host_rotation_enabled(),
                remna_settings.is_load_monitor_enabled(),
            )
            return await _send(
                callback, remna_settings._root_text(), InlineKeyboardBuilder(markup=markup.inline_keyboard), state=state
            )
        if action == "list":
            return await _show_jobs(callback, state, owner)
        if action == "new":
            source = None
            if value == "vless_exit":
                source = await jobs.get_job(job_id, owner)
                if source["status"] != "draft" or _params(source)["role"] != "entry" or source.get("deletion"):
                    raise ValueError("Создайте выход из мастера входной ноды VLESS")
            await _clear_input(state)
            job = await jobs.create_draft(
                owner, callback.message.chat.id, **({"panel_ref": _params(source)["panel_ref"]} if source else {})
            )
            if source:
                job = await _save(job, owner, {"role": "exit", "transport": "vless_tcp_reality", "shaping": {}}, state)
                return await _step(callback, state, job, "1")
            return await _step(callback, state, job, "type")
        job = await jobs.get_job(job_id, owner)
        stored_params = _params(job)
        deletion_params = {**InstallSpec().to_dict(), **(job.get("params") or {})}
        await state.set_state(None)
        job = await _effective_job(job, state)
        context = await state.get_data()
        editing = context.get("edit_mode") and context.get("edit_job") == job_id
        params = _params(job)
        if job.get("deletion") and action not in {
            "show",
            "partner",
            "res",
            "delask",
            "delconfirm",
            "delete",
            "retry",
            "auth",
            "s",
            "stopask",
            "stop",
        }:
            raise ValueError("Для этой записи уже выбрано удаление. Откройте её карточку и продолжите удаление")
        if action == "show":
            return await _job_screen(callback, state, job)
        if action == "partner":
            pair = job.get("cascade_pair") or {}
            if job_id not in {pair.get("entry_id"), pair.get("exit_id")}:
                raise ValueError("У этой установки нет связанной ноды")
            partner_id = pair["exit_id"] if pair["entry_id"] == job_id else pair["entry_id"]
            return await _job_screen(callback, state, await jobs.get_job(partner_id, owner))
        if action == "res":
            await _clear_input(state)
            return await _resources_screen(callback, job, value, state)
        if action == "servernext":
            if job["status"] != "draft" or editing or value not in {"pw", "2"}:
                raise ValueError("Запрос устарел; откройте мастер установки заново")
            if not params["address"]:
                return await _server_input(callback, state, job, "ip")
            if value == "pw":
                return await _server_input(callback, state, job, "pw")
            if await _missing_input(callback, state, job, "1"):
                return
            return await _step(callback, state, job, "2")
        if action == "newexit":
            if job["status"] != "draft" or editing or params["role"] != "haproxy_entry":
                raise ValueError("Откройте мастер нового каскада")
            if await _missing_input(callback, state, job, "1"):
                return
            target = await jobs.create_draft(
                owner, callback.message.chat.id, panel_ref=params["panel_ref"], entry_job_id=job_id
            )
            await _clear_input(state)
            return await _step(callback, state, target, "1")
        if action == "entrysetup":
            pair = job.get("cascade_pair") or {}
            if job["status"] != "draft" or editing or pair.get("exit_id") != job_id:
                raise ValueError("Откройте мастер каскада заново")
            for step in BASIC_STEPS:
                if await _missing_input(callback, state, job, step):
                    return
            InstallSpec.from_dict(params)
            entry = await jobs.get_job(pair["entry_id"], owner)
            await _clear_input(state)
            return await _step(callback, state, entry, "2")
        if action == "step":
            if value not in STEP_NAMES or (
                job["status"] not in EDITABLE_STATUSES and not (editing and job["status"] in SETTINGS_EDIT_STATUSES)
            ):
                raise ValueError("Эту установку сейчас нельзя редактировать")
            if context.get("job_id") != job_id:
                await _clear_input(state)
            previous = context.get("step")
            if (
                not editing
                and previous in {"1", "2", "3"}
                and value == str(int(previous) + 1)
                and await _missing_input(callback, state, job, previous)
            ):
                return
            return await _step(callback, state, job, value)
        if action == "edit":
            if job["status"] == "draft" and job.get("cascade_pair"):
                await _clear_input(state)
                return await _step(callback, state, job, "2")
            if job["status"] == "waiting_dns":
                job = await jobs.pause_job(job_id, owner)
                params = _params(job)
                if job["status"] == "pause_requested":
                    return await _job_screen(callback, state, job)
            if job["status"] in {"queued", "running", "pause_requested"}:
                builder = InlineKeyboardBuilder()
                _button(builder, "Остановить с сохранением и изменить", "pause", job_id)
                _button(builder, "← Продолжить установку", "show", job_id)
                return await _send(
                    callback,
                    "<b>Установка ещё выполняется</b>\nДля изменения настроек сначала остановим ввод-вывод и сохраним контрольные точки. SSH-доступ и остальные секреты сохранятся.",
                    builder,
                    state=state,
                )
            if job["status"] not in SETTINGS_EDIT_STATUSES:
                raise ValueError("Сейчас настройки этой установки менять нельзя")
            await _clear_input(state)
            await state.update_data(edit_mode=True, edit_job=job_id, pending_params=params)
            return await _edit_menu(callback, state, job)
        if action == "pause":
            job = await jobs.pause_job(job_id, owner)
            if job["status"] == "paused":
                await _clear_input(state)
                await state.update_data(edit_mode=True, edit_job=job_id, pending_params=_params(job))
                return await _edit_menu(callback, state, job)
            return await _job_screen(callback, state, job)
        if action in {"emenu", "preview", "apply"}:
            if not editing or job["status"] not in SETTINGS_EDIT_STATUSES:
                raise ValueError("Черновик изменений устарел; откройте настройки заново")
            if action == "emenu":
                return await _edit_menu(callback, state, job)
            if action == "preview":
                if job["status"] == "failed" and params == stored_params:
                    return await _job_screen(callback, state, job)
                return await _changes_screen(callback, state, job, owner)
            token = context.get("preview_token")
            if not token:
                raise ValueError("Сначала нажмите «Показать изменения»")
            job = await jobs.apply_changes(
                job_id, owner, params=params, token=token, message_id=callback.message.message_id
            )
            return await _job_screen(callback, state, job)
        if action == "delauth":
            if (
                context.get("delete_job") != job_id
                or not context.get("delete_token")
                or context.get("delete_can_remove_server") is not True
                or job.get("can_delete") is not True
            ):
                raise ValueError("Сначала откройте удаление ноды из её карточки")
            return await _deletion_credentials_screen(
                callback, state, {**job, "params": deletion_params, "pending_params": None}
            )
        if action in {"auth", "authkeys"}:
            if job.get("deletion"):
                if (
                    action != "auth"
                    or not job.get("deletion_requires_credentials")
                    or job.get("can_delete") is not True
                ):
                    raise ValueError("Сейчас доступы для удаления обновлять нельзя")
                return await _deletion_credentials_screen(
                    callback, state, {**job, "params": deletion_params, "pending_params": None}
                )
            if job["status"] not in SECRET_EDIT_STATUSES:
                raise ValueError("Сейчас учётные данные обновлять нельзя")
            if action == "authkeys":
                if not editing and job["status"] not in EDITABLE_STATUSES:
                    await _clear_input(state)
                    await state.update_data(edit_mode=True, edit_job=job_id, pending_params=params)
                return await _step(callback, state, job, "server")
            builder = InlineKeyboardBuilder()
            if params["transport"] == "cdn_vk":
                _vk_auth_choices(builder, params, job_id)
            cloud_codes = (
                _vk_auth_fields(params)
                if params["transport"] == "cdn_vk"
                else ("ya",)
                if params["transport"] == "cdn_yandex"
                else ()
            )
            for code in (
                "pw",
                *cloud_codes,
                "pc" if "psiphon" in params["addons"] else "",
            ):
                if code:
                    _button(builder, SECRET_FIELDS[code][1], "s", job_id, code)
            _button(builder, "Доступ по SSH-ключу", "authkeys", job_id)
            _button(
                builder,
                "← Разделы настроек" if editing else "← Карточка установки",
                "emenu" if editing else "show",
                job_id,
            )
            await _reset_input(state, job_id=job_id, step=context.get("step", "1"))
            return await _send(
                callback,
                "<b>Обновление учётных данных</b>\nКлючи и пароли сохраняются зашифрованными. После завершения срок хранения — 30 дней.",
                builder,
                state=state,
            )
        if action == "vkauth":
            if params["transport"] != "cdn_vk" or value not in {"token", "service_account"}:
                raise ValueError("Выберите способ авторизации VK Cloud")
            if job["status"] not in SETTINGS_EDIT_STATUSES:
                raise ValueError("Сейчас учётные данные обновлять нельзя")
            if not editing and job["status"] not in EDITABLE_STATUSES:
                await state.update_data(edit_mode=True, edit_job=job_id, pending_params=params)
            job = await _save(job, owner, {"cloud": {**params["cloud"], "auth_mode": value}}, state)
            return await _vk_auth_setup(callback, state, job)
        if action in {"f", "s", "sf"}:
            if job.get("deletion") or context.get("deletion_prep_job") == job_id:
                if (
                    action != "s"
                    or (
                        job.get("deletion")
                        and (not context.get("deletion_credentials") or not job.get("deletion_requires_credentials"))
                    )
                    or job.get("can_delete") is not True
                    or value not in _deletion_secret_codes(job)
                ):
                    raise ValueError("Откройте обновление доступов в карточке удаления")
                return await _prompt(callback, state, job, value, secret=True)
            if (
                job["status"] not in EDITABLE_STATUSES
                and not (editing and job["status"] in SETTINGS_EDIT_STATUSES)
                and not (action == "sf" and job["status"] in {"succeeded", "warning"})
                and not (action == "s" and job["status"] in SECRET_EDIT_STATUSES)
            ):
                raise ValueError("Параметры выполняющейся установки менять нельзя")
            await state.update_data(vk_auth_setup=False)
            if params["role"] == "haproxy_entry" and value in {"np", "ps", "rd", "pn", "ei", "ef", "pc"}:
                raise ValueError("Эта настройка задаётся на выходе HAProxy")
            if value == "hs" and params["role"] != "haproxy_exit":
                raise ValueError("IP входов задаются на выходе HAProxy")
            if action == "s" and value in {"vk", "vd", "vu", "vw"} and value not in _vk_auth_fields(params):
                raise ValueError("Откройте настройки выбранного способа авторизации VK Cloud")
            if action == "s" and value in {"vk", "vd", "vu", "vw"} and not params["cloud"].get("project_id"):
                if not editing and job["status"] not in EDITABLE_STATUSES:
                    await state.update_data(edit_mode=True, edit_job=job_id, pending_params=params)
                await _reset_input(state, job_id=job_id, step="2", wizard_step="2")
                return await _prompt(callback, state, job, "vp")
            return await _prompt(callback, state, job, value, secret=action == "s")
        if action == "haproxy" and context.get("step") in {"type", "cascade_type"}:
            action, value = "role", "haproxy_entry"
        if action in {"roles", "haproxy", "vless", "tr"}:
            if action != "tr":
                builder = InlineKeyboardBuilder()
                roles = (
                    ("haproxy_entry", "haproxy_exit")
                    if action == "haproxy"
                    else ("entry", "exit")
                    if action == "vless"
                    else ROLES
                )
                for role in roles:
                    if action == "roles" and params["transport"] == "hysteria2" and role != "normal":
                        continue
                    title = (
                        "Нода 1 — вход VLESS"
                        if role == "entry"
                        else "Нода 2 — выход VLESS"
                        if role == "exit"
                        else ROLES[role]
                    )
                    _button(builder, title if action == "vless" else ROLES[role], "role", job_id, role)
                if action in {"haproxy", "vless"}:
                    choosing_type = context.get("step") in {"type", "cascade_type"}
                    _button(
                        builder,
                        "← Выбрать тип каскада" if choosing_type else "← К транспорту",
                        "step",
                        job_id,
                        "cascade_type" if choosing_type else "2",
                    )
                else:
                    _button(builder, "← Назад", "step", job_id, "cascade")
                return await _send(
                    callback,
                    "<b>Каскад через HAProxy</b>\nНода 1 — вход → нода 2 — выход. Выберите роль текущего сервера:\n\n"
                    "<b>Вход HAProxy</b> — сервер, к которому подключаются клиенты, например в России. "
                    "Выход задаётся на следующем этапе.\n\n"
                    "<b>Выход HAProxy</b> — сервер, который подключается к сайтам, обычно за границей. "
                    "Для него понадобятся IP входов и клиентский сквад."
                    if action == "haproxy"
                    else "<b>Каскад VLESS</b>\nНода 1 — вход → нода 2 — выход.\n\n"
                    "Выберите, какой сервер устанавливаете сейчас. Вход принимает подключения клиентов; "
                    "для него потребуется готовый выход. Выход подключается к сайтам и затем выбирается на входной ноде."
                    if action == "vless"
                    else "<b>Роль ноды</b>\nДля входа каскада понадобится уже установленная выходная нода.",
                    builder,
                    state=state,
                )
            transports = list(TRANSPORTS)
            if not value.isdecimal() or int(value) >= len(transports):
                raise ValueError("Неизвестный транспорт")
            if params["role"] in {"haproxy_entry", "haproxy_exit"} and transports[int(value)] != "vless_tcp_reality":
                raise ValueError("Каскад HAProxy использует VLESS TCP Reality; сначала смените роль")
            job = await _save(job, owner, {"transport": transports[int(value)]}, state)
            return await _step(callback, state, job, "2")
        if action == "role":
            if value not in ROLES:
                raise ValueError("Неизвестная роль")
            if job.get("cascade_pair") and value != params["role"]:
                raise ValueError("Роли двух серверов уже заданы в мастере каскада")
            choosing_cascade = context.get("step") == "cascade_type"
            if (
                params["transport"] == "hysteria2"
                and value not in {"normal", "haproxy_entry", "haproxy_exit"}
                and not choosing_cascade
            ):
                raise ValueError("Hysteria2 устанавливается только как обычная нода")
            values = {"role": value}
            if value != params["role"]:
                values["exit_install_id"] = None
                values["cascade_routes"] = []
                if params["role"] in {"exit", "haproxy_exit"}:
                    values["publish_host"] = value not in {"exit", "haproxy_exit"}
            if (
                choosing_cascade
                and value in {"entry", "exit"}
                and (params["transport"] == "hysteria2" or value == "exit")
            ):
                values.update(transport="vless_tcp_reality", shaping={})
            if value in {"haproxy_entry", "haproxy_exit"}:
                values.update(transport="vless_tcp_reality", shaping={})
                if value == "haproxy_exit":
                    values["publish_host"] = False
                    if params["client_port"] == 443:
                        values["client_port"] = 8443
                else:
                    values["addons"] = [name for name in params["addons"] if name in {"bbr", "fail2ban", "ufw"}]
                    values["publish_host"] = True
                    values.update(exit_ip="", exit_interface="")
                    if params["role"] != "haproxy_entry":
                        values.update(squad_uuid=None, exit_install_id=None)
                    if params["role"] == "haproxy_exit":
                        values["client_port"] = 443
            elif params["role"] in {"haproxy_entry", "haproxy_exit"}:
                values.update(haproxy_sources=[], exit_install_id=None, publish_host=value != "exit")
            job = await _save(job, owner, values, state)
            if context.get("step") in {"type", "cascade_type"} and job["status"] == "draft" and not editing:
                return await _step(callback, state, job, "1")
            return await _step(callback, state, job, "2" if value in {"haproxy_entry", "haproxy_exit"} else "cascade")
        if action == "probe":
            result = await jobs.probe_host(job_id, owner, **({"params": params} if editing else {}))
            if not result.get("fingerprint"):
                raise ValueError("Не удалось проверить ключ подключения сервера")
            if result.get("params"):
                if editing:
                    job = await _save(job, owner, result["params"], state)
                else:
                    job = {**job, "params": result["params"]}
            builder = InlineKeyboardBuilder()
            _button(builder, "← Назад", "step", job_id, "server")
            text = (
                "Подключение к серверу, права и Ubuntu проверены. Можно продолжать установку."
                if result.get("authenticated")
                else "Укажите пароль или приватный ключ. Подключение будет проверено при установке; его также можно проверить этой кнопкой."
            )
            return await _send(callback, "<b>Проверка подключения</b>\n" + escape(text), builder, state=state)
        if action == "resetfp":
            result = await jobs.inspect_host_key(job_id, owner, params=params)
            fingerprint = result.get("fingerprint")
            if not isinstance(fingerprint, str) or not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", fingerprint):
                raise ValueError("Не удалось получить ключ сервера. Повторите проверку")
            if fingerprint == result.get("previous_fingerprint"):
                await _reset_input(state, job_id=job_id)
                builder = InlineKeyboardBuilder()
                if editing:
                    _button(builder, "← Назад к настройкам", "step", job_id, "server")
                    _button(builder, "Показать изменения", "preview", job_id)
                    if job["status"] in {"failed", "paused"}:
                        _button(builder, "Продолжить установку", "retry", job_id)
                else:
                    for title, next_action, next_value in job_actions(job):
                        if next_action != "resetfp":
                            _button(builder, title, next_action, job_id, next_value)
                return await _send(
                    callback,
                    "<b>Ключ сервера не изменился</b>\nРанее сохранённый ключ доступен. Повторное подтверждение не требуется; можно продолжить установку.",
                    builder,
                    state=state,
                )
            await _reset_input(state, job_id=job_id, reset_endpoint=_endpoint(params), reset_fingerprint=fingerprint)
            builder = InlineKeyboardBuilder()
            _button(builder, "Подтвердить ключ сервера", "forgetfp", job_id)
            _button(
                builder,
                "← Назад",
                "step" if editing or job["status"] == "draft" else "show",
                job_id,
                "server" if editing or job["status"] == "draft" else "",
            )
            lines = [
                "<b>Проверка ключа сервера</b>",
                f"Сервер: {_copyable(params['address'])}, порт {_copyable(params['ssh_port'])}",
            ]
            previous = result.get("previous_fingerprint")
            if previous:
                lines.append("Сохранённый отпечаток: <code>" + _display(previous) + "</code>")
            lines.extend([
                "Текущий отпечаток: <code>" + escape(fingerprint) + "</code>",
                "",
                "Это ключ самого сервера. Смена пароля или приватного ключа для входа не меняет его подтверждение.",
                "Если вы переустанавливали VPS или меняли его SSH-ключи, подтвердите текущий отпечаток. Его можно сверить в консоли сервера у хостера.",
            ])
            return await _send(
                callback,
                "\n".join(lines),
                builder,
                state=state,
            )
        if action == "forgetfp":
            data = await state.get_data()
            if (
                data.get("job_id") != job_id
                or data.get("reset_endpoint") != _endpoint(params)
                or not data.get("reset_fingerprint")
            ):
                raise ValueError("Запрос устарел. Откройте настройки сервера заново")
            result = await jobs.reset_host_key(job_id, owner, params=params, fingerprint=data["reset_fingerprint"])
            if not result.get("params") or not result["params"].get("host_fingerprint"):
                raise ValueError("Не удалось сохранить ключ сервера")
            fresh = await jobs.get_job(job_id, owner)
            if editing and result["params"] != _params(fresh):
                job = await _save(job, owner, result["params"], state)
                return await _step(callback, state, job, "server")
            if not result.get("persisted"):
                await state.update_data(
                    edit_mode=True, edit_job=job_id, pending_params=result["params"], preview_token=None
                )
                return await _step(callback, state, {**job, "pending_params": result["params"]}, "server")
            await _clear_input(state)
            builder = InlineKeyboardBuilder()
            for title, next_action, next_value in job_actions(fresh):
                if next_action != "resetfp":
                    _button(builder, title, next_action, job_id, next_value)
            return await _send(
                callback,
                "<b>Ключ сервера подтверждён и сохранён</b>\nНастройки установки и данные для входа сохранены. Можно продолжить установку из этой карточки.",
                builder,
                state=state,
            )
        if action in {"routes", "routepage", "route", "routefield", "routedel", "routeadd"}:
            if params["role"] not in {"entry", "haproxy_entry"} or job["status"] not in SETTINGS_EDIT_STATUSES:
                raise ValueError("Выходы можно менять у остановленного входного сервера каскада")
            if not editing and job["status"] != "draft":
                await _clear_input(state)
                await state.update_data(edit_mode=True, edit_job=job_id, pending_params=params)
                editing = True
            if action in {"routes", "routepage"}:
                if action == "routepage":
                    if not value.isdecimal():
                        raise ValueError("Неизвестная страница направлений")
                    await state.update_data(routes_page=int(value))
                return await _step(callback, state, job, "routes")
            routes = params.get("cascade_routes") or []
            if action == "routeadd":
                if not (params["exit_install_id"] or job.get("cascade_pair")):
                    return await _step(callback, state, job, "3")
                if params["transport"].startswith("cdn_"):
                    raise ValueError("Несколько выходов доступны для прямых транспортов каскада; сначала смените CDN")
                if params["role"] == "haproxy_entry" and not (
                    params["squad_uuid"] or (job.get("haproxy") or {}).get("squad_uuid")
                ):
                    raise ValueError("Сначала выберите клиентский сквад основного выхода HAProxy")
            else:
                index_text, _, code = value.partition(":")
                if not index_text.isdecimal() or int(index_text) >= len(routes):
                    raise ValueError("Направление изменилось. Откройте список стран заново")
                index = int(index_text)
                route = routes[index]
                if action != "route" and context.get("route_exit_id") != route["exit_install_id"]:
                    raise ValueError("Сначала откройте нужную страну в списке выходов")
                if action == "routedel":
                    job = await _save(job, owner, {"cascade_routes": routes[:index] + routes[index + 1 :]}, state)
                    return await _step(callback, state, job, "routes")
                await _reset_input(state, job_id=job_id, step="routes", route_exit_id=route["exit_install_id"])
                if action == "routefield":
                    if code not in {"cp", "hr"}:
                        raise ValueError("Неизвестная настройка страны")
                    return await _prompt(callback, state, job, code)
                target = _params(await jobs.get_job(route["exit_install_id"], owner))
                builder = InlineKeyboardBuilder()
                _button(builder, "Название хоста", "routefield", job_id, f"{index}:hr")
                _button(builder, "Порт на входе", "routefield", job_id, f"{index}:cp")
                _button(builder, "Убрать направление", "routedel", job_id, str(index))
                _button(builder, "← Выходы и страны", "step", job_id, "routes")
                return await _send(
                    callback,
                    "<b>Направление каскада</b>\n"
                    f"Хост: {_copyable(route['host_remark'])}\n"
                    f"Вход: {_copyable(params['address'])}:{route['client_port']}\n"
                    f"Выход: {_copyable(target['name'])} · {_copyable(target['address'])}\n\n"
                    "Название хоста будет видно клиентам в подписке.",
                    builder,
                    state=state,
                )
        if action in {"squads", "exits", "routeadd"}:
            builder = InlineKeyboardBuilder()
            if action == "squads":
                if params["role"] == "haproxy_entry":
                    raise ValueError("Вход HAProxy наследует сквад выбранного выхода")
                options = (await jobs.list_panel_options(params["panel_ref"])).get("squads") or []
                options = [{"id": item["uuid"], "name": item.get("name") or item["uuid"]} for item in options]
            else:
                if job.get("cascade_pair") and action == "exits":
                    raise ValueError("Нода 2 уже задана в мастере каскада; откройте её настройки")
                selected_ids = {route["exit_install_id"] for route in params.get("cascade_routes") or []}
                if action == "routeadd":
                    selected_ids.add(params["exit_install_id"] or (job.get("cascade_pair") or {}).get("exit_id"))
                squad_uuid = params["squad_uuid"] or (job.get("haproxy") or {}).get("squad_uuid")
                options = [
                    {
                        "id": item["id"],
                        "name": _params(item)["host_remark"] or _params(item)["name"],
                        "address": _params(item)["address"],
                        "port": _params(item)["client_port"],
                        "squad_uuid": _params(item)["squad_uuid"],
                    }
                    for item in await jobs.list_jobs(owner, exits_only=True)
                    if item["status"] in {"succeeded", "warning"}
                    and _params(item)["role"] == ("haproxy_exit" if params["role"] == "haproxy_entry" else "exit")
                    and item["id"] != job_id
                    and item["id"] not in selected_ids
                    and _params(item)["address"] != params["address"]
                    and _params(item)["panel_ref"] == params["panel_ref"]
                    and (params["role"] != "haproxy_entry" or _params(item)["squad_uuid"])
                    and (
                        action != "routeadd"
                        or params["role"] != "haproxy_entry"
                        or _params(item)["squad_uuid"] == squad_uuid
                    )
                    and (
                        action != "routeadd"
                        or params["role"] != "haproxy_entry"
                        or any(
                            ipaddress.ip_address(params["address"]) in ipaddress.ip_network(source)
                            for source in _params(item)["haproxy_sources"]
                        )
                    )
                ]
            option_step = (
                "routes"
                if action == "routeadd"
                else "cascade_target"
                if action == "exits" and context.get("step") == "cascade_target"
                else "3"
                if action == "squads" or params["role"] == "haproxy_entry" or context.get("step") == "3"
                else "cascade"
            )
            await _reset_input(
                state,
                job_id=job_id,
                step=option_step,
                options=options,
                option_kind=action,
                route_exit_id=None,
            )
            for index, option in enumerate(options):
                _button(builder, option["name"], "pick", job_id, str(index))
            if action == "squads" and params["role"] != "haproxy_exit":
                _button(builder, "Без сквада", "pick", job_id, "none")
            if action == "exits" and params["role"] == "haproxy_entry" and job["status"] == "draft" and not editing:
                _button(builder, "Установить новую ноду 2", "newexit", job_id)
            if (
                action == "exits"
                and params["role"] == "entry"
                and job["status"] == "draft"
                and not editing
                and not options
            ):
                _button(builder, "Создать выход VLESS", "new", job_id, "vless_exit")
            _button(builder, "← Назад", "step", job_id, option_step)
            return await _send(
                callback,
                "<b>Выберите сквад</b>\nБез сквада инбаунд не появится в клиентских подписках."
                if action == "squads"
                else "<b>Добавить страну к общему входу</b>\n"
                + (
                    "Выберите установленный выход. Для него будут добавлены отдельный порт входа и хост в панели."
                    if options
                    else "Свободных подходящих выходов нет. Установите выход в этой панели; для HAProxy нужен тот же клиентский сквад и разрешённый IP входа."
                )
                if action == "routeadd"
                else "<b>Выберите установленный выход HAProxy</b>\n"
                + (
                    "Подходят выходы этой панели на другом сервере с выбранным клиентским сквадом."
                    if options
                    else "Готовых выходов этой панели нет. Установите новую ноду 2."
                )
                if params["role"] == "haproxy_entry"
                else "<b>Выберите установленную выходную ноду</b>"
                + (
                    "\nГотовых выходов VLESS этой панели нет. Создайте выход, затем вернитесь к сохранённому входу "
                    "и выберите его. Данные входного сервера сохранятся."
                    if not options
                    else ""
                ),
                builder,
                state=state,
            )
        if action == "pick":
            data = await state.get_data()
            options = data.get("options") or []
            if data.get("job_id") != job_id or data.get("option_kind") not in {"squads", "exits", "routeadd"}:
                raise ValueError("Выбор устарел")
            if value == "none" and data["option_kind"] == "squads":
                if params["role"] == "haproxy_exit":
                    raise ValueError("Для выхода HAProxy требуется клиентский сквад")
                selected = None
            elif value.isdecimal() and int(value) < len(options):
                selected = options[int(value)]["id"]
            else:
                raise ValueError("Выбор устарел")
            if data["option_kind"] == "routeadd":
                if params["role"] not in {"entry", "haproxy_entry"} or (job["status"] != "draft" and not editing):
                    raise ValueError("Откройте настройки стран заново")
                routes = params.get("cascade_routes") or []
                reserved = {params["ssh_port"], params["node_port"], params["client_port"], 80}
                reserved.update(route["client_port"] for route in routes)
                if params["transport"] == "vless_tcp_selfsteal":
                    reserved.add(8443)
                port = next((port for port in range(8443, 65536, 1000) if port not in reserved), None)
                if port is None:
                    port = next(port for port in range(20000, 65536) if port not in reserved)
                option = options[int(value)]
                route = {"exit_install_id": selected, "client_port": port, "host_remark": option["name"][:100]}
                job = await _save(job, owner, {"cascade_routes": [*routes, route]}, state)
                return await _step(callback, state, job, "routes")
            values = {"squad_uuid" if data["option_kind"] == "squads" else "exit_install_id": selected}
            if data["option_kind"] == "exits" and params["role"] == "haproxy_entry":
                option = options[int(value)]
                values["squad_uuid"] = option["squad_uuid"]
                await state.update_data(exit_label={"job_id": job_id, "exit_install_id": selected, **option})
            job = await _save(job, owner, values, state)
            if data["option_kind"] == "squads":
                await state.update_data(
                    squad_label={"job_id": job_id, "uuid": selected, "name": options[int(value)]["name"]}
                    if selected
                    else None
                )
            return await _step(
                callback,
                state,
                job,
                "2"
                if data.get("step") == "cascade_target"
                else "3"
                if data["option_kind"] == "squads" or params["role"] in {"entry", "haproxy_entry"}
                else "cascade",
            )
        if action == "add":
            if value == "shape":
                if (
                    params["transport"].startswith("cdn_")
                    or params["transport"] == "hysteria2"
                    or params["role"] in {"exit", "haproxy_entry", "haproxy_exit"}
                ):
                    raise ValueError("Шейпинг недоступен для этого транспорта или роли")
                shaping = params["shaping"]
                enabled = bool(shaping.get("enabled", bool(shaping)))
                job = await _save(job, owner, {"shaping": {} if enabled else {**shaping, "enabled": True}}, state)
                return await _step(callback, state, job, "4")
            if value not in ADDONS:
                raise ValueError("Неизвестное дополнение")
            if params["role"] == "haproxy_entry" and value not in {"bbr", "fail2ban", "ufw"}:
                raise ValueError("На входе HAProxy доступны только BBR, fail2ban и UFW")
            addons = list(params["addons"])
            addons.remove(value) if value in addons else addons.append(value)
            job = await _save(job, owner, {"addons": addons}, state)
            return await _step(callback, state, job, "4")
        if action == "replace":
            builder = InlineKeyboardBuilder()
            _button(
                builder,
                "Разрешить замену" if not params["replace_existing"] else "Запретить замену",
                "flag",
                job_id,
                "replace",
            )
            _button(builder, "← Назад", "step", job_id, "advanced")
            return await _send(
                callback,
                "<b>Замена ресурсов</b>\nПодтверждение позволяет заменить существующий контейнер с резервной копией и ресурсы этой установки. Объекты других нод сохраняются.",
                builder,
                state=state,
            )
        if action == "flag":
            field = {"host": "publish_host", "replace": "replace_existing"}.get(value)
            if not field:
                raise ValueError("Неизвестный переключатель")
            if field == "publish_host" and params["role"] == "haproxy_exit":
                raise ValueError("Хост создаётся на входе HAProxy")
            job = await _save(job, owner, {field: not params[field]}, state)
            return await _step(callback, state, job, "3" if value == "host" else "advanced")
        if action == "site":
            job = await _save(
                job,
                owner,
                {"site_template": "standard" if params["site_template"] == "streaming" else "streaming"},
                state,
            )
            return await _step(callback, state, job, "2")
        if action == "shape":
            data = await state.get_data()
            if job["status"] in {"succeeded", "warning"} and not editing:
                await _clear_input(state)
                await state.update_data(edit_mode=True, edit_job=job_id, pending_params=params)
            return await _shaping(callback, state, job)
        if action == "shon":
            data = await state.get_data()
            shaping = {
                field: (data.get("shaping") or {}).get(field, default) for field, _, default in SHAPING_FIELDS.values()
            }
            shaping["enabled"] = value == "1"
            if data.get("manage"):
                await state.update_data(shaping=shaping)
            else:
                job = await _save(job, owner, {"shaping": shaping if shaping["enabled"] else {}}, state)
            return await _shaping(callback, state, job, manage=bool(data.get("manage")))
        if action == "shapply":
            if not editing:
                raise ValueError("Откройте раздел «Изменить настройки» заново")
            return await _changes_screen(callback, state, job, owner)
        if action == "summary":
            if editing:
                return await _changes_screen(callback, state, job, owner)
            pair = job.get("cascade_pair") or {}
            if pair.get("exit_id") == job_id:
                job = await _effective_job(await jobs.get_job(pair["entry_id"], owner), state)
                job_id, params = job["id"], _params(job)
            target = job.get("cascade_exit")
            if target:
                for step in BASIC_STEPS:
                    if await _missing_input(callback, state, target, step):
                        return
                InstallSpec.from_dict(_params(target))
            for step in BASIC_STEPS:
                if await _missing_input(callback, state, job, step):
                    return
            if target:
                params = {**params, "exit_install_id": target["id"], "squad_uuid": _params(target)["squad_uuid"]}
            InstallSpec.from_dict(params)
            builder = InlineKeyboardBuilder()
            _button(builder, "Установить каскад" if target else "Установить ноду", "start", job_id)
            _button(builder, "← Изменить параметры", "step", job_id, "4")
            _button(builder, "Сквад и хост", "step", job_id, "3")
            if target:
                _button(builder, "Нода 2 — выход", "step", target["id"], "2")
            await _clear_input(state)
            text = _summary({**job, "params": params})
            if target:
                text = (
                    "<b>Проверьте каскад: нода 1 → нода 2</b>\n\n"
                    + text
                    + "\n\n"
                    + _summary(target)
                    + "\n\nБот подготовит выход, затем настроит вход и свяжет оба сервера."
                )
            return await _send(callback, text, builder, state=state)
        if action == "start":
            if editing:
                raise ValueError("Для применения настроек сначала нажмите «Показать изменения»")
            job = await jobs.start_job(job_id, owner, message_id=callback.message.message_id)
            return await _job_screen(callback, state, job)
        if action == "retry":
            if (
                not job.get("deletion")
                and job["status"] in {"failed", "paused", "warning"}
                and params["transport"] == "cdn_vk"
                and _vk_missing_auth(job)[0]
            ):
                if not editing and not params["cloud"].get("project_id"):
                    await state.update_data(edit_mode=True, edit_job=job_id, pending_params=params)
                return await _vk_auth_setup(callback, state, job)
            if editing and params != stored_params:
                return await _changes_screen(callback, state, job, owner)
            job = await jobs.retry_job(job_id, owner)
            return await _job_screen(callback, state, job)
        if action == "delask":
            if job["status"] not in DELETABLE_STATUSES or job.get("can_delete") is False:
                raise ValueError("Сначала остановите текущую операцию и дождитесь её завершения")
            result = await jobs.prepare_deletion(job_id, owner)
            if not result.get("token"):
                raise ValueError("Не удалось подготовить удаление. Обновите карточку и повторите попытку")
            has_panel_changes = bool(result.get("has_panel_changes"))
            can_remove_server = result.get("can_remove_server") is True and not job.get("deletion")
            allow_panel = (
                has_panel_changes and job["status"] not in {"succeeded", "warning"} and not job.get("deletion")
            )
            await _reset_input(
                state,
                job_id=job_id,
                delete_job=job_id,
                delete_token=result["token"],
                delete_has_panel_changes=has_panel_changes,
                delete_can_remove_server=can_remove_server,
                delete_allowed_modes=[
                    "record",
                    *(["panel"] if allow_panel else []),
                    *(["full"] if can_remove_server else []),
                ],
            )
            builder = InlineKeyboardBuilder()
            lines = [
                "<b>Удаление установки</b>",
                f"Нода: {_copyable(deletion_params['name'])}",
                f"Сервер: {_copyable(deletion_params['address'])}",
                "",
            ]
            lines.append(
                "Только из бота — удалить запись и сохранённые доступы. Настройки VPS, объекты панели и облачные ресурсы останутся в текущем состоянии."
            )
            _button(builder, "Только из бота", "delete", job_id, "record")
            if job.get("deletion"):
                lines.append(
                    "Для продолжения уже выбранного удаления вернитесь в карточку и нажмите «Повторить удаление»."
                )
            if can_remove_server:
                lines.append(
                    "Полное удаление — также удалить созданные установщиком файлы, контейнеры и настройки на VPS, собственные объекты Remnawave, технический доступ каскада и ресурсы CDN. Подключения через эту ноду перестанут работать."
                )
                _button(builder, "Полное удаление", "delconfirm", job_id, "full")
                _button(builder, "Обновить доступы для полного удаления", "delauth", job_id)
            elif result.get("server_cleanup_error"):
                lines.append("Полное удаление недоступно: " + escape(str(result["server_cleanup_error"])[:600]))
            if allow_panel:
                lines.extend([
                    "Из бота и панели — удалить запись и собственные хост, ноду, профиль и технический доступ каскада; инбаунд установки будет убран из сквада. VPS и общие сквады сохранятся.",
                ])
                _button(builder, "Из бота и панели", "delete", job_id, "panel")
            cloud = (job.get("resources") or {}).get("cdn")
            if cloud or job.get("retained_resources") or job.get("has_cloud_resources"):
                lines.append(
                    "При полном удалении бот удалит созданные им ресурсы CDN, включая сохранённые прежние ресурсы. В остальных вариантах CDN остаётся в облаке и может оплачиваться."
                )
                _button(builder, "Посмотреть ресурсы CDN", "res", job_id)
            lines.append("Восстановить удалённую запись из этого меню нельзя.")
            _button(builder, "← Не удалять", "show", job_id)
            return await _send(callback, "\n".join(lines), builder, state=state)
        if action == "delconfirm":
            if (
                context.get("delete_job") != job_id
                or not context.get("delete_token")
                or value != "full"
                or context.get("delete_can_remove_server") is not True
            ):
                raise ValueError("Подтверждение устарело. Нажмите «Удалить ноду» в карточке заново")
            await state.update_data(delete_mode="full")
            builder = InlineKeyboardBuilder()
            _button(builder, "Подтвердить полное удаление", "delete", job_id, "full")
            _button(builder, "← Выбрать другой вариант", "delask", job_id)
            _button(builder, "← Не удалять", "show", job_id)
            lines = [
                "<b>Подтверждение полного удаления</b>",
                f"Нода: {_copyable(deletion_params['name'])}",
                f"Сервер: {_copyable(deletion_params['address'])}",
                "",
                "Будут удалены созданные установщиком файлы, контейнеры и настройки ноды на VPS, её собственные объекты Remnawave, ресурсы CDN, запись и сохранённые доступы из бота. Подключения через ноду перестанут работать.",
                "Сам VPS, общесистемные пакеты, аккаунт облачного провайдера и общие объекты панели сохранятся.",
            ]
            if (
                (job.get("resources") or {}).get("cdn")
                or job.get("retained_resources")
                or job.get("has_cloud_resources")
            ):
                lines.append(
                    "Будут удалены и текущие, и сохранённые прежние ресурсы CDN этой установки. DNS-записи у регистратора домена нужно удалить вручную."
                )
            lines.append("Отменить уже выполненные этапы и восстановить запись из этого меню нельзя.")
            return await _send(callback, "\n".join(lines), builder, state=state)
        if action == "stopask":
            builder = InlineKeyboardBuilder()
            _button(
                builder,
                "Подтвердить отмену",
                "stop",
                job_id,
            )
            _button(builder, "← Назад", "show", job_id)
            return await _send(
                callback,
                "Остановить удаление? Уже выполненные изменения на VPS, в панели и облаке сохранятся. Запись останется в боте; удаление можно будет повторить."
                if job.get("deletion")
                else "Остановить задачу? Уже созданные ресурсы сохранятся, секреты будут удалены.",
                builder,
                state=state,
            )
        if action == "stop":
            return await _job_screen(callback, state, await jobs.cancel_job(job_id, owner))
        if action == "delete":
            if (
                context.get("delete_job") != job_id
                or not context.get("delete_token")
                or value not in context.get("delete_allowed_modes", [])
                or (value == "panel" and not context.get("delete_has_panel_changes"))
                or (
                    value == "full"
                    and (context.get("delete_can_remove_server") is not True or context.get("delete_mode") != "full")
                )
            ):
                raise ValueError("Подтверждение устарело. Нажмите «Удалить запись» в карточке заново")
            result = await jobs.delete_job(
                job_id,
                owner,
                remove_panel=value in {"panel", "full"},
                remove_server=value == "full",
                token=context["delete_token"],
                message_id=callback.message.message_id,
            )
            if result is None:
                return await _show_jobs(callback, state, owner)
            return await _job_screen(callback, state, result)
        if action == "manage":
            if job["status"] not in SETTINGS_EDIT_STATUSES:
                raise ValueError("Дождитесь остановки установки")
            await _clear_input(state)
            await state.update_data(edit_mode=True, edit_job=job_id, pending_params=params)
            return await _edit_menu(callback, state, job)
        if action == "riask":
            if value not in {*ADDONS, "shape", "all"}:
                raise ValueError("Неизвестный вариант переустановки")
            builder = InlineKeyboardBuilder()
            _button(builder, "Подтвердить переустановку", "reinst", job_id, value)
            _button(builder, "← Назад", "manage", job_id)
            detail = ""
            if value == "shape":
                detail = " Шейпинг будет включён с последними параметрами либо значениями 200/100 Мбит/с, 30 секунд, 5 минут."
            elif value in ADDONS:
                detail = " Будет добавлено дополнение: " + ADDONS[value] + "."
            return await _send(
                callback,
                "Повторно установить контейнер и выбранные дополнения этой ноды? Возможен перерыв подключения."
                + detail,
                builder,
                state=state,
            )
        if action == "reinst":
            if value not in {*ADDONS, "shape", "all"}:
                raise ValueError("Неизвестный вариант переустановки")
            addons = [*params["addons"], value] if value in ADDONS and value not in params["addons"] else None
            shaping = {**params["shaping"], "enabled": True} if value == "shape" else None
            job = await jobs.reinstall_job(job_id, owner, addons=addons, shaping=shaping)
            return await _job_screen(callback, state, job)
        if action == "rmask":
            if value not in params["addons"]:
                raise ValueError("Дополнение не установлено")
            builder = InlineKeyboardBuilder()
            _button(builder, "Удалить " + ADDONS[value], "rm", job_id, value)
            _button(builder, "← Не удалять", "manage", job_id)
            return await _send(
                callback, "Удалить дополнение с сервера и убрать его настройки из профиля?", builder, state=state
            )
        if action == "rm":
            if value not in ADDONS:
                raise ValueError("Неизвестное дополнение")
            if job["status"] not in SETTINGS_EDIT_STATUSES:
                raise ValueError("Дождитесь остановки установки")
            if not editing:
                await _clear_input(state)
                await state.update_data(edit_mode=True, edit_job=job_id, pending_params=params)
            job = await _save(job, owner, {"addons": [name for name in params["addons"] if name != value]}, state)
            return await _changes_screen(callback, state, job, owner)
        raise ValueError("Действие устарело; откройте список установок")
    except (ValueError, PermissionError, LookupError) as error:
        await _report_error(
            callback,
            state,
            str(error),
            job_id,
            host_key_changed=getattr(error, "step", "") == "ssh_host_key",
            deleting=action in {"delask", "delconfirm", "delete"},
        )
    except Exception as error:
        _log_unexpected(error, action, job_id)
        await _report_error(
            callback,
            state,
            "Не удалось выполнить действие. Обновите список установок и повторите попытку.",
            job_id,
            deleting=action in {"delask", "delconfirm", "delete"},
        )


@router.message(RemnaInstallState.value)
@router.message(RemnaInstallState.secret)
async def installer_input(message: Message, state: FSMContext, session=None):
    """Сохраняет поле мастера без записи секретов в FSM."""
    await release_session_early(session)
    data = await state.get_data()
    job_id, code = data.get("job_id"), data.get("field")
    secret = await state.get_state() == RemnaInstallState.secret.state
    try:
        job = await _effective_job(await jobs.get_job(job_id, message.from_user.id), state)
        deletion_credentials = (
            job.get("deletion")
            and data.get("deletion_credentials")
            and job.get("deletion_requires_credentials")
            and job.get("can_delete") is True
            and secret
            and code in _deletion_secret_codes(job)
        )
        deletion_prep_credentials = (
            not job.get("deletion")
            and data.get("deletion_prep_job") == job_id
            and job.get("can_delete") is True
            and secret
            and code in _deletion_secret_codes(job)
        )
        if job.get("deletion") and not deletion_credentials:
            raise ValueError("Для этой записи уже выбрано удаление. Откройте карточку установки")
        if data.get("deletion_prep_job") and not deletion_prep_credentials:
            raise ValueError("Подготовка доступов устарела. Откройте карточку установки")
        if message.text in {"/cancel", "/отмена"}:
            await _clear_input(state)
            return await _job_screen(message, state, job)
        if secret:
            if code not in SECRET_FIELDS:
                raise ValueError("Запрос устарел")
            text = message.text
            if message.document:
                if (message.document.file_size or 0) > 65536:
                    raise ValueError("Документ должен быть не больше 64 КБ")
                buffer = io.BytesIO()
                await message.bot.download(message.document, destination=buffer)
                if buffer.tell() > 65536:
                    raise ValueError("Документ должен быть не больше 64 КБ")
                try:
                    text = buffer.getvalue().decode("utf-8")
                except UnicodeDecodeError as error:
                    raise ValueError("Нужен текстовый UTF-8 документ") from error
            if not text or len(text.encode()) > 65536:
                raise ValueError("Отправьте текст или документ не больше 64 КБ")
            field = SECRET_FIELDS[code][0]
            if code in {"vd", "vu"}:
                text = text.strip()
                if not text or any(ord(char) < 32 or ord(char) == 127 for char in text) or len(text) > 255:
                    raise ValueError("Отправьте значение из OpenStack RC одной строкой")
            if code in {"ya", "pc"} and not (code == "pc" and text == "-"):
                try:
                    parsed = json.loads(text)
                    if not isinstance(parsed, dict):
                        raise ValueError
                    if code == "ya" and not all(
                        isinstance(parsed.get(name), str) and parsed[name]
                        for name in ("id", "service_account_id", "private_key")
                    ):
                        raise ValueError
                except (ValueError, TypeError) as error:
                    raise ValueError("Некорректный JSON конфигурации; содержимое не показывается") from error
            secrets = {field: "" if text == "-" and code in {"pp", "pc"} else text}
            if code == "pw":
                secrets.update(ssh_private_key="", ssh_key_passphrase="")
            elif code == "pk":
                secrets["ssh_password"] = ""
            if deletion_credentials or deletion_prep_credentials:
                job = await jobs.update_deletion_credentials(job_id, message.from_user.id, secrets_patch=secrets)
                return await _job_screen(message, state, job)
            await jobs.update_draft(job_id, message.from_user.id, secrets=secrets)
            await state.update_data(preview_token=None)
            job = await jobs.get_job(job_id, message.from_user.id)
            context = await state.get_data()
            if context.get("vk_auth_setup") and code in {"vk", "vd", "vu", "vw"}:
                return await _vk_auth_setup(message, state, job)
            if context.get("edit_mode") and context.get("edit_job") == job_id:
                if job["status"] == "failed" and context.get("pending_params") == _params(job):
                    return await _job_screen(message, state, job)
                return await _step(message, state, job, data.get("step") if data.get("step") in STEP_NAMES else "1")
            if job["status"] not in EDITABLE_STATUSES:
                return await _job_screen(message, state, job)
            if code == "pw" and data.get("step") == "1":
                if not _params(job)["address"]:
                    return await _server_input(message, state, job, "ip")
                return await _step(message, state, job, "2")
            return await _step(
                message,
                state,
                job,
                data.get("step")
                if data.get("step") in STEP_NAMES
                else "2"
                if code in {"vk", "vd", "vu", "vw", "ya"}
                else "4"
                if code == "pc"
                else "1",
            )
        if code not in FIELDS and code not in SHAPING_FIELDS:
            raise ValueError("Запрос устарел")
        if not message.text:
            raise ValueError("Отправьте значение текстом")
        value = _parse_value(code, message.text)
        if data.get("step") == "routes" and data.get("route_exit_id") and code in {"cp", "hr"}:
            params = _params(job)
            routes = params.get("cascade_routes") or []
            if not any(route["exit_install_id"] == data["route_exit_id"] for route in routes):
                raise ValueError("Направление изменилось. Откройте список стран заново")
            if code == "hr" and not value:
                raise ValueError("Введите название хоста страны")
            updated = [
                {**route, FIELDS[code][0]: value} if route["exit_install_id"] == data["route_exit_id"] else route
                for route in routes
            ]
            job = await _save(job, message.from_user.id, {"cascade_routes": updated}, state)
            return await _step(message, state, job, "routes")
        if code in SHAPING_FIELDS:
            shaping = dict(data.get("shaping") or _params(job)["shaping"])
            shaping[SHAPING_FIELDS[code][0]] = value
            shaping.setdefault("enabled", True)
            if data.get("manage"):
                await state.update_data(shaping=shaping)
            else:
                job = await _save(job, message.from_user.id, {"shaping": shaping}, state)
            return await _shaping(message, state, job, manage=bool(data.get("manage")))
        values = (
            {"cloud": {**_params(job)["cloud"], FIELDS[code][0]: value}}
            if code in {"fd", "vp"}
            else {FIELDS[code][0]: value}
        )
        job = await _save(job, message.from_user.id, values, state)
        if code == "vp" and data.get("vk_auth_setup"):
            return await _vk_auth_setup(message, state, job)
        if code == "ip" and data.get("step") == "1" and job["status"] == "draft" and not data.get("edit_mode"):
            return await _server_input(message, state, job, "pw")
        return await _step(message, state, job, data.get("step", "1"))
    except (ValueError, PermissionError, LookupError) as error:
        await _report_error(
            message, state, str(error), job_id, host_key_changed=getattr(error, "step", "") == "ssh_host_key"
        )
    except Exception as error:
        _log_unexpected(error, "input", job_id)
        await _report_error(
            message, state, "Не удалось сохранить поле. Повторите ввод или вернитесь в список установок.", job_id
        )
    finally:
        try:
            await message.delete()
        except TelegramAPIError:
            pass

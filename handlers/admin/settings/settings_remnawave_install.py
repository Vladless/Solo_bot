import hashlib
import io
import ipaddress
import json
import logging
import re

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
from services.remnawave_installer.progress import job_actions, job_status_text
from services.remnawave_installer.types import ADDONS, DELETABLE_STATUSES, ROLES, TRANSPORTS, InstallError, InstallSpec

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
    "nm": ("name", "Название ноды", "3–30 символов: латиница, цифры, пробел, дефис и подчёркивание."),
    "ip": ("address", "IP сервера", "Укажите IPv4 или IPv6 сервера."),
    "us": ("ssh_username", "SSH-логин", "Например: root."),
    "sp": ("ssh_port", "SSH-порт", "Целое число от 1 до 65535."),
    "np": ("node_port", "Порт управления нодой", "Обычно 2222; должен отличаться от SSH и клиентского порта."),
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
    "cp": ("client_port", "Порт клиентов", "Обычно 443; должен отличаться от SSH и порта управления."),
    "rd": ("reality_domain", "Домен Reality", "Домен сайта-донора без протокола и пути."),
    "dm": ("domain", "Домен сайта / origin", "Домен без протокола и пути; его DNS должен указывать на IP сервера."),
    "cd": ("cdn_domain", "Домен CDN", "Отдельный домен для CDN, отличный от origin."),
    "em": ("email", "Email сертификата", "Адрес для Let's Encrypt."),
    "pn": (
        "profile_name",
        "Имя Config Profile",
        "2–29 символов: латиница, цифры, пробел, дефис и подчёркивание; «-» создаст имя автоматически.",
    ),
    "hr": ("host_remark", "Название хоста", "До 100 символов; «-» использует название ноды."),
    "ei": ("exit_ip", "Выходной IP", "Дополнительный IP сервера; «-» использует основной адрес."),
    "ef": ("exit_interface", "Интерфейс выходного IP", "Например: eth0; «-» для автоматического определения."),
    "fd": ("folder_id", "Каталог Яндекс Cloud", "ID каталога; «-» выберет первый доступный каталог."),
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
    "ya": (
        "yandex_key",
        "Ключ Яндекс Cloud",
        "Отправьте JSON авторизованного ключа сервисного аккаунта: id, service_account_id, private_key. Можно документом до 64 КБ.",
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
            "server_field",
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
    return job


async def _save(job: dict, owner: int, values: dict, state: FSMContext) -> dict:
    """Сохраняет полные параметры без помещения секретов в FSM."""
    params = {**_params(job), **values}
    if any(key in values and values[key] != _params(job).get(key) for key in ("address", "ssh_port")):
        params["host_fingerprint"] = ""
    if params["transport"].startswith("cdn_") or params["role"] == "exit":
        params["shaping"] = {}
    data = await state.get_data()
    if job["status"] == "draft" and not data.get("edit_mode") and {"ssh_port", "transport"}.intersection(values):
        reserved = {params["ssh_port"], params["client_port"], 80, 443, 4443, 7443, 8443}
        if params["node_port"] == 2222 and params["ssh_port"] == 2222:
            params["node_port"] = next(port for port in range(2223, 2232) if port not in reserved)
        if params["client_port"] == 443 and params["ssh_port"] == 443 and not params["transport"].startswith("cdn_"):
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


def _field_button(builder, lines: list[str], params: dict, job_id: str, code: str) -> None:
    """Добавляет текущее значение и кнопку изменения поля."""
    field, title, _ = FIELDS[code]
    value = params["cloud"].get("folder_id") if code == "fd" else params[field]
    lines.append(f"{title}: {_display(value)}")
    _button(builder, title, "f", job_id, code)


async def _step(event, state: FSMContext, job: dict, step: str = "1") -> None:
    """Показывает основной шаг или отдельный раздел настроек."""
    job = await _effective_job(job, state)
    if step not in BASIC_STEPS:
        return await _advanced(event, state, job, step)
    await _reset_input(state, job_id=job["id"], step=step, wizard_step=step)
    editing = (await state.get_data()).get("edit_mode", False)
    if step == "1" and job["status"] == "draft" and not editing:
        data = await state.get_data()
        return await _server_input(event, state, job, data.get("server_field", "ip"))
    params = _params(job)
    builder = InlineKeyboardBuilder()
    job_id = job["id"]
    lines = [
        f"<b>{'Настройки ноды' if editing else 'Установка ноды'} · {step}/{len(BASIC_STEPS)} · {BASIC_STEPS[step]}</b>"
    ]
    if step == "1":
        lines.extend([
            f"IP сервера: {_display(params['address'])}",
            f"SSH-порт: {params['ssh_port']}",
            f"SSH-логин: {_display(params['ssh_username'])}",
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
        _choices(
            builder,
            [
                (("✅ " if transport == params["transport"] else "") + title, "tr", str(index))
                for index, (transport, title) in enumerate(TRANSPORTS.items())
            ],
            job_id,
        )
        if params["transport"] in {"vless_tcp_selfsteal", "cdn_vk", "cdn_yandex"}:
            for code in ("dm", "em"):
                _field_button(builder, lines, params, job_id, code)
        if params["transport"].startswith("cdn_"):
            _field_button(builder, lines, params, job_id, "cd")
            code, field = ("vk", "vk_token") if params["transport"] == "cdn_vk" else ("ya", "yandex_key")
            lines.append(SECRET_FIELDS[code][1] + ": " + _secret_status(job, field))
            _button(builder, SECRET_FIELDS[code][1], "s", job_id, code)
            lines.append(
                "Укажите два домена: origin с A/AAAA на IP сервера и домен CDN. После создания CDN бот покажет нужную запись CNAME и дождётся DNS."
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
        else:
            lines.append(f"Название хоста: {_display(params['host_remark'] or params['name'])}")
            _button(builder, "Название хоста", "f", job_id, "hr")
            lines.append(f"Сквад: {_display(job.get('squad_name') or params['squad_uuid'])}")
            _button(builder, "Выбрать сквад", "squads", job_id)
            _button(builder, ("✅ " if params["publish_host"] else "⬜ ") + "Создать хост", "flag", job_id, "host")
            lines.append(
                "Выберите сквад, в подписках которого должна появиться нода. Если оставить без сквада, инбаунд можно привязать позже."
            )
    else:
        lines.append("Выберите нужные дополнения или продолжите без них.")
        choices = [
            (("✅ " if name in params["addons"] else "⬜ ") + title, "add", name) for name, title in ADDONS.items()
        ]
        shaping_available = not params["transport"].startswith("cdn_") and params["role"] != "exit"
        shaping_enabled = bool(params["shaping"].get("enabled", bool(params["shaping"])))
        if shaping_available:
            choices.append((("✅ " if shaping_enabled else "⬜ ") + "Шейпинг скорости", "add", "shape"))
        else:
            lines.append("Шейпинг скорости недоступен для CDN и выхода каскада.")
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
            "Показать изменения" if editing else "Проверить и установить",
            "preview" if editing else "summary",
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
    await _reset_input(state, job_id=job_id, step=section)
    data = await state.get_data()
    builder = InlineKeyboardBuilder()
    lines = [f"<b>{STEP_NAMES[section]}</b>"]
    if section == "advanced":
        lines.append("Обычная установка не требует изменений здесь.")
        for key in ("server", "network", "profile", "cascade"):
            _button(builder, STEP_NAMES[key], "step", job_id, key)
        if params["transport"] == "cdn_yandex":
            _field_button(builder, lines, params, job_id, "fd")
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
            for code in ("np", "cp", "ps", "ss"):
                _field_button(builder, lines, params, job_id, code)
            if params["transport"] in {"vless_tcp_reality", "vless_xhttp_reality"}:
                _field_button(builder, lines, params, job_id, "rd")
            lines.append(
                "Ограничение по IP необязательно. Управление нодой проверяет сертификат панели и токен доступа."
            )
        elif section == "profile":
            _field_button(builder, lines, params, job_id, "pn")
            lines.append("Профиль создаётся автоматически. Здесь можно задать собственное имя.")
        elif section == "cascade":
            lines.append(f"Роль: {ROLES[params['role']]}")
            _button(builder, "Роль ноды", "roles", job_id)
            if params["role"] == "entry":
                lines.append(f"Выходная нода: {_display(params['exit_install_id'])}")
                _button(builder, "Выходная нода каскада", "exits", job_id)
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
        "<b>Всё готово к установке</b>",
        f"Сервер: {_display(params['address'])}",
        f"SSH: {_display(params['ssh_username'])}, порт {params['ssh_port']}",
        f"Транспорт: {TRANSPORTS[params['transport']]}",
        "Дополнения: " + (", ".join(addons) or "без дополнений"),
    ]
    if params["role"] != "normal":
        lines.append("Роль: " + ROLES[params["role"]])
    if params["client_port"] != 443 and not params["transport"].startswith("cdn_"):
        lines.append(f"Порт клиентов: {params['client_port']}")
    if params["transport"] in {"vless_tcp_selfsteal", "cdn_vk", "cdn_yandex"}:
        lines.extend([
            f"Домен / origin: {_display(params['domain'])}",
            f"Email сертификата: {_display(params['email'])}",
        ])
    if params["transport"].startswith("cdn_"):
        lines.extend([
            f"Домен CDN: {_display(params['cdn_domain'])}",
            "Ресурсы CDN оплачиваются у провайдера и сохраняются при отмене установки.",
        ])
    if params["role"] != "exit":
        lines.append(f"Название хоста: {_display(params['host_remark'] or params['name'])}")
        lines.append("Создать хост: " + ("да" if params["publish_host"] else "нет"))
    if params["squad_uuid"]:
        lines.append(f"Сквад: {_display(job.get('squad_name') or params['squad_uuid'])}")
    elif params["role"] != "exit":
        lines.append(
            "Сквад не выбран. После установки добавьте инбаунд в нужный сквад или выберите его на шаге «Сквад и хост»."
        )
    lines.append("Бот подключит ноду к настроенной панели и создаст профиль. Ход установки появится в этом сообщении.")
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
        lines.append(
            f"{_display(change.get('label') or change.get('field'))}: {_display(change.get('before'))} → {_display(change.get('after'))}"
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
    if params["transport"].startswith("cdn_") or params["role"] == "exit":
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
    if code == "nm" and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]{2,29}", value):
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
    return value


async def _prompt(event, state: FSMContext, job: dict, code: str, *, secret: bool = False) -> None:
    """Запрашивает одно поле мастера."""
    data = await state.get_data()
    if code in SHAPING_FIELDS:
        _, title, _ = SHAPING_FIELDS[code]
        hint = "Введите целое число."
    else:
        _, title, hint = (SECRET_FIELDS if secret else FIELDS)[code]
    await state.set_state(RemnaInstallState.secret if secret else RemnaInstallState.value)
    await state.update_data(job_id=job["id"], field=code, step=data.get("step", "1"))
    builder = InlineKeyboardBuilder()
    if code in {"ip", "pw"} and data.get("step", "1") == "1" and job["status"] == "draft" and not data.get("edit_mode"):
        params = _params(job)
        await state.update_data(server_field=code)
        lines = [f"<b>Установка ноды · 1/{len(BASIC_STEPS)} · Сервер</b>"]
        if code == "ip":
            lines.extend(["", "<b>Введите IP сервера</b>", escape(FIELDS["ip"][2])])
            if params["address"]:
                lines.append(f"Текущий IP: {_display(params['address'])}")
                _button(builder, "Оставить IP →", "servernext", job["id"], "pw")
        else:
            lines.extend([f"IP сервера: {_display(params['address'])}", "", "<b>Введите пароль SSH</b>", escape(hint)])
            if set(job.get("credential_fields") or []).intersection({"ssh_password", "ssh_private_key"}):
                _button(builder, "Использовать сохранённый доступ →", "servernext", job["id"], "2")
            _button(builder, "← Изменить IP", "f", job["id"], "ip")
        lines.append(f"Подключение: {_display(params['ssh_username'])}, порт {params['ssh_port']}.")
        _button(builder, "⚙️ Дополнительные настройки SSH", "step", job["id"], "ssh")
        _button(builder, "Сохранить и закрыть", "show", job["id"])
        if secret:
            lines.append("Сообщение с паролем будет скрыто в журналах и удалено после обработки.")
        return await _send(event, "\n".join(lines), builder, state=state)
    _button(
        builder,
        "← Назад",
        "shape" if data.get("step") == "shape" else "step",
        job["id"],
        data.get("step", "1") if data.get("step") != "shape" else "",
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
    credentials = set(job.get("credential_fields") or [])
    code, secret = "", False
    if step == "1":
        if not params["address"]:
            code = "ip"
        elif not credentials.intersection({"ssh_password", "ssh_private_key"}):
            code, secret = "pw", True
    elif step == "2":
        if params["transport"] in {"vless_tcp_selfsteal", "cdn_vk", "cdn_yandex"}:
            for field, candidate in (("domain", "dm"), ("email", "em")):
                if not params[field]:
                    code = candidate
                    break
        if not code and params["transport"].startswith("cdn_"):
            if not params["cdn_domain"]:
                code = "cd"
            else:
                provider, field = ("vk", "vk_token") if params["transport"] == "cdn_vk" else ("ya", "yandex_key")
                if field not in credentials:
                    code, secret = provider, True
    if not code:
        return False
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
            await _clear_input(state)
            job = await jobs.create_draft(owner, callback.message.chat.id)
            return await _step(callback, state, job)
        job = await jobs.get_job(job_id, owner)
        stored_params = _params(job)
        await state.set_state(None)
        job = await _effective_job(job, state)
        context = await state.get_data()
        editing = context.get("edit_mode") and context.get("edit_job") == job_id
        params = _params(job)
        if job.get("deletion") and action not in {"show", "res", "delask", "delete", "retry", "stopask", "stop"}:
            raise ValueError("Для этой записи уже выбрано удаление. Откройте её карточку и продолжите удаление")
        if action == "show":
            return await _job_screen(callback, state, job)
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
        if action == "step":
            if value not in STEP_NAMES or (
                job["status"] not in EDITABLE_STATUSES and not (editing and job["status"] in SETTINGS_EDIT_STATUSES)
            ):
                raise ValueError("Эту установку сейчас нельзя редактировать")
            previous = context.get("step")
            if (
                not editing
                and previous in {"1", "2"}
                and value == str(int(previous) + 1)
                and await _missing_input(callback, state, job, previous)
            ):
                return
            return await _step(callback, state, job, value)
        if action == "edit":
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
        if action in {"auth", "authkeys"}:
            if job["status"] not in SECRET_EDIT_STATUSES:
                raise ValueError("Сейчас учётные данные обновлять нельзя")
            if action == "authkeys":
                if not editing and job["status"] not in EDITABLE_STATUSES:
                    await _clear_input(state)
                    await state.update_data(edit_mode=True, edit_job=job_id, pending_params=params)
                return await _step(callback, state, job, "server")
            builder = InlineKeyboardBuilder()
            for code in (
                "pw",
                "vk" if params["transport"] == "cdn_vk" else "ya" if params["transport"] == "cdn_yandex" else "",
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
        if action in {"f", "s", "sf"}:
            if (
                job["status"] not in EDITABLE_STATUSES
                and not (editing and job["status"] in SETTINGS_EDIT_STATUSES)
                and not (action == "sf" and job["status"] in {"succeeded", "warning"})
                and not (action == "s" and job["status"] in SECRET_EDIT_STATUSES)
            ):
                raise ValueError("Параметры выполняющейся установки менять нельзя")
            return await _prompt(callback, state, job, value, secret=action == "s")
        if action in {"roles", "tr"}:
            if action == "roles":
                builder = InlineKeyboardBuilder()
                for role, title in ROLES.items():
                    _button(builder, title, "role", job_id, role)
                return await _send(
                    callback,
                    "<b>Роль ноды</b>\nДля входа каскада понадобится уже установленная выходная нода.",
                    builder,
                    state=state,
                )
            transports = list(TRANSPORTS)
            if not value.isdecimal() or int(value) >= len(transports):
                raise ValueError("Неизвестный транспорт")
            job = await _save(job, owner, {"transport": transports[int(value)]}, state)
            return await _step(callback, state, job, "2")
        if action == "role":
            if value not in ROLES:
                raise ValueError("Неизвестная роль")
            job = await _save(job, owner, {"role": value}, state)
            return await _step(callback, state, job, "cascade")
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
                f"Сервер: {_display(params['address'])}, порт {params['ssh_port']}",
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
        if action in {"squads", "exits"}:
            builder = InlineKeyboardBuilder()
            if action == "squads":
                options = (await jobs.list_panel_options(params["panel_ref"])).get("squads") or []
                options = [{"id": item["uuid"], "name": item.get("name") or item["uuid"]} for item in options]
            else:
                options = [
                    {"id": item["id"], "name": _params(item)["name"]}
                    for item in await jobs.list_jobs(owner)
                    if item["status"] in {"succeeded", "warning"}
                    and _params(item)["role"] == "exit"
                    and item["id"] != job_id
                ]
            await _reset_input(
                state,
                job_id=job_id,
                step="3" if action == "squads" else "cascade",
                options=options,
                option_kind=action,
            )
            for index, option in enumerate(options):
                _button(builder, option["name"], "pick", job_id, str(index))
            if action == "squads":
                _button(builder, "Без сквада", "pick", job_id, "none")
            _button(builder, "← Назад", "step", job_id, "3" if action == "squads" else "cascade")
            return await _send(
                callback,
                "<b>Выберите сквад</b>\nБез сквада инбаунд не появится в клиентских подписках."
                if action == "squads"
                else "<b>Выберите установленную выходную ноду</b>",
                builder,
                state=state,
            )
        if action == "pick":
            data = await state.get_data()
            options = data.get("options") or []
            if data.get("job_id") != job_id or data.get("option_kind") not in {"squads", "exits"}:
                raise ValueError("Выбор устарел")
            if value == "none" and data["option_kind"] == "squads":
                selected = None
            elif value.isdecimal() and int(value) < len(options):
                selected = options[int(value)]["id"]
            else:
                raise ValueError("Выбор устарел")
            job = await _save(
                job, owner, {"squad_uuid" if data["option_kind"] == "squads" else "exit_install_id": selected}, state
            )
            if data["option_kind"] == "squads":
                await state.update_data(
                    squad_label={"job_id": job_id, "uuid": selected, "name": options[int(value)]["name"]}
                    if selected
                    else None
                )
            return await _step(callback, state, job, "3" if data["option_kind"] == "squads" else "cascade")
        if action == "add":
            if value == "shape":
                if params["transport"].startswith("cdn_") or params["role"] == "exit":
                    raise ValueError("Шейпинг недоступен для этого транспорта или роли")
                shaping = params["shaping"]
                enabled = bool(shaping.get("enabled", bool(shaping)))
                job = await _save(job, owner, {"shaping": {} if enabled else {**shaping, "enabled": True}}, state)
                return await _step(callback, state, job, "4")
            if value not in ADDONS:
                raise ValueError("Неизвестное дополнение")
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
            for step in BASIC_STEPS:
                if await _missing_input(callback, state, job, step):
                    return
            InstallSpec.from_dict(params)
            builder = InlineKeyboardBuilder()
            _button(builder, "Установить ноду", "start", job_id)
            _button(builder, "← Изменить параметры", "step", job_id, "4")
            _button(builder, "Сквад и хост", "step", job_id, "3")
            await _clear_input(state)
            return await _send(callback, _summary(job), builder, state=state)
        if action == "start":
            if editing:
                raise ValueError("Для применения настроек сначала нажмите «Показать изменения»")
            job = await jobs.start_job(job_id, owner, message_id=callback.message.message_id)
            return await _job_screen(callback, state, job)
        if action == "retry":
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
            await _reset_input(
                state,
                job_id=job_id,
                delete_job=job_id,
                delete_token=result["token"],
                delete_has_panel_changes=has_panel_changes,
            )
            builder = InlineKeyboardBuilder()
            lines = [
                "<b>Удаление установки</b>",
                f"Нода: {_display(stored_params['name'])}",
                f"Сервер: {_display(stored_params['address'])}",
                "",
            ]
            if has_panel_changes:
                lines.extend([
                    "В Remnawave могли остаться объекты этой установки. Выберите, что удалить:",
                    "• Только из бота — запись и сохранённые доступы. Объекты панели останутся.",
                    "• Из бота и панели — также собственные хост, ноду, профиль и технический доступ каскада; инбаунд установки будет убран из сквада. Общие сквады сохранятся.",
                ])
                _button(builder, "Только из бота", "delete", job_id, "record")
                _button(builder, "Из бота и панели", "delete", job_id, "panel")
            else:
                lines.append(
                    "В панели нет сохранённых изменений этой установки. Будут удалены запись и сохранённые доступы."
                )
                _button(builder, "Удалить запись", "delete", job_id, "record")
            cloud = (job.get("resources") or {}).get("cdn")
            if cloud or job.get("retained_resources"):
                lines.append("CDN остаётся в облаке и может оплачиваться. Сохраните его данные перед удалением записи.")
                _button(builder, "Посмотреть ресурсы CDN", "res", job_id)
            lines.append("Файлы и контейнеры на VPS сохранятся. Восстановить удалённую запись из этого меню нельзя.")
            _button(builder, "← Не удалять", "show", job_id)
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
                "Остановить удаление? Уже выполненные изменения в панели сохранятся. Запись останется в боте; удаление можно будет повторить."
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
                or value not in {"record", "panel"}
                or (value == "panel" and not context.get("delete_has_panel_changes"))
            ):
                raise ValueError("Подтверждение устарело. Нажмите «Удалить запись» в карточке заново")
            result = await jobs.delete_job(
                job_id,
                owner,
                remove_panel=value == "panel",
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
            deleting=action in {"delask", "delete"},
        )
    except Exception as error:
        _log_unexpected(error, action, job_id)
        await _report_error(
            callback,
            state,
            "Не удалось выполнить действие. Обновите список установок и повторите попытку.",
            job_id,
            deleting=action in {"delask", "delete"},
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
        if job.get("deletion"):
            raise ValueError("Для этой записи уже выбрано удаление. Откройте карточку установки")
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
            await jobs.update_draft(job_id, message.from_user.id, secrets=secrets)
            await state.update_data(preview_token=None)
            job = await jobs.get_job(job_id, message.from_user.id)
            context = await state.get_data()
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
                if code in {"vk", "ya"}
                else "4"
                if code == "pc"
                else "1",
            )
        if code not in FIELDS and code not in SHAPING_FIELDS:
            raise ValueError("Запрос устарел")
        if not message.text:
            raise ValueError("Отправьте значение текстом")
        value = _parse_value(code, message.text)
        if code in SHAPING_FIELDS:
            shaping = dict(data.get("shaping") or _params(job)["shaping"])
            shaping[SHAPING_FIELDS[code][0]] = value
            shaping.setdefault("enabled", True)
            if data.get("manage"):
                await state.update_data(shaping=shaping)
            else:
                job = await _save(job, message.from_user.id, {"shaping": shaping}, state)
            return await _shaping(message, state, job, manage=bool(data.get("manage")))
        values = {"cloud": {**_params(job)["cloud"], "folder_id": value}} if code == "fd" else {FIELDS[code][0]: value}
        job = await _save(job, message.from_user.id, values, state)
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

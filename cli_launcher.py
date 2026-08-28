import json
import locale
import os
import re
import secrets
import select
import shutil
import subprocess
import sys
import time as time_mod

from contextlib import contextmanager
from datetime import datetime
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def _ensure_cli_deps() -> None:
    """Бутстрап: ставит rich+requests системным pip, если их нет.

    CLI запускают одним файлом на голом сервере, где venv проекта ещё нет.
    Happy-path должен идти на настоящем rich, а не на заглушках. Если pip
    недоступен (нет сети / залочен) — молча уходим на минимальный фолбэк.
    """
    try:
        import requests  # noqa: F401
        import rich  # noqa: F401

        return
    except ImportError:
        pass

    for extra in ([], ["--break-system-packages"]):
        try:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "-q",
                    "--disable-pip-version-check",
                    *extra,
                    "rich",
                    "requests",
                ],
                check=True,
            )
            return
        except Exception:
            continue


def _bootstrap_rpc() -> None:
    """Бутстрап: тянет core/rpc с публичной ветки, если его ещё нет рядом с CLI."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import core.rpc  # noqa: F401

        if hasattr(core.rpc, "get_settings_builder_url"):
            return
    except Exception:
        pass

    core_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "core")
    os.makedirs(core_dir, exist_ok=True)
    base_url = "https://raw.githubusercontent.com/Vladless/Solo_bot/dev/core"
    got_so = False
    for name in ("__init__.py", "rpc.cpython-312-x86_64-linux-gnu.so"):
        try:
            with urlopen(Request(f"{base_url}/{name}", headers={"Cache-Control": "no-cache"}), timeout=20) as resp:
                data = resp.read()
            if data:
                with open(os.path.join(core_dir, name), "wb") as fh:
                    fh.write(data)
                if name.endswith(".so"):
                    got_so = True
        except Exception:
            continue

    if got_so:
        try:
            os.remove(os.path.join(core_dir, "rpc.py"))
        except OSError:
            pass
    sys.modules.pop("core.rpc", None)

    try:
        import core.rpc  # noqa: F401
    except Exception as e:
        print(f"Не удалось подготовить core/rpc: {e}")
        sys.exit(1)


def _reexec_into_target_python() -> None:
    if sys.platform != "linux" or os.environ.get("_SOLOBOT_REEXEC") == "1":
        return
    proj = os.path.abspath(os.path.dirname(__file__))
    venv_py = os.path.join(proj, "venv", "bin", "python")
    target = None
    if os.path.exists(venv_py):
        target = venv_py
    elif sys.version_info[:2] != (3, 12):
        target = shutil.which("python3.12")
    if not target:
        return
    try:
        if os.path.realpath(target) == os.path.realpath(sys.executable):
            return
    except Exception:
        return
    try:
        os.execve(
            target,
            [target, os.path.abspath(__file__), *sys.argv[1:]],
            dict(os.environ, _SOLOBOT_REEXEC="1"),
        )
    except Exception:
        pass


_reexec_into_target_python()
_ensure_cli_deps()
_bootstrap_rpc()

from core.rpc import (  # noqa: E402
    adopt_beta_files,
    cli_gate,
    extract_version,
    get_settings_builder_url,
    local_version,
    migrate_settings_layout,
    project_uses_new_layout,
    resolve_config_path,
    resolve_texts_path,
    settings_gate,
)


try:
    import requests
except ImportError:
    requests = None

try:
    from rich import box
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
    from rich.prompt import Confirm, Prompt
    from rich.rule import Rule
    from rich.table import Table
    from rich.theme import Theme

    _HAS_RICH = True
except ImportError:
    _HAS_RICH = False

    def _strip_markup(value):
        if not isinstance(value, str):
            return str(value)
        return re.sub(r"\[[^\]]+\]", "", value)

    class Group:
        def __init__(self, *items) -> None:
            self.items = items

        def __str__(self) -> str:
            return "\n".join(_strip_markup(item) for item in self.items)

    class Panel:
        def __init__(self, renderable, **kwargs) -> None:
            self.renderable = renderable

        def __str__(self) -> str:
            return _strip_markup(self.renderable)

    class Table:
        def __init__(self, title=None, **kwargs) -> None:
            self.title = title
            self.rows = []

        def add_column(self, *args, **kwargs):
            return None

        def add_row(self, *row):
            self.rows.append(row)

        def __str__(self) -> str:
            lines = []
            if self.title:
                lines.append(_strip_markup(self.title))
            lines.extend(" | ".join(_strip_markup(cell) for cell in row) for row in self.rows)
            return "\n".join(lines)

    class Live:
        def __init__(self, **kwargs) -> None:
            self.last_renderable = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def update(self, renderable):
            self.last_renderable = renderable
            print(_strip_markup(str(renderable)))

    class SpinnerColumn:
        def __init__(self, *args, **kwargs) -> None:
            pass

    class BarColumn:
        def __init__(self, *args, **kwargs) -> None:
            pass

    class TextColumn:
        def __init__(self, *args, **kwargs) -> None:
            pass

    class box:
        ROUNDED = SIMPLE = MINIMAL = HEAVY = SQUARE = HORIZONTALS = None

    class Theme:
        def __init__(self, *args, **kwargs) -> None:
            pass

    class Rule:
        def __init__(self, title="", **kwargs) -> None:
            self.title = title

        def __str__(self) -> str:
            return _strip_markup(self.title)

    class Progress:
        def __init__(self, *args, **kwargs) -> None:
            self.last_description = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def add_task(self, description, total=None):
            self.last_description = description
            print(_strip_markup(description))
            return 1

        def update(self, task_id, description=None):
            if description and description != self.last_description:
                self.last_description = description
                print(_strip_markup(description))

    class Prompt:
        @staticmethod
        def ask(message, choices=None, default=None, show_choices=True, **kwargs):
            suffix = ""
            if choices and show_choices:
                suffix = f" ({'/'.join(choices)})"
            if default is not None:
                suffix = f"{suffix} [{default}]"
            value = input(f"{_strip_markup(message)}{suffix}: ").strip()
            if not value and default is not None:
                value = str(default)
            if choices and value not in choices:
                raise ValueError(f"Ожидается одно из значений: {', '.join(choices)}")
            return value

    class Confirm:
        @staticmethod
        def ask(message, default=False, **kwargs):
            prompt = "Y/n" if default else "y/N"
            value = input(f"{_strip_markup(message)} [{prompt}]: ").strip().lower()
            if not value:
                return default
            return value in {"y", "yes", "1", "true"}

    class Console:
        def print(self, *args, **kwargs):
            print(*(_strip_markup(str(arg)) for arg in args))

        def log(self, *args, **kwargs):
            self.print(*args)

        @contextmanager
        def status(self, message, **kwargs):
            self.print(message)
            yield


def ensure_utf8_locale():
    try:
        current_locale = locale.getlocale()
        if current_locale and current_locale[1] == "UTF-8":
            return
    except Exception:
        pass

    step_warn("Проверка и установка локали UTF-8...")

    os.environ["LC_ALL"] = "en_US.UTF-8"
    os.environ["LANG"] = "en_US.UTF-8"

    result = subprocess.run(["locale", "-a"], capture_output=True, text=True)
    if "en_US.utf8" not in result.stdout.lower():
        console.print("[title]Добавляю локаль en_US.UTF-8 в систему...[/title]")
        try:
            subprocess.run(["sudo", "locale-gen", "en_US.UTF-8"], check=True)
            subprocess.run(["sudo", "update-locale", "LANG=en_US.UTF-8"], check=True)
            step_ok("Локаль успешно установлена.")
        except Exception as e:
            step_fail(f"Ошибка при установке локали: {e}")
    else:
        step_ok("Локаль UTF-8 уже доступна в системе.")


try:
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

if _HAS_RICH:
    SOLO_THEME = Theme({
        "brand": "bold #ff8c42",
        "accent": "#ff8c42",
        "accent.dim": "#8a4a1f",
        "title": "bold #f2f5f9",
        "text": "#d7dde5",
        "muted": "#8b949e",
        "faint": "#666f7b",
        "line": "#333a44",
        "key": "bold #ff8c42",
        "ok": "#4ade80",
        "ok.bold": "bold #4ade80",
        "warn": "#fbbf24",
        "warn.bold": "bold #fbbf24",
        "err": "#f87171",
        "err.bold": "bold #f87171",
        "step": "bold #ff8c42",
    })
    console = Console(theme=SOLO_THEME, highlight=False)
else:
    console = Console()


_G_STEP = "›"
_G_OK = "✓"
_G_WARN = "!"
_G_FAIL = "✗"
_G_DOT = "•"
_G_PROMPT = "❯"
_PANEL_W = 76


def hr() -> None:
    console.print(Rule(style="line"), width=_PANEL_W)


def heading(title: str, subtitle: str = "") -> None:
    console.print()
    console.print(f"  [title]{title}[/title]" + (f"   [muted]{subtitle}[/muted]" if subtitle else ""))
    console.print(Rule(style="line"), width=_PANEL_W)


def step_rule(index: int, total: int, title: str) -> None:
    console.print()
    console.print(
        Rule(
            f"[step]{index}/{total}[/step] [faint]{_G_STEP}[/faint] [title]{title}[/title]",
            style="accent.dim",
            align="left",
        ),
        width=_PANEL_W,
    )


def step_ok(text: str) -> None:
    console.print(f"  [ok.bold]{_G_OK}[/ok.bold] [text]{text}[/text]")


def step_warn(text: str) -> None:
    console.print(f"  [warn.bold]{_G_WARN}[/warn.bold] [warn]{text}[/warn]")


def step_fail(text: str) -> None:
    console.print(f"  [err.bold]{_G_FAIL}[/err.bold] [err]{text}[/err]")


def step_info(text: str) -> None:
    console.print(f"  [faint]{_G_DOT}[/faint] [muted]{text}[/muted]")


def menu(title: str, groups: list, subtitle: str = "") -> None:
    """Меню одним блоком: [(заголовок группы, [(номер, значок, подпись, доступен, примечание)])]."""
    blocks = []
    notes = []
    for position, (group_title, items) in enumerate(groups):
        if position:
            blocks.append("")
        if group_title:
            blocks.append(f"[faint]{group_title}[/faint]")

        table = Table(box=None, show_header=False, padding=(0, 1), pad_edge=False, expand=False)
        table.add_column(justify="right", no_wrap=True, width=2)
        table.add_column(justify="center", no_wrap=True, width=1)
        table.add_column(overflow="fold")
        for number, glyph, label, enabled, note in items:
            if enabled:
                table.add_row(f"[key]{number}[/key]", f"[accent]{glyph}[/accent]", f"[text]{label}[/text]")
            else:
                table.add_row(f"[faint]{number}[/faint]", f"[faint]{glyph}[/faint]", f"[faint]{label}[/faint]")
                if note and note not in notes:
                    notes.append(note)
        blocks.append(table)

    for note in notes:
        blocks.append("")
        blocks.append(f"[faint]{note}[/faint]")

    console.print()
    console.print(
        Panel(
            Group(*blocks),
            title=f"[brand]{title}[/brand]",
            subtitle=f"[faint]{subtitle}[/faint]" if subtitle else None,
            subtitle_align="right",
            title_align="left",
            border_style="accent.dim",
            box=box.ROUNDED,
            padding=(1, 3),
            width=_PANEL_W,
        )
    )


def ask_choice(count: int, label: str = "Выберите пункт") -> str:
    return safe_prompt(
        f"[key]{_G_PROMPT}[/key] [title]{label}[/title]",
        choices=[str(i) for i in range(1, count + 1)],
        show_choices=False,
    )


ensure_utf8_locale()

BACK_DIR = os.path.expanduser("~/.solobot_backups")
TEMP_DIR = os.path.expanduser("~/.solobot_tmp")
PROJECT_DIR = os.path.abspath(os.path.dirname(__file__))
IS_ROOT_DIR = PROJECT_DIR == "/root"
GITHUB_REPO = "https://github.com/Vladless/Solo_bot"
CONFIG_BUILDER_URL = get_settings_builder_url()
WIKI_URL = "https://wikibot.solobot.ru"
GHCR_IMAGE = os.environ.get("GHCR_IMAGE", "vladless/solo-brick").strip() or "vladless/solo-brick"
DEFAULT_SERVICE_NAME = "bot.service"
VENV_PYTHON = os.path.join(PROJECT_DIR, "venv", "bin", "python")
SOLOBOT_CMD_PATH = "/usr/local/bin/solobot"
SETTINGS_DIR = os.path.join(PROJECT_DIR, "settings")
CLI_VERSION = "v1.2.0"


def _ensure_solobot_command() -> None:
    if sys.platform != "linux":
        return
    cli_name = os.path.basename(os.path.abspath(__file__))
    wrapper = (
        "#!/bin/bash\n"
        f'cd "{PROJECT_DIR}" || exit 1\n'
        f'if [ -x "{VENV_PYTHON}" ]; then\n'
        f'  PY="{VENV_PYTHON}"\n'
        "elif command -v python3.12 >/dev/null 2>&1; then\n"
        '  PY="$(command -v python3.12)"\n'
        "else\n"
        '  PY="python3"\n'
        "fi\n"
        f'exec "$PY" "{cli_name}" "$@"\n'
    )
    try:
        if os.path.isfile(SOLOBOT_CMD_PATH):
            with open(SOLOBOT_CMD_PATH, encoding="utf-8") as fh:
                if fh.read() == wrapper:
                    return
    except Exception:
        pass
    try:
        subprocess.run(
            ["sudo", "tee", SOLOBOT_CMD_PATH],
            input=wrapper,
            text=True,
            stdout=subprocess.DEVNULL,
            check=True,
        )
        subprocess.run(["sudo", "chmod", "+x", SOLOBOT_CMD_PATH], check=True)
        step_ok(f"Команда «solobot» установлена ({SOLOBOT_CMD_PATH})")
    except Exception:
        pass


class HttpResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text

    def json(self):
        return json.loads(self.text)


def http_get(url: str, *, params=None, timeout: int = 10) -> HttpResponse:
    if requests is not None:
        response = requests.get(url, params=params, timeout=timeout)
        return HttpResponse(response.status_code, response.text)

    final_url = url
    if params:
        final_url = f"{url}?{urlencode(params)}"
    request = Request(final_url, headers={"User-Agent": "SoloBot-CLI"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return HttpResponse(response.status, response.read().decode("utf-8"))
    except HTTPError as error:
        return HttpResponse(error.code, error.read().decode("utf-8", errors="replace"))
    except URLError:
        return HttpResponse(599, "")


def detect_service_name() -> str:
    config_path = resolve_config_path(PROJECT_DIR)
    if os.path.isfile(config_path):
        try:
            with open(config_path, encoding="utf-8") as config_file:
                config_text = config_file.read()
            match = re.search(r"BOT_SERVICE\s*=\s*['\"]([^'\"]+)['\"]", config_text)
            if match:
                return match.group(1)
        except Exception:
            pass
    return DEFAULT_SERVICE_NAME


def refresh_service_name() -> str:
    global SERVICE_NAME, SYSTEMD_SERVICE_PATH
    SERVICE_NAME = detect_service_name()
    SYSTEMD_SERVICE_PATH = os.path.join("/etc/systemd/system", SERVICE_NAME)
    return SERVICE_NAME


SERVICE_NAME = refresh_service_name()


def is_ascii_only(value: str) -> bool:
    """Проверка, что строка содержит только ASCII."""
    return all(ord(ch) < 128 for ch in value)


def _parse_tag_version(tag_name: str) -> tuple[int, ...]:
    """Извлекает кортеж (major, minor, patch, ...) из тега для сортировки. v.5.1 -> (5, 1), v4 -> (4, 0)."""
    s = tag_name.strip().lstrip("v.")
    parts = []
    for part in re.split(r"[.\s]+", s):
        try:
            parts.append(int(part))
        except ValueError:
            break
    return tuple(parts) if parts else (0,)


def warn_english_only():
    """Предупреждение о необходимости английской раскладки."""
    step_fail("Обнаружен ввод с неанглийской раскладкой.")
    step_warn("Пожалуйста, переключите раскладку на ENG и введите снова.")


_CONFIRM_YES = {"y", "yes", "1", "true", "д", "да", "у"}
_CONFIRM_NO = {"n", "no", "0", "false", "н", "нет"}


_AUTO_YES = False
_AUTO_TAG = ""
_AUTO_OVERWRITE: dict[str, bool] = {}
_AUTO_ABORT_REASON = ""

_AUTO_FILE_MARKERS = (("buttons", "buttons.py"), ("img", "папку img"), ("redis_cache", "redis_cache.py"))
_AUTO_STOP_MARKERS = (
    ("БЕЗ бэкапа", "резервная копия не создана"),
    ("Всё равно продолжить обновление", "config и texts не содержат переменных новой версии"),
)


def _auto_answer(message: str) -> bool | None:
    """Ответ за админа в неинтерактивном режиме. None — обычный интерактивный запуск.

    Вопросы «обновлять ли buttons.py / img / redis_cache.py» отвечаются по флагам:
    без флага файл не перезаписывается. Вопросы, где «да» означает обновление
    вслепую (без бэкапа, с неполным конфигом), отвечаются «нет» — без админа
    у экрана такой риск брать нельзя. Остальные подтверждения — «да».
    """
    global _AUTO_ABORT_REASON

    if not _AUTO_YES:
        return None
    for key, marker in _AUTO_FILE_MARKERS:
        if marker in message:
            return bool(_AUTO_OVERWRITE.get(key, False))
    for marker, reason in _AUTO_STOP_MARKERS:
        if marker in message:
            _AUTO_ABORT_REASON = reason
            return False
    return True


def safe_confirm(message: str, default: bool = False, **kwargs) -> bool:
    """Подтверждение y/n, устойчивое к раскладке.

    Срезает не-ASCII «мусор» от переключения раскладки и принимает y/n в любой
    раскладке (y/да/д/у → да, n/нет/н → нет). Пустой ввод → значение по умолчанию.
    """
    auto = _auto_answer(message)
    if auto is not None:
        console.print(f"[faint]{message} → {'да' if auto else 'нет'}[/faint]")
        return auto

    suffix = "[faint](Y/n)[/faint]" if default else "[faint](y/n)[/faint]"
    while True:
        try:
            raw = Prompt.ask(f"[key]{_G_PROMPT}[/key] [text]{message}[/text] {suffix}", **kwargs)
        except UnicodeDecodeError:
            warn_english_only()
            continue
        text = str(raw if raw is not None else "").strip()
        if not text:
            return default
        ascii_only = "".join(ch for ch in text if ord(ch) < 128).strip().lower()
        candidate = ascii_only or text.lower()
        if candidate in _CONFIRM_YES or candidate[:1] in ("y",):
            return True
        if candidate in _CONFIRM_NO or candidate[:1] in ("n",):
            return False
        step_warn("Введите y (да) или n (нет).")


def safe_prompt(message: str, **kwargs) -> str:
    """Безопасный Prompt.ask с защитой от русской раскладки.

    Не-ASCII символы тихо фильтруются. Предупреждение появляется только
    если после фильтрации в строке не осталось значимого ASCII (т.е. ввод
    был полностью на не-английской раскладке).
    """
    while True:
        try:
            value = Prompt.ask(message, **kwargs)
        except UnicodeDecodeError:
            warn_english_only()
            continue
        except ValueError as e:
            step_fail(f"{e}")
            continue
        if isinstance(value, str) and not is_ascii_only(value):
            cleaned = "".join(ch for ch in value if ord(ch) < 128)
            if not cleaned.strip():
                warn_english_only()
                continue
            return cleaned
        return value


if IS_ROOT_DIR:
    _required_paths = ("requirements.txt", "main.py")
    _has_project = all(os.path.exists(os.path.join(PROJECT_DIR, p)) for p in _required_paths)
    _has_config = os.path.exists(resolve_config_path(PROJECT_DIR))

    if _has_project or _has_config:
        step_fail("КРИТИЧЕСКАЯ ОШИБКА:")
        step_fail("Обнаружена установка бота прямо в корневой папке (/root).")
        step_fail("Это крайне опасно и может привести к потере данных!")
        step_fail("Рекомендуется перенести бота в отдельную папку, например /root/Solo_bot")
        step_fail("Обновление заблокировано в целях безопасности.")
        sys.exit(1)

    _target_dir = "/root/Solo_bot"
    os.makedirs(_target_dir, exist_ok=True)
    _target_path = os.path.join(_target_dir, os.path.basename(__file__))
    try:
        shutil.move(__file__, _target_path)
    except Exception as e:
        step_fail(f"Не удалось перенести launcher в {_target_dir}: {e}")
        sys.exit(1)
    os.chdir(_target_dir)
    step_ok(f"✓ Launcher перенесён в {_target_dir}")
    console.print("[faint]Перезапуск из новой папки...[/faint]")
    os.execv(sys.executable, [sys.executable, _target_path, *sys.argv[1:]])


def run_with_status(
    cmd,
    *,
    status_text: str,
    cwd: str | None = None,
    check: bool = False,
    env: dict | None = None,
) -> subprocess.CompletedProcess:
    with console.status(f"[accent]{status_text}[/accent]", spinner="dots"):
        result = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        step_fail(status_text)
        if result.stdout:
            console.print(result.stdout)
        if result.stderr:
            console.print(f"[err]{result.stderr.rstrip()}[/err]")
        if check:
            raise subprocess.CalledProcessError(result.returncode, cmd, result.stdout, result.stderr)
    else:
        step_ok(status_text)
    return result


def is_service_exists(service_name):
    result = subprocess.run(["systemctl", "list-unit-files", service_name], capture_output=True, text=True)
    return service_name in result.stdout


def get_runtime_user() -> str:
    return os.environ.get("SUDO_USER") or subprocess.check_output(["whoami"], text=True).strip()


def has_project_code() -> bool:
    required_paths = ("requirements.txt", "main.py")
    return all(os.path.exists(os.path.join(PROJECT_DIR, path)) for path in required_paths)


def has_local_config() -> bool:
    return os.path.exists(resolve_config_path(PROJECT_DIR))


def bootstrap_project_files(branch: str = "main") -> bool:
    refresh_service_name()
    if has_project_code():
        return True

    step_warn("Полный проект рядом не найден. Подтягиваю файлы бота...")
    install_core_packages_if_needed()
    install_rsync_if_needed()

    subprocess.run(["rm", "-rf", TEMP_DIR], check=False)
    clone_result = run_with_status(
        ["git", "clone", "--depth", "1", "--branch", branch, GITHUB_REPO, TEMP_DIR],
        status_text=f"Клонирование {GITHUB_REPO} (ветка {branch})",
    )
    if clone_result.returncode != 0:
        step_fail("Не удалось скачать проект из GitHub.")
        return False

    rsync_cmd = ["rsync", "-a", f"{TEMP_DIR}/", f"{PROJECT_DIR}/"]
    if has_local_config():
        rsync_cmd.insert(2, "--exclude=config.py")
        rsync_cmd.insert(2, "--exclude=settings/config.py")
    if os.path.exists(os.path.join(PROJECT_DIR, "handlers", "texts.py")):
        rsync_cmd.insert(2, "--exclude=handlers/texts.py")
    if os.path.exists(os.path.join(SETTINGS_DIR, "texts.py")):
        rsync_cmd.insert(2, "--exclude=settings/texts.py")
    if os.path.exists(os.path.join(PROJECT_DIR, "handlers", "buttons.py")) or os.path.exists(
        os.path.join(SETTINGS_DIR, "buttons.py")
    ):
        rsync_cmd.insert(2, "--exclude=settings/buttons.py")
    if os.path.exists(os.path.join(PROJECT_DIR, "core", "redis_cache.py")):
        rsync_cmd.insert(2, "--exclude=core/redis_cache.py")
    if os.path.exists(os.path.join(PROJECT_DIR, "img")):
        rsync_cmd.insert(2, "--exclude=img")
    if os.path.exists(os.path.join(PROJECT_DIR, "modules")):
        rsync_cmd.insert(2, "--exclude=modules")
    rsync_cmd.insert(2, "--exclude=.git")

    sync_result = run_with_status(rsync_cmd, status_text="Распаковка файлов проекта")
    subprocess.run(["rm", "-rf", TEMP_DIR], check=False)
    if sync_result.returncode != 0:
        step_fail("Не удалось распаковать файлы проекта.")
        return False

    migrate_settings_layout(PROJECT_DIR, out=console.print)
    refresh_service_name()
    step_ok("Файлы проекта подготовлены.")
    return True


def install_core_packages_if_needed():
    missing_packages = []

    if shutil.which("git") is None:
        missing_packages.append("git")
    if shutil.which("rsync") is None:
        missing_packages.append("rsync")
    if shutil.which("curl") is None:
        missing_packages.append("curl")

    python312_path = shutil.which("python3.12")
    if python312_path is None:
        missing_packages.extend(["python3.12", "python3.12-venv"])
    else:
        venv_check = subprocess.run(
            [python312_path, "-m", "venv", "--help"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if venv_check.returncode != 0:
            missing_packages.append("python3.12-venv")

    if not missing_packages:
        return

    unique_packages = list(dict.fromkeys(missing_packages))
    step_warn(f"Устанавливаю системные пакеты: {', '.join(unique_packages)}")
    run_with_status(["sudo", "apt", "update"], status_text="apt update", check=True)
    run_with_status(
        ["sudo", "apt", "install", "-y", *unique_packages],
        status_text=f"Установка: {', '.join(unique_packages)}",
        check=True,
    )


def build_systemd_service() -> str:
    run_user = get_runtime_user()
    return (
        "[Unit]\n"
        "Description=SoloBot Telegram bot\n"
        "After=network.target\n\n"
        "[Service]\n"
        f"User={run_user}\n"
        f"WorkingDirectory={PROJECT_DIR}\n"
        f"ExecStart={VENV_PYTHON} {os.path.join(PROJECT_DIR, 'main.py')}\n"
        "Restart=always\n"
        "RestartSec=10\n"
        "TimeoutStopSec=10\n"
        "KillMode=control-group\n"
        'Environment="PYTHONUNBUFFERED=1"\n'
        "LimitNOFILE=65536\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def ensure_systemd_service() -> bool:
    refresh_service_name()
    step_warn(f"Проверяю systemd-службу {SERVICE_NAME}...")
    service_text = build_systemd_service()
    service_exists = os.path.exists(SYSTEMD_SERVICE_PATH)

    if service_exists:
        try:
            with open(SYSTEMD_SERVICE_PATH, encoding="utf-8") as service_file:
                if service_file.read() == service_text:
                    step_ok(f"Служба {SERVICE_NAME} уже настроена.")
                    return True
        except Exception:
            pass

    try:
        subprocess.run(
            ["sudo", "tee", SYSTEMD_SERVICE_PATH],
            input=service_text,
            text=True,
            stdout=subprocess.DEVNULL,
            check=True,
        )
        subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
        step_ok(f"Служба {SERVICE_NAME} настроена.")
        return True
    except Exception as e:
        step_fail(f"Не удалось настроить службу {SERVICE_NAME}: {e}")
        return False


def initialize_database() -> bool:
    if not os.path.exists(VENV_PYTHON):
        step_warn("Инициализация базы пропущена: виртуальное окружение ещё не создано.")
        return False
    step_warn("Инициализация базы данных...")
    try:
        subprocess.run(
            [
                VENV_PYTHON,
                "-c",
                "import asyncio; from database.setup.init_db import init_db; asyncio.run(init_db())",
            ],
            cwd=PROJECT_DIR,
            check=True,
        )
        step_ok("База данных успешно инициализирована.")
        return True
    except Exception as e:
        step_fail(f"Не удалось инициализировать базу данных: {e}")
        return False


def enable_and_start_service(start_now: bool = True) -> None:
    refresh_service_name()
    subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
    subprocess.run(["sudo", "systemctl", "enable", SERVICE_NAME], check=True)
    if start_now:
        subprocess.run(["sudo", "systemctl", "restart", SERVICE_NAME], check=True)
        step_ok(f"Служба {SERVICE_NAME} включена и запущена.")
    else:
        console.print(
            f"[warn]Служба {SERVICE_NAME} включена, но не запущена. Проверьте config.py и доступность базы данных.[/warn]"
        )


def is_runtime_ready() -> bool:
    refresh_service_name()
    if not has_project_code():
        return False
    return os.path.exists(VENV_PYTHON) and is_service_exists(SERVICE_NAME)


def _read_config_str(key: str) -> str:
    try:
        text = open(resolve_config_path(PROJECT_DIR), encoding="utf-8").read()
    except Exception:
        return ""
    m = re.search(rf'^{key}\s*=\s*[\'"]([^\'"]*)[\'"]', text, re.M)
    return m.group(1) if m else ""


def _write_config_value(key: str, value: str) -> bool:
    path = resolve_config_path(PROJECT_DIR)
    try:
        text = open(path, encoding="utf-8").read()
    except Exception as e:
        step_warn(f"Не удалось открыть config.py: {e}")
        return False
    line = f'{key} = "{value}"'
    text, n = re.subn(rf"(?m)^{key}\s*=.*$", line, text)
    if n == 0:
        text = text.rstrip() + f"\n{line}\n"
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return True
    except Exception as e:
        step_warn(f"Не удалось записать config.py: {e}")
        return False


def _prompt_domain() -> str:
    cur = _read_config_str("WEBHOOK_HOST")
    console.print(
        "[faint]Домен, на котором работает бот (A-запись домена должна указывать на IP этого сервера). "
        "Обязателен: по нему Telegram доставляет сообщения и клиенты получают ссылки подписок.[/faint]"
    )
    raw = (safe_prompt("[accent]Домен бота[/accent] (например vpn.example.com)", default=cur) or "").strip()
    raw = raw.replace("https://", "").replace("http://", "").strip("/ ")
    if not raw:
        return ""
    host = f"https://{raw}"
    if _write_config_value("WEBHOOK_HOST", host):
        step_ok(f"Домен сохранён в config.py: {host}")
    return host


def _read_config_db_creds() -> dict:
    creds = {"user": "myuser", "password": "", "name": "solobot"}
    try:
        text = open(resolve_config_path(PROJECT_DIR), encoding="utf-8").read()
    except Exception:
        return creds
    for key, field in (("DB_USER", "user"), ("DB_PASSWORD", "password"), ("DB_NAME", "name")):
        m = re.search(rf'^{key}\s*=\s*[\'"]([^\'"]*)[\'"]', text, re.M)
        if m:
            creds[field] = m.group(1)
    return creds


def _write_config_db_creds(creds: dict) -> bool:
    path = resolve_config_path(PROJECT_DIR)
    try:
        text = open(path, encoding="utf-8").read()
    except Exception as e:
        step_warn(f"Не удалось открыть config.py: {e}")
        return False
    for key, field in (("DB_NAME", "name"), ("DB_USER", "user"), ("DB_PASSWORD", "password")):
        line = f'{key} = "{creds[field]}"'
        text, n = re.subn(rf"(?m)^{key}\s*=.*$", line, text)
        if n == 0:
            text = text.rstrip() + f"\n{line}\n"
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return True
    except Exception as e:
        step_warn(f"Не удалось записать config.py: {e}")
        return False


def _prompt_db_creds() -> dict:
    cur = _read_config_db_creds()
    if not cur["password"]:
        cur["password"] = secrets.token_urlsafe(18)
        console.print("[faint]Пароль БД сгенерирован автоматически — оставьте его (Enter) или задайте свой.[/faint]")
    console.print("[faint]Доступ к базе данных. Нажмите Enter, чтобы оставить предложенное значение.[/faint]")
    name = (safe_prompt("[accent]Имя базы данных[/accent]", default=cur["name"]) or cur["name"]).strip()
    user = (safe_prompt("[accent]Пользователь БД[/accent]", default=cur["user"]) or cur["user"]).strip()
    password = (safe_prompt("[accent]Пароль БД[/accent]", default=cur["password"]) or cur["password"]).strip()
    creds = {"name": name, "user": user, "password": password}
    if _write_config_db_creds(creds):
        step_ok("Доступ к БД сохранён в config.py.")
    return creds


def _ensure_data_services(creds: dict) -> bool:
    compose_file = os.path.join(PROJECT_DIR, "docker-compose.local.yml")
    if not os.path.exists(compose_file):
        step_warn("Файл docker-compose.local.yml не найден — данные не поднять автоматически.")
        return False
    if not _ensure_docker():
        return False
    for port, svc in ((5432, "PostgreSQL"), (6379, "Redis")):
        owner = _port_owner(port)
        if owner and "docker" not in owner.lower():
            console.print(
                f"[warn]Порт {port} уже занят процессом «{owner}» (не Docker) — это помешает поднять {svc}.[/warn]"
            )
            console.print(
                f"[faint]Обычно это системный {svc}. Остановите его (например: sudo systemctl stop postgresql) "
                f"или освободите порт {port}, затем повторите.[/faint]"
            )
            if not safe_confirm("Попробовать поднять контейнеры всё равно?", default=False):
                return False
    env = {
        **os.environ,
        "POSTGRES_USER": creds["user"],
        "POSTGRES_PASSWORD": creds["password"],
        "POSTGRES_DB": creds["name"],
    }
    base = ["docker", "compose", "-f", compose_file, "up", "-d"]
    with console.status("[warn.bold]Запуск PostgreSQL и Redis…[/warn.bold]"):
        res = subprocess.run(base + ["--wait"], capture_output=True, text=True, env=env)
        if res.returncode != 0:
            res = subprocess.run(base, capture_output=True, text=True, env=env)
    if res.returncode != 0:
        console.print(f"[err]{(res.stderr or '').strip()[:500]}[/err]")
        return False
    for _ in range(30):
        chk = subprocess.run(
            ["docker", "exec", "solobot-postgres", "pg_isready", "-U", creds["user"], "-d", creds["name"]],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if chk.returncode == 0:
            return True
        sleep(2)
    step_warn("PostgreSQL запущен, но не ответил готовностью за отведённое время.")
    return False


def install_bot():
    console.print(
        Panel(
            "[text]CLI подготовит окружение, поднимет базу данных и Redis, установит зависимости, "
            "создаст systemd-службу и инициализирует базу. Если проекта ещё нет рядом, CLI сначала скачает его автоматически.[/text]\n\n"
            "[warn]Понадобятся два файла с сайта:[/warn] [bold]config.py[/bold] и [bold]texts.py[/bold] "
            "(токен, доступ к БД, тексты). Если их ещё нет — CLI остановится и подскажет, куда их положить.\n\n"
            "[err.bold]Важно:[/err.bold] боту обязательно нужен [bold]домен с HTTPS[/bold]. Без него Telegram не доставляет "
            "сообщения и не работают ссылки подписок. Домен указывается на сайте при генерации config.py.\n"
            "[text]HTTPS CLI может настроить сам — предложит выбор: Caddy (сертификат сам), "
            "Nginx + certbot, или пропустить, если прокси уже есть.[/text]",
            border_style="ok",
            width=_PANEL_W,
            title="[ok.bold]Автоматическая установка SoloBot[/ok.bold]",
            padding=(1, 2),
        )
    )

    if not safe_confirm("Запустить автоматическую установку?", default=True):
        return

    total = 9
    try:
        step_rule(1, total, "Файлы проекта")
        console.print(
            "[faint]Проверяю исходники бота рядом с лаунчером. Если их нет — скачаю стабильную версию с GitHub.[/faint]"
        )
        if not bootstrap_project_files(branch="main"):
            step_fail("Не удалось подготовить файлы проекта. Установка прервана.")
            return
        refresh_service_name()
        step_ok("Файлы проекта на месте.")

        step_rule(2, total, "Конфигурация")
        console.print(
            "[faint]config.py и texts.py вы получаете на сайте (там задаются токен бота, доступ к базе данных, тексты). "
            "В исходниках их нет, поэтому без этих двух файлов установка не продолжится.[/faint]"
        )
        migrate_settings_layout(PROJECT_DIR, out=console.print)

        installed_ver = local_version(PROJECT_DIR) or "неизвестна"
        layout_hint = (
            "settings/ (версии выше 5.1.2 и бета)"
            if project_uses_new_layout(PROJECT_DIR)
            else "корень проекта и handlers/ (версии до 5.1.2 включительно)"
        )
        console.print(f"[accent]Версия бота: {installed_ver}[/accent] [faint]→ файлы настроек: {layout_hint}[/faint]")

        def _cfg_paths():
            return resolve_config_path(PROJECT_DIR), resolve_texts_path(PROJECT_DIR)

        def _missing_cfg():
            moved = migrate_settings_layout(PROJECT_DIR, out=console.print)
            if moved:
                step_ok("Нашёл файлы в местах от другой версии и перенёс их куда нужно.")
            cp, tp = _cfg_paths()
            miss = []
            if not os.path.exists(cp):
                miss.append(os.path.relpath(cp, PROJECT_DIR))
            if not os.path.exists(tp):
                miss.append(os.path.relpath(tp, PROJECT_DIR))
            return miss

        while True:
            missing = _missing_cfg()
            if not missing:
                break
            config_path, texts_path = _cfg_paths()
            step_warn("Пока нет файлов: " + ", ".join(missing))
            console.print(
                Panel(
                    "[text]Положите рядом с ботом два файла, которые вы скачиваете на сайте:[/text]\n\n"
                    f"  • [accent]config.py[/accent] кладётся сюда:\n    [bold]{config_path}[/bold]\n"
                    f"  • [accent]texts.py[/accent] кладётся сюда:\n    [bold]{texts_path}[/bold]\n\n"
                    f"[text]Где взять файлы:[/text] [bold]{CONFIG_BUILDER_URL}[/bold]\n"
                    "[warn]Это шаблоны с пустыми значениями — заполните config.py "
                    f"(токен бота и др.) по инструкции на вики:[/warn] [bold]{WIKI_URL}[/bold]\n\n"
                    "[text]Как загрузить (команды с вашего компьютера):[/text]\n"
                    f"  • [bold]scp config.py root@ВАШ_IP:{config_path}[/bold]\n"
                    f"  • [bold]scp texts.py root@ВАШ_IP:{texts_path}[/bold]\n"
                    "  • положили не туда (места от другой версии)? — не страшно, CLI сам перенесёт куда нужно.\n"
                    "  • либо перетащите файлы в FileZilla/SFTP по этим путям.\n\n"
                    "[faint]В этих файлах ваши токены и пароли — никому не пересылайте их.[/faint]",
                    border_style="warn",
                    width=_PANEL_W,
                    title="[warn.bold]Нужны config.py и texts.py[/warn.bold]",
                    padding=(1, 2),
                )
            )
            if not safe_confirm("Загрузили файлы? Проверить снова?", default=True):
                step_warn("Установка приостановлена. Запустите снова, когда загрузите файлы: sudo solobot")
                return
        config_path, texts_path = _cfg_paths()
        step_ok(f"config.py и texts.py на месте: {os.path.relpath(config_path, PROJECT_DIR)}, {os.path.relpath(texts_path, PROJECT_DIR)}")

        console.print(
            f"[warn]Напоминание:[/warn] config.py — это шаблон с пустыми значениями. "
            f"Заполните его по инструкции на вики ([bold]{WIKI_URL}[/bold]) — обязателен токен бота, иначе бот не запустится."
        )
        domain = _prompt_domain()
        if not domain:
            console.print(
                Panel(
                    "[text]Боту обязательно нужен домен с HTTPS:[/text] по нему Telegram доставляет сообщения, "
                    "и по этому же адресу клиенты получают ссылки подписок.\n\n"
                    "[text]Что нужно:[/text]\n"
                    "  1. Купите домен и направьте его A-записью на IP этого сервера.\n"
                    "  2. Запустите установку снова и введите домен (или впишите в config.py "
                    'WEBHOOK_HOST = "https://ваш-домен").\n\n'
                    "[faint]Без домена бот не будет отвечать в Telegram.[/faint]",
                    border_style="err",
                    width=_PANEL_W,
                    title="[err.bold]Домен не указан[/err.bold]",
                    padding=(1, 2),
                )
            )
            if not safe_confirm("Продолжить установку без домена?", default=False):
                step_warn("Установка остановлена. Подготовьте домен и запустите снова: sudo solobot")
                return

        step_rule(3, total, "HTTPS для бота")
        console.print(
            "[faint]Telegram доставляет сообщения только на HTTPS. CLI может сам поставить реверс-прокси "
            "и выпустить сертификат — или пропустите шаг, если прокси уже настроен.[/faint]"
        )
        setup_bot_https(domain)

        step_rule(4, total, "Системные пакеты")
        console.print("[faint]git, rsync, Python 3.12 и модуль venv — это база, без которой бот не запустится.[/faint]")
        install_core_packages_if_needed()
        step_ok("Системные пакеты готовы.")

        step_rule(5, total, "Python-окружение")
        console.print("[faint]Создаю виртуальное окружение venv/ и ставлю зависимости из requirements.txt.[/faint]")
        install_dependencies()
        if not os.path.exists(VENV_PYTHON):
            step_fail("Виртуальное окружение не создано. Установка прервана.")
            return
        step_ok("Зависимости установлены.")

        step_rule(6, total, "Данные (PostgreSQL и Redis)")
        console.print(
            "[faint]Зададим доступ к базе данных (Enter — значения по умолчанию). Эти значения пропишутся "
            "в config.py и в контейнер PostgreSQL, чтобы бот и база точно совпали. Затем подниму PostgreSQL и Redis.[/faint]"
        )
        db_creds = _prompt_db_creds()
        if _ensure_data_services(db_creds):
            step_ok("PostgreSQL и Redis запущены.")
        else:
            step_warn(
                "Не удалось поднять данные автоматически. Подними вручную: "
                "docker compose -f docker-compose.local.yml up -d"
            )

        step_rule(7, total, "База данных")
        console.print(
            "[faint]Создаю таблицы по доступам из config.py. "
            "Если данные базы в config.py неверные — шаг можно завершить позже, перезапустив бота из меню.[/faint]"
        )
        db_ready = initialize_database()
        if db_ready:
            step_ok("База данных инициализирована.")
        else:
            step_warn("База не готова: бот не смог подключиться по доступам из config.py.")
            console.print(
                "[faint]Если база уже создавалась раньше с другими логином/паролем, контейнер хранит старые доступы. "
                "Пересоздать БД (СОТРЁТ данные): docker compose -f docker-compose.local.yml down -v, затем переустановите.[/faint]"
            )

        step_rule(8, total, "Служба автозапуска")
        console.print(
            "[faint]Создаю systemd-службу, чтобы бот стартовал сам и поднимался после перезагрузки сервера.[/faint]"
        )
        if not ensure_systemd_service():
            step_fail("Не удалось настроить службу. Установка прервана.")
            return
        step_ok(f"Служба {SERVICE_NAME} настроена.")

        step_rule(9, total, "Права и запуск")
        console.print(
            "[faint]Назначаю владельца и права на файлы проекта, закрываю секреты (config.py, тексты) и запускаю бота.[/faint]"
        )
        fix_permissions()
        enable_and_start_service(start_now=db_ready)
        step_ok("Права назначены, служба включена.")

        console.print()
        if db_ready:
            step_ok("Установка SoloBot завершена. Бот запущен.")
            console.print(
                "[faint]Проверка: в меню пункт 6 (статус) и 5 (логи). Откройте бота в Telegram и нажмите /start. "
                "Если бот не отвечает — проверьте токен в config.py, затем перезапустите (пункт 3).[/faint]"
            )
        else:
            console.print(
                "[warn.bold]Установка почти готова.[/warn.bold] "
                "[warn]Осталась база: проверьте доступ к БД в config.py и перезапустите бота (пункт 3 в меню).[/warn]"
            )
    except subprocess.CalledProcessError as e:
        step_fail(f"Ошибка во время установки: {e}")
    except KeyboardInterrupt:
        console.print("\n[warn]Установка прервана пользователем.[/warn]")


def prompt_install_if_needed():
    if is_runtime_ready():
        return

    refresh_service_name()
    has_project = has_project_code()
    has_venv = has_project and os.path.exists(VENV_PYTHON)
    has_service = has_project and is_service_exists(SERVICE_NAME)

    if not has_project:
        console.print(
            Panel(
                "[text]В этой папке ещё нет установки.[/text]\n\n"
                "[bold]SoloBot состоит из двух независимых частей:[/bold]\n"
                "  • [accent]Telegram-бот[/accent] — продажа VPN-ключей в ТГ\n"
                "    (пункт меню [bold]9 — Установить / переустановить бота[/bold])\n"
                "  • [accent]Веб-сайт[/accent] — личный кабинет для клиентов\n"
                "    (пункт меню [bold]10 — Веб-сайт[/bold])\n\n"
                "[text]Можно установить только одно из двух, либо оба.[/text]\n"
                "[text]Выберите нужный пункт в меню ниже.[/text]",
                border_style="accent.dim",
                width=_PANEL_W,
                title="[ok.bold]Первый запуск[/ok.bold]",
                padding=(1, 2),
            )
        )
        return

    missing_labels: list[str] = []
    if not has_venv:
        missing_labels.append("Python virtual environment (venv/) с зависимостями")
    if not has_service:
        missing_labels.append(f"systemd-служба {SERVICE_NAME} (автозапуск)")
    if not missing_labels:
        return
    bullets = "\n".join(f"  • {label}" for label in missing_labels)
    console.print(
        Panel(
            "[text]Установка бота частично нарушена.[/text]\n"
            f"[warn]Не хватает:[/warn]\n{bullets}\n\n"
            "[text]CLI допустит недостающие части — исходники и настройки не трогаются.[/text]",
            border_style="warn",
            width=_PANEL_W,
            title="[warn.bold]Починка установки бота[/warn.bold]",
            padding=(1, 2),
        )
    )
    if safe_confirm("Выполнить починку сейчас?", default=True):
        install_bot()


def _bot_state() -> tuple:
    if not has_project_code():
        return "faint", "не установлен"
    if not is_service_exists(SERVICE_NAME):
        return "warn", "служба не создана"
    state = _service_state()
    if state == "active":
        return "ok", "работает"
    if state == "activating":
        return "warn", "запускается"
    if state == "failed":
        return "err", "сбой"
    return "muted", "остановлен"


def _site_state() -> tuple:
    if not os.path.exists(WEB_DIR):
        return "faint", "не установлен", None
    version = read_installed_solo_brick_version()
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "--format", "{{.State}}"],
            cwd=WEB_DIR,
            capture_output=True,
            text=True,
            timeout=5,
        )
        states = [s.strip() for s in (result.stdout or "").splitlines() if s.strip()]
    except Exception:
        return "warn", "статус неизвестен", version
    if not states:
        return "muted", "остановлен", version
    running = sum(1 for s in states if s.lower() == "running")
    if running == len(states):
        return "ok", "работает", version
    return "warn", f"частично ({running}/{len(states)})", version


def print_logo():
    logo_lines = [
        "███████╗ ██████╗ ██╗      ██████╗ ██████╗  ██████╗ ████████╗",
        "██╔════╝██╔═══██╗██║     ██╔═══██╗██╔══██╗██╔═══██╗╚══██╔══╝",
        "███████╗██║   ██║██║     ██║   ██║██████╔╝██║   ██║   ██║   ",
        "╚════██║██║   ██║██║     ██║   ██║██╔══██╗██║   ██║   ██║   ",
        "███████║╚██████╔╝███████╗╚██████╔╝██████╔╝╚██████╔╝   ██║   ",
        "╚══════╝ ╚═════╝ ╚══════╝ ╚═════╝ ╚═════╝  ╚═════╝    ╚═╝   ",
    ]
    console.print()
    console.print(
        Panel(
            Group(*[f"[accent]{line.center(_PANEL_W - 8)}[/accent]" for line in logo_lines]),
            border_style="accent.dim",
            box=box.ROUNDED,
            padding=(0, 3),
            width=_PANEL_W,
            subtitle=f"[faint]Solobot CLI {CLI_VERSION}[/faint]",
            subtitle_align="right",
        )
    )

    home = os.path.expanduser("~")
    where = PROJECT_DIR.replace(home, "~", 1) if PROJECT_DIR.startswith(home) else PROJECT_DIR
    style, state = _bot_state()
    s_style, s_state, s_version = _site_state()
    bot_installed = has_project_code()
    site_installed = os.path.exists(WEB_DIR)

    console.print()
    bot_line = f"  [faint]бот[/faint]        [{style}]{_G_DOT} {state}[/{style}]"
    if bot_installed:
        bot_line += f"   [title]{local_version(PROJECT_DIR) or '—'}[/title]"
    console.print(bot_line)
    site_line = f"  [faint]сайт[/faint]       [{s_style}]{_G_DOT} {s_state}[/{s_style}]"
    if s_version:
        site_line += f"   [title]{s_version}[/title]"
    console.print(site_line)
    if bot_installed:
        console.print(f"  [faint]обновлён[/faint]   [text]{get_last_update_date() or '—'}[/text]")
        console.print(f"  [faint]папка[/faint]      [muted]{where}[/muted]")
    elif site_installed:
        site_where = WEB_DIR.replace(home, "~", 1) if WEB_DIR.startswith(home) else WEB_DIR
        console.print(f"  [faint]папка[/faint]      [muted]{site_where}[/muted]")


def list_backups():
    if not os.path.isdir(BACK_DIR):
        return []
    pairs = []
    for name in os.listdir(BACK_DIR):
        path = os.path.join(BACK_DIR, name)
        if os.path.isdir(path):
            try:
                mtime = os.path.getmtime(path)
            except Exception:
                mtime = 0
            pairs.append((mtime, path))
    pairs.sort(reverse=True)
    return [p for _, p in pairs]


def prune_old_backups():
    backups = list_backups()
    for path in backups[3:]:
        try:
            shutil.rmtree(path, ignore_errors=True)
        except Exception:
            subprocess.run(["sudo", "rm", "-rf", path])


BACKUP_SKIP_DIRS = ("venv", "node_modules", ".git", "__pycache__")


def backup_project() -> str | None:
    """Копия проекта без того, что восстанавливается само.

    venv пересобирает установка зависимостей, node_modules и .git тянут сотни
    мегабайт и в откате не нужны — держать их в копии значит только раздувать её.
    """
    from datetime import datetime

    os.makedirs(BACK_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(BACK_DIR, f"backup-{ts}")
    step_warn("Создаётся резервная копия проекта...")
    install_rsync_if_needed()
    excludes = [f"--exclude={name}" for name in BACKUP_SKIP_DIRS]
    with console.status("[brand]Копирование файлов...[/brand]"):
        result = subprocess.run(
            ["rsync", "-a", *excludes, f"{PROJECT_DIR}/", f"{dst}/"],
            check=False,
        )
    if result.returncode != 0:
        step_fail("Не удалось создать бэкап")
        return None
    step_ok(f"Бэкап сохранён в: {dst}")
    prune_old_backups()
    return dst


def _restore_backup_unattended(backup_path: str) -> bool:
    if not backup_path or not os.path.isdir(backup_path):
        return False
    if is_service_exists(SERVICE_NAME):
        subprocess.run(["sudo", "systemctl", "stop", SERVICE_NAME], check=False)
    install_rsync_if_needed()
    # Того, чего в копии нет, --delete не должен сносить: venv переживает откат.
    excludes = [f"--exclude={name}" for name in BACKUP_SKIP_DIRS]
    result = run_with_status(
        ["rsync", "-a", "--delete", *excludes, f"{backup_path}/", f"{PROJECT_DIR}/"],
        status_text="Откат из бэкапа",
    )
    return result.returncode == 0


def _build_update_rsync_excludes(update_buttons: bool, update_img: bool, update_redis_cache: bool) -> list[str]:
    excludes = []
    if not update_img:
        excludes.append("--exclude=img")
    if not update_buttons:
        excludes.append("--exclude=handlers/buttons.py")
        excludes.append("--exclude=settings/buttons.py")
    if not update_redis_cache:
        excludes.append("--exclude=core/redis_cache.py")
    excludes.append("--exclude=modules")
    excludes.append("--exclude=static/web_uploads")
    return excludes


def restore_from_backup():
    from datetime import datetime

    backups = list_backups()[:3]
    if not backups:
        step_fail(f"Бэкапы не найдены: {BACK_DIR}")
        return

    heading("Резервные копии", BACK_DIR)
    shown = []
    for idx, path in enumerate(backups, 1):
        try:
            dt = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%d.%m.%Y %H:%M")
        except Exception:
            dt = "дата неизвестна"
        console.print(f"  [key]{idx}[/key]  [text]{os.path.basename(path)}[/text]  [faint]{dt}[/faint]")
        shown.append((idx, path))

    try:
        choice = safe_prompt(
            f"[key]{_G_PROMPT}[/key] [title]Какой бэкап восстановить[/title]",
            choices=[str(i) for i, _ in shown],
        )
    except Exception:
        return

    sel_path = shown[int(choice) - 1][1]

    step_warn("Текущие файлы проекта будут перезаписаны выбранным бэкапом")
    if not safe_confirm("Продолжить восстановление из бэкапа?"):
        return

    if is_service_exists(SERVICE_NAME):
        console.print("[title]Останавливаю службу перед восстановлением...[/title]")
        subprocess.run(["sudo", "systemctl", "stop", SERVICE_NAME])

    install_rsync_if_needed()

    step_warn("Копирую файлы из бэкапа в проект...")
    rc = subprocess.run(
        ["rsync", "-a", "--delete", f"{sel_path}/", f"{PROJECT_DIR}/"],
        check=False,
    ).returncode
    if rc != 0:
        step_fail("Ошибка rsync при восстановлении")
        return

    install_dependencies()
    fix_permissions()
    restart_service()
    step_ok("Восстановление из бэкапа завершено")


def _pg_docker_container() -> str | None:
    try:
        out = subprocess.run(
            ["docker", "ps", "--filter", "name=solobot-postgres", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    names = [n.strip() for n in out.stdout.splitlines() if n.strip()]
    if "solobot-postgres" in names:
        return "solobot-postgres"
    return names[0] if names else None


def _list_db_backups() -> list[str]:
    if not os.path.isdir(BACK_DIR):
        return []
    files = [
        os.path.join(BACK_DIR, name)
        for name in os.listdir(BACK_DIR)
        if name.startswith("db-") and name.endswith(".dump")
    ]
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return files


def _bot_backup_dir() -> str:
    """Каталог, куда бэкапы кладёт сам бот — берём из его config.py."""
    value = _read_config_str("BACK_DIR").strip()
    if not value:
        return ""
    return value if os.path.isabs(value) else os.path.join(PROJECT_DIR, value)


def _list_bot_db_backups() -> list[str]:
    """Дампы, снятые ботом при запуске. Формат тот же pg_dump -Fc, отличается только имя."""
    path = _bot_backup_dir()
    if not path or not os.path.isdir(path):
        return []
    if os.path.abspath(path) == os.path.abspath(BACK_DIR):
        return []
    files = [
        os.path.join(path, name)
        for name in os.listdir(path)
        if "-backup-" in name and "-full-backup-" not in name and name.endswith((".sql", ".dump"))
    ]
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return files


def _list_restorable_dumps() -> list[tuple[str, str]]:
    """Все дампы, пригодные для pg_restore: снятые из CLI и снятые ботом."""
    items = [(path, "CLI") for path in _list_db_backups()]
    items += [(path, "бот") for path in _list_bot_db_backups()]
    items.sort(key=lambda item: os.path.getmtime(item[0]), reverse=True)
    return items


def _prune_db_backups(keep: int = 5) -> None:
    for path in _list_db_backups()[keep:]:
        try:
            os.remove(path)
        except Exception:
            pass


def backup_database() -> str | None:
    from datetime import datetime

    creds = _read_config_db_creds()
    if not creds.get("password"):
        step_fail("В config.py не заполнен доступ к БД (DB_PASSWORD).")
        return None

    os.makedirs(BACK_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(BACK_DIR, f"db-{ts}.dump")
    container = _pg_docker_container()

    step_warn("Создаётся дамп базы данных...")
    try:
        with open(dst, "wb") as out_file:
            if container:
                proc = subprocess.run(
                    [
                        "docker", "exec", "-e", f"PGPASSWORD={creds['password']}", container,
                        "pg_dump", "-U", creds["user"], "-h", "127.0.0.1", "-p", "5432",
                        "-F", "c", creds["name"],
                    ],
                    stdout=out_file,
                    stderr=subprocess.PIPE,
                )
            elif shutil.which("pg_dump") is None:
                step_fail("pg_dump не найден на хосте и контейнер PostgreSQL не обнаружен.")
                out_file.close()
                os.remove(dst)
                return None
            else:
                host = _read_config_str("PG_HOST") or "127.0.0.1"
                port = _read_config_str("PG_PORT") or "5432"
                env = {**os.environ, "PGPASSWORD": creds["password"]}
                proc = subprocess.run(
                    ["pg_dump", "-U", creds["user"], "-h", host, "-p", port, "-F", "c", creds["name"]],
                    stdout=out_file,
                    stderr=subprocess.PIPE,
                    env=env,
                )
    except Exception as e:
        step_fail(f"Ошибка бэкапа БД: {e}")
        return None

    if proc.returncode != 0:
        step_fail((proc.stderr or b"").decode("utf-8", "replace").strip()[:300] or "Ошибка pg_dump")
        try:
            os.remove(dst)
        except Exception:
            pass
        return None

    size_mb = os.path.getsize(dst) / (1024 * 1024)
    step_ok(f"Бэкап БД сохранён: {dst} ({size_mb:.1f} МБ)")
    _prune_db_backups()
    return dst


def _pg_recreate_db(creds: dict, container: str | None, host: str, port: str) -> bool:
    name = creds["name"]
    user = creds["user"]
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name) or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", user):
        step_fail("Недопустимое имя БД или пользователя в config.py.")
        return False

    def run_admin(sql: str):
        if container:
            return subprocess.run(
                [
                    "docker", "exec", "-e", f"PGPASSWORD={creds['password']}", container,
                    "psql", "-U", user, "-h", "127.0.0.1", "-p", "5432", "-d", "postgres", "-c", sql,
                ],
                capture_output=True,
                text=True,
            )
        env = {**os.environ, "PGPASSWORD": creds["password"]}
        return subprocess.run(
            ["psql", "-U", user, "-h", host, "-p", port, "-d", "postgres", "-c", sql],
            capture_output=True,
            text=True,
            env=env,
        )

    with console.status("[warn.bold]Пересоздаю базу данных…[/warn.bold]"):
        run_admin(
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{name}' AND pid <> pg_backend_pid();"
        )
        drop = run_admin(f"DROP DATABASE IF EXISTS {name};")
        create = run_admin(f"CREATE DATABASE {name} OWNER {user};")

    if drop.returncode != 0 or create.returncode != 0:
        step_fail((drop.stderr or create.stderr or "").strip()[:300] or "Не удалось пересоздать базу данных.")
        return False
    return True


def restore_database():
    from datetime import datetime

    backups = _list_restorable_dumps()[:10]
    if not backups:
        places = BACK_DIR
        bot_dir = _bot_backup_dir()
        if bot_dir and os.path.abspath(bot_dir) != os.path.abspath(BACK_DIR):
            places = f"{BACK_DIR} и {bot_dir}"
        step_fail(f"Бэкапы базы данных не найдены: {places}")
        return

    heading("Бэкапы базы данных", BACK_DIR)
    shown = []
    for idx, (path, origin) in enumerate(backups, 1):
        try:
            dt = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%d.%m.%Y %H:%M")
        except Exception:
            dt = "дата неизвестна"
        console.print(
            f"  [key]{idx}[/key]  [text]{os.path.basename(path)}[/text]  [faint]{dt} · {origin}[/faint]"
        )
        shown.append((idx, path))

    try:
        choice = safe_prompt(
            f"[key]{_G_PROMPT}[/key] [title]Какой дамп восстановить[/title]",
            choices=[str(i) for i, _ in shown],
        )
    except Exception:
        return
    sel_path = shown[int(choice) - 1][1]

    step_warn("Текущая база данных будет ПОЛНОСТЬЮ заменена выбранным дампом.")
    if not safe_confirm("Продолжить восстановление базы данных?"):
        return

    creds = _read_config_db_creds()
    container = _pg_docker_container()
    host = _read_config_str("PG_HOST") or "127.0.0.1"
    port = _read_config_str("PG_PORT") or "5432"

    if is_service_exists(SERVICE_NAME):
        console.print("[title]Останавливаю бота перед восстановлением БД...[/title]")
        subprocess.run(["sudo", "systemctl", "stop", SERVICE_NAME], check=False)

    if not _pg_recreate_db(creds, container, host, port):
        return

    step_warn("Восстанавливаю базу данных из дампа...")
    if container:
        with open(sel_path, "rb") as dump_file:
            rc = subprocess.run(
                [
                    "docker", "exec", "-i", "-e", f"PGPASSWORD={creds['password']}", container,
                    "pg_restore", f"--dbname={creds['name']}", "-U", creds["user"],
                    "-h", "127.0.0.1", "-p", "5432", "--no-owner", "--clean", "--if-exists",
                ],
                stdin=dump_file,
                capture_output=True,
            ).returncode
    elif shutil.which("pg_restore") is None:
        step_fail("pg_restore не найден на хосте и контейнер PostgreSQL не обнаружен.")
        return
    else:
        env = {**os.environ, "PGPASSWORD": creds["password"]}
        rc = subprocess.run(
            [
                "pg_restore", f"--dbname={creds['name']}", "-U", creds["user"], "-h", host, "-p", port,
                "--no-owner", "--clean", "--if-exists", sel_path,
            ],
            capture_output=True,
            env=env,
        ).returncode

    if rc != 0:
        step_fail("Ошибка pg_restore при восстановлении базы данных.")
    else:
        step_ok("База данных восстановлена.")

    if is_service_exists(SERVICE_NAME):
        restart_service()


def manage_backup():
    while True:
        menu(
            "Бэкап и восстановление",
            [
                (
                    "Создать бэкап",
                    [
                        ("1", "▤", "Папка бота", True, ""),
                        ("2", "▤", "База данных", True, ""),
                        ("3", "▤", "Всё вместе", True, ""),
                    ],
                ),
                (
                    "Восстановить",
                    [
                        ("4", "⭯", "Папку бота", True, ""),
                        ("5", "⭯", "Базу данных", True, ""),
                        ("6", "⭯", "Всё вместе", True, ""),
                    ],
                ),
                (
                    "",
                    [
                        ("7", "←", "Назад", True, ""),
                    ],
                ),
            ],
            subtitle=BACK_DIR,
        )
        choice = ask_choice(7, "Действие")
        if choice == "1":
            backup_project()
        elif choice == "2":
            backup_database()
        elif choice == "3":
            backup_project()
            backup_database()
        elif choice == "4":
            restore_from_backup()
        elif choice == "5":
            restore_database()
        elif choice == "6":
            restore_from_backup()
            restore_database()
        elif choice == "7":
            return


def _sync_rpc_files() -> bool:
    core_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "core")
    os.makedirs(core_dir, exist_ok=True)
    cachebuster = str(int(time_mod.time()))
    base_url = "https://raw.githubusercontent.com/Vladless/Solo_bot/dev/core"
    targets = [
        ("__init__.py", os.path.join(core_dir, "__init__.py")),
        (
            "rpc.cpython-312-x86_64-linux-gnu.so",
            os.path.join(core_dir, "rpc.cpython-312-x86_64-linux-gnu.so"),
        ),
    ]
    updated: list[str] = []
    for name, path in targets:
        try:
            req = Request(
                f"{base_url}/{name}?v={cachebuster}",
                headers={"Cache-Control": "no-cache", "Pragma": "no-cache"},
            )
            with urlopen(req, timeout=20) as resp:
                remote_bytes = resp.read()
        except Exception as e:
            step_fail(f"Не удалось скачать core/{name}: {e}")
            continue
        if not remote_bytes:
            step_fail(f"core/{name}: пустой ответ от GitHub")
            continue
        local_bytes = None
        if os.path.exists(path):
            try:
                with open(path, "rb") as f:
                    local_bytes = f.read()
            except Exception:
                local_bytes = None
        if local_bytes == remote_bytes:
            continue
        try:
            with open(path, "wb") as f:
                f.write(remote_bytes)
            updated.append(f"core/{name}")
        except Exception as e:
            step_fail(f"Не удалось записать core/{name}: {e}")
    if updated:
        step_ok(f"Обновлены: {', '.join(updated)}")
        import sys as _sys

        for mod_name in list(_sys.modules.keys()):
            if mod_name == "core" or mod_name == "core.rpc" or mod_name.startswith("core."):
                del _sys.modules[mod_name]
        return True
    return False


def auto_update_cli():
    step_warn("Проверка обновлений CLI...")
    try:
        url = "https://raw.githubusercontent.com/Vladless/Solo_bot/dev/cli_launcher.py"
        response = http_get(url, timeout=10)
        if response.status_code != 200:
            step_fail("Не удалось получить обновление CLI")
            return

        latest_text = response.text
        current_path = os.path.realpath(__file__)
        with open(current_path, encoding="utf-8") as f:
            current_text = f.read()

        rpc_updated = _sync_rpc_files()

        if current_text != latest_text:
            step_ok("Доступна новая версия CLI. Обновляю...")
            with open(current_path, "w", encoding="utf-8") as f:
                f.write(latest_text)
            os.chmod(current_path, 0o644)
            step_ok("CLI обновлён. Перезапуск...")
            os.execv(sys.executable, [sys.executable, current_path])
        elif rpc_updated:
            step_ok("core/rpc обновлён. Перезапуск CLI...")
            os.execv(sys.executable, [sys.executable, current_path])
        else:
            step_ok("CLI уже актуален")
    except Exception as e:
        step_fail(f"Ошибка при автообновлении CLI: {e}")


def fix_permissions():
    step_warn("Восстанавливаю владельца и права доступа к проекту...")

    try:
        user = os.environ.get("SUDO_USER") or subprocess.check_output(["whoami"], text=True).strip()
        console.print(
            f"  [faint]Пользователь {user}: владелец проекта, права u=rwX,go=rX, чистка __pycache__[/faint]"
        )

        skip_dirs = {"venv", ".venv", ".git", "node_modules"}
        for root, dirs, files in os.walk(PROJECT_DIR):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for dir in list(dirs):
                if dir == "__pycache__":
                    pycache_path = os.path.join(root, dir)
                    subprocess.run(["sudo", "rm", "-rf", pycache_path], check=False)
                    dirs.remove(dir)
            for file in files:
                if file.endswith(".pyc"):
                    pyc_path = os.path.join(root, file)
                    subprocess.run(["sudo", "rm", "-f", pyc_path], check=False)

        subprocess.run(["sudo", "chown", "-R", f"{user}:{user}", PROJECT_DIR], check=True)
        subprocess.run(["sudo", "chmod", "-R", "u=rwX,go=rX", PROJECT_DIR], check=True)

        closed = []
        for secret in (
            "config.py",
            os.path.join("handlers", "texts.py"),
            os.path.join("settings", "config.py"),
            os.path.join("settings", "texts.py"),
            ".env",
        ):
            secret_path = os.path.join(PROJECT_DIR, secret)
            if os.path.exists(secret_path):
                subprocess.run(["sudo", "chown", f"{user}:{user}", secret_path], check=False)
                subprocess.run(["sudo", "chmod", "600", secret_path], check=False)
                closed.append(secret)
        if closed:
            console.print(f"  [faint]Секреты закрыты (600): {', '.join(closed)}[/faint]")

        launcher_path = os.path.join(PROJECT_DIR, "cli_launcher.py")
        if os.path.exists(launcher_path):
            subprocess.run(["chmod", "+x", launcher_path], check=True)

        step_ok(f"Права восстановлены для пользователя {user}.")

    except Exception as e:
        step_fail(f"Ошибка при установке прав: {e}")


def install_rsync_if_needed():
    install_core_packages_if_needed()


def clean_project_dir_safe(update_buttons=False, update_img=False, update_redis_cache=False):
    step_warn("Очистка проекта перед обновлением...")

    preserved_paths = set()

    preserved_paths.update([
        os.path.join(PROJECT_DIR, "config.py"),
        os.path.join(PROJECT_DIR, "handlers", "texts.py"),
        os.path.join(PROJECT_DIR, "settings"),
        os.path.join(PROJECT_DIR, "settings", "config.py"),
        os.path.join(PROJECT_DIR, "settings", "texts.py"),
        os.path.join(PROJECT_DIR, ".git"),
        os.path.join(PROJECT_DIR, ".cli_session"),
        os.path.join(PROJECT_DIR, ".license_state"),
        os.path.join(PROJECT_DIR, ".license_lease"),
        os.path.join(PROJECT_DIR, "modules"),
        os.path.join(PROJECT_DIR, "static"),
        os.path.join(PROJECT_DIR, "static", "web_uploads"),
    ])

    for root, dirs, files in os.walk(os.path.join(PROJECT_DIR, "modules")):
        for name in dirs + files:
            preserved_paths.add(os.path.join(root, name))

    for root, dirs, files in os.walk(os.path.join(PROJECT_DIR, "static", "web_uploads")):
        for name in dirs + files:
            preserved_paths.add(os.path.join(root, name))

    if not update_buttons:
        preserved_paths.add(os.path.join(PROJECT_DIR, "handlers", "buttons.py"))
        preserved_paths.add(os.path.join(PROJECT_DIR, "settings", "buttons.py"))

    if not update_img:
        preserved_paths.add(os.path.join(PROJECT_DIR, "img"))
        for root, dirs, files in os.walk(os.path.join(PROJECT_DIR, "img")):
            for name in dirs + files:
                preserved_paths.add(os.path.join(root, name))

    if not update_redis_cache:
        preserved_paths.add(os.path.join(PROJECT_DIR, "core", "redis_cache.py"))

    for root, dirs, files in os.walk(PROJECT_DIR, topdown=False):
        for file in files:
            path = os.path.join(root, file)
            if path in preserved_paths:
                continue
            try:
                os.remove(path)
            except PermissionError:
                subprocess.run(["sudo", "rm", "-f", path])
            except Exception as e:
                step_fail(f"Не удалось удалить файл: {path}: {e}")

        for dir in dirs:
            dir_path = os.path.join(root, dir)

            if os.path.abspath(dir_path) in [
                os.path.join(PROJECT_DIR, "handlers"),
                os.path.join(PROJECT_DIR, "img"),
                os.path.join(PROJECT_DIR, "modules"),
                os.path.join(PROJECT_DIR, "settings"),
                os.path.join(PROJECT_DIR, "static"),
                os.path.join(PROJECT_DIR, "static", "web_uploads"),
            ]:
                continue

            if os.path.abspath(dir_path).startswith(os.path.join(PROJECT_DIR, "modules") + os.sep):
                continue

            if os.path.abspath(dir_path).startswith(os.path.join(PROJECT_DIR, "static", "web_uploads") + os.sep):
                continue

            try:
                if os.listdir(dir_path):
                    continue
            except OSError:
                continue

            try:
                os.rmdir(dir_path)
            except Exception:
                subprocess.run(["sudo", "rm", "-rf", dir_path])


def install_git_if_needed():
    install_core_packages_if_needed()


def _pip_install_streamed(cmd, progress, task_id):
    import re as _re

    proc = subprocess.Popen(
        cmd, cwd=PROJECT_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
    )
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        m = _re.match(r"(?:Using cached|Downloading|Collecting)\s+([A-Za-z0-9._-]+)", line)
        if m:
            progress.update(task_id, description=f"Зависимости: {m.group(1).split('-')[0]}")
        elif line.startswith("Installing collected packages"):
            progress.update(task_id, description="Зависимости: установка пакетов…")
        elif line.startswith("Building wheel for"):
            progress.update(task_id, description=f"Зависимости: сборка {line.split('for', 1)[1].strip().split(' ')[0]}")
    proc.wait()
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd)


def install_dependencies():
    console.print("[title]Установка зависимостей...[/title]")
    install_core_packages_if_needed()

    python312_path = shutil.which("python3.12")
    if not python312_path:
        step_fail("Не найден python3.12 в системе")
        step_warn("Установите Python 3.12: sudo apt install python3.12 python3.12-venv")
        sys.exit(1)

    with Progress(
        SpinnerColumn(style="green"),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) as progress:
        task_id = progress.add_task(description="Создание виртуального окружения...", total=None)
        try:
            if os.path.exists("venv"):
                shutil.rmtree("venv")
                step_warn("Удалён старый venv")

            subprocess.run([python312_path, "-m", "venv", "venv"], check=True)

            progress.update(task_id, description="Установка зависимостей...")
            _pip_install_streamed(
                [os.path.join("venv", "bin", "pip"), "install", "-r", "requirements.txt"],
                progress,
                task_id,
            )

            progress.update(task_id, description="Установка завершена")

        except subprocess.CalledProcessError as e:
            progress.update(task_id, description="Ошибка при установке")
            step_fail(f"Ошибка: {e}")


def restart_service():
    if ensure_systemd_service():
        console.print("[title]Перезапуск службы...[/title]")
        with console.status("[warn.bold]Перезапуск...[/warn.bold]"):
            subprocess.run(["sudo", "systemctl", "enable", SERVICE_NAME], check=False)
            subprocess.run(["sudo", "systemctl", "restart", SERVICE_NAME])


_STARTUP_SUCCESS_MARKERS = (
    "Run polling for bot",
    "Start polling",
    "Application startup complete",
)

_STARTUP_FATAL_MARKERS = (
    "Traceback (most recent call last)",
    "ModuleNotFoundError",
    "ImportError:",
    "SyntaxError:",
    "IndentationError:",
    "Main process exited",
    "Failed with result",
    "Start request repeated too quickly",
)


_ERROR_LINE_RE = re.compile(r"^(?:[\w.]+(?:Error|Exception|Warning|Exit))\b.*|^SystemExit\b.*")


def _is_noise_line(line: str) -> bool:
    if len(line) > 400:
        return True
    if line.count("\\x") >= 4:
        return True
    return False


def _shorten(line: str, limit: int = 300) -> str:
    line = line.strip()
    return line if len(line) <= limit else line[:limit] + "…"


def _is_caret_line(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and set(stripped) <= set("^~ .…")


def _looks_like_error_line(stripped: str) -> bool:
    if _ERROR_LINE_RE.match(stripped):
        return True
    if ": " in stripped:
        head = stripped.split(":", 1)[0]
        return head.replace(".", "").isidentifier() and head.endswith(("Error", "Exception"))
    return False


def _extract_error_summary(lines: list[str]) -> list[str]:
    rows = [line for line in lines if line.strip() and not _is_caret_line(line)]
    if not rows:
        return []

    err_idx = None
    for idx in range(len(rows) - 1, -1, -1):
        if _looks_like_error_line(rows[idx].strip()):
            err_idx = idx
            break

    if err_idx is None:
        block = rows
        for idx in range(len(rows) - 1, -1, -1):
            if "Traceback (most recent call last)" in rows[idx]:
                block = rows[idx:]
                break
        file_lines = [line.strip() for line in block if line.strip().startswith("File ")]
        tail_line = next(
            (
                line.strip()
                for line in reversed(block)
                if not _is_noise_line(line) and not line.strip().startswith("File ")
            ),
            block[-1].strip(),
        )
        summary = [_shorten(line) for line in file_lines[-2:]]
        if tail_line not in summary:
            summary.append(_shorten(tail_line))
        return summary

    start = 0
    for idx in range(err_idx, -1, -1):
        if "Traceback (most recent call last)" in rows[idx]:
            start = idx
            break
    block = rows[start:err_idx]

    file_lines = [line.strip() for line in block if line.strip().startswith("File ")]
    code_line = next(
        (
            line.strip()
            for line in reversed(block)
            if not line.strip().startswith("File ") and not _is_noise_line(line) and "Traceback" not in line
        ),
        None,
    )

    summary = [_shorten(line) for line in file_lines[-2:]]
    if code_line:
        summary.append(_shorten(code_line))
    summary.append(_shorten(rows[err_idx].strip()))
    return summary


def _service_state() -> str:
    result = subprocess.run(["systemctl", "is-active", SERVICE_NAME], capture_output=True, text=True)
    return (result.stdout or "").strip()


def _journal_tail(lines: int = 30) -> list[str]:
    result = subprocess.run(
        ["sudo", "journalctl", "-u", SERVICE_NAME, "-n", str(lines), "--no-pager", "-o", "cat"],
        capture_output=True,
        text=True,
    )
    return [line for line in (result.stdout or "").splitlines() if line.strip()]


def wait_for_bot_startup(timeout: int = 300) -> None:
    """Стримит логи службы после рестарта до полного запуска бота или явной ошибки."""
    if not is_service_exists(SERVICE_NAME):
        return
    console.print(f"[title]Слежу за логами запуска бота (до {timeout} сек)...[/title]")

    try:
        proc = subprocess.Popen(
            ["sudo", "journalctl", "-u", SERVICE_NAME, "-f", "-n", "0", "--no-pager", "-o", "cat"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
    except Exception as e:
        step_warn(f"journalctl недоступен ({e}) — проверяю только статус службы.")
        sleep(10)
        state = _service_state()
        if state == "active":
            step_ok("Успешно: служба активна.")
        else:
            step_fail(f"Служба не активна ({state or 'нет статуса'}). Проверьте логи (пункт 5 меню).")
        return

    started_at = time_mod.time()
    last_state_check = 0.0
    error_lines: list[str] = []
    fatal_seen_at: float | None = None
    verdict: str | None = None

    try:
        while True:
            now = time_mod.time()
            if now - started_at > timeout:
                break
            if fatal_seen_at is not None and now - fatal_seen_at > 10:
                verdict = "fail"
                break

            ready, _, _ = select.select([proc.stdout], [], [], 1.0)
            if ready:
                line = proc.stdout.readline()
                if not line:
                    break
                line = line.rstrip("\n")
                if not _is_noise_line(line) and fatal_seen_at is None:
                    console.print(line, markup=False, highlight=False, style="dim")
                if fatal_seen_at is not None:
                    error_lines.append(line)
                    if _ERROR_LINE_RE.match(line.strip()):
                        verdict = "fail"
                        break
                elif any(marker in line for marker in _STARTUP_SUCCESS_MARKERS):
                    verdict = "ok"
                    break
                elif any(marker in line for marker in _STARTUP_FATAL_MARKERS):
                    fatal_seen_at = time_mod.time()
                    error_lines.append(line)
            elif now - last_state_check > 5:
                last_state_check = now
                if _service_state() in ("failed", "inactive"):
                    verdict = "fail"
                    if not error_lines:
                        error_lines = _journal_tail(80)
                    break
    finally:
        try:
            proc.terminate()
        except Exception:
            pass

    if verdict == "ok":
        step_ok("Успешно: бот полностью запущен.")
    elif verdict == "fail":
        step_fail("Бот не запустился. Ошибка из службы:")
        summary = _extract_error_summary(_journal_tail(200)) or _extract_error_summary(error_lines)
        for line in summary:
            console.print(line, markup=False, highlight=False, style="err")
    else:
        if _service_state() == "active":
            step_ok("Успешно: служба активна.")
        else:
            step_fail("Служба не активна. Ошибка из службы:")
            for line in _extract_error_summary(_journal_tail(80)):
                console.print(line, markup=False, highlight=False, style="err")

    try:
        input("Нажмите Enter, чтобы вернуться в меню CLI... ")
    except (EOFError, KeyboardInterrupt):
        pass


def get_last_update_date():
    try:
        result = subprocess.run(
            ["git", "-C", PROJECT_DIR, "log", "-1", "--format=%cd", "--date=format:%Y-%m-%d %H:%M:%S"],
            capture_output=True,
            text=True,
            check=False,
        )
        value = result.stdout.strip()
        if result.returncode == 0 and value:
            return value
    except Exception:
        pass

    excluded_dirs = {".git", "venv", ".venv", "__pycache__", "build", "dist"}
    latest_mtime = 0.0
    for root, dirs, files in os.walk(PROJECT_DIR):
        dirs[:] = [d for d in dirs if d not in excluded_dirs]
        for file_name in files:
            path = os.path.join(root, file_name)
            try:
                latest_mtime = max(latest_mtime, os.path.getmtime(path))
            except Exception:
                continue
    if latest_mtime <= 0:
        return None
    return datetime.fromtimestamp(latest_mtime).strftime("%Y-%m-%d %H:%M:%S")


def get_remote_version(branch="main"):
    try:
        url = f"https://raw.githubusercontent.com/Vladless/Solo_bot/{branch}/utils/versioning.py"
        response = http_get(url, timeout=10)
        if response.status_code == 200:
            version = extract_version(response.text)
            if version:
                return version
    except Exception:
        pass
    try:
        url = f"https://raw.githubusercontent.com/Vladless/Solo_bot/{branch}/bot.py"
        response = http_get(url, timeout=10)
        if response.status_code == 200:
            for line in response.text.splitlines():
                match = re.search(r'version\s*=\s*["\'](.+?)["\']', line)
                if match:
                    return match.group(1)
    except Exception:
        return None
    return None


def _prompt_config_update() -> None:
    console.print(
        Panel(
            "[text]Если для этой версии вы скачали свежие config и texts на сайте — "
            "загрузите их на сервер сейчас, и CLI подставит их сам.[/text]\n\n"
            f"[text]Где взять:[/text] [bold]{CONFIG_BUILDER_URL}[/bold]\n\n"
            "[text]Файлы беты называются [bold]config_beta.py[/bold] и [bold]texts_beta.py[/bold] — "
            "CLI автоматически переименует их в обычные config.py и texts.py.[/text]\n"
            f"[faint]Класть сюда: {PROJECT_DIR}/config_beta.py и {PROJECT_DIR}/texts_beta.py[/faint]",
            border_style="accent.dim",
            width=_PANEL_W,
            title="[brand]Обновляли config и texts?[/brand]",
            padding=(1, 2),
        )
    )
    renamed = adopt_beta_files(PROJECT_DIR)
    if renamed:
        for item in renamed:
            step_ok(f"Подставлен новый файл: {item}")
    else:
        step_warn("Новых config_beta.py / texts_beta.py не найдено — оставляю текущие config.py и texts.py.")


def update_from_beta():
    installed_version = local_version(PROJECT_DIR)
    remote_version = get_remote_version(branch="dev")

    console.print(
        Panel(
            "[err.bold]Обновление на DEV / BETA-ветку[/err.bold]\n\n"
            "[text]"
            "• Dev-ветка может содержать изменения, которые ещё находятся в доработке.\n"
            "• Возможны ошибки и непредсказуемое поведение отдельных функций, особенно режима стран.\n\n"
            "• BETA-версии бота в первую очередь ориентированы на опытных пользователей, "
            "готовых протестировать новые возможности и осознанно работать с обновлённым функционалом.\n"
            "[/text]\n\n"
            "[warn]Перед началом обновления CLI автоматически создаёт резервную копию проекта, "
            "что позволит при необходимости безопасно восстановиться из бэкапа.[/warn]",
            border_style="err",
            width=_PANEL_W,
            title="[err.bold]Нестабильная ветка разработки[/err.bold]",
            padding=(1, 2),
        )
    )

    if installed_version and remote_version:
        console.print(f"[accent]Локальная версия: {installed_version} | Последняя в dev: {remote_version}[/accent]")
        if installed_version == remote_version:
            if not safe_confirm("Версия актуальна. Обновить всё равно?"):
                return

    if not safe_confirm(
        "[err.bold]Продолжить обновление на dev-ветку с учётом возможных особенностей работы?[/err.bold]"
    ):
        return

    step_fail("ВНИМАНИЕ! Папка бота будет перезаписана!")
    if not safe_confirm("Продолжить обновление?"):
        return

    update_buttons = safe_confirm("Обновлять файл buttons.py?", default=False)
    update_img = safe_confirm("Обновлять папку img?", default=False)
    update_redis_cache = safe_confirm("Обновлять файл core/redis_cache.py?", default=False)

    backup_path = backup_project()
    if not backup_path and not safe_confirm(
        "[warn]Бэкап не создан. Продолжить обновление БЕЗ бэкапа?[/warn]", default=False
    ):
        return
    install_git_if_needed()
    install_rsync_if_needed()

    _prompt_config_update()

    if not settings_gate(PROJECT_DIR, "beta", console.print, safe_confirm, CONFIG_BUILDER_URL):
        step_warn("Обновление отменено. Обновите config и texts и запустите снова.")
        return

    try:
        os.chdir(PROJECT_DIR)
        subprocess.run(["rm", "-rf", TEMP_DIR])

        clone_result = run_with_status(
            ["git", "clone", "--depth=1000000", "-b", "dev", GITHUB_REPO, TEMP_DIR],
            status_text=f"Клонирование dev-ветки {GITHUB_REPO}",
        )
        if clone_result.returncode != 0:
            raise RuntimeError("git clone dev не удался")

        subprocess.run(["sudo", "rm", "-rf", os.path.join(PROJECT_DIR, "venv")])
        clean_project_dir_safe(
            update_buttons=update_buttons,
            update_img=update_img,
            update_redis_cache=update_redis_cache,
        )

        rsync_cmd = (
            ["rsync", "-a"]
            + _build_update_rsync_excludes(update_buttons, update_img, update_redis_cache)
            + [f"{TEMP_DIR}/", f"{PROJECT_DIR}/"]
        )
        rsync_result = run_with_status(rsync_cmd, status_text="Применение обновления (rsync)")
        if rsync_result.returncode != 0:
            raise RuntimeError("rsync обновления не удался")

        migrate_settings_layout(PROJECT_DIR, out=console.print)

        modules_path = os.path.join(PROJECT_DIR, "modules")
        if not os.path.exists(modules_path):
            try:
                os.makedirs(modules_path, exist_ok=True)
            except Exception:
                pass

        if os.path.exists(os.path.join(TEMP_DIR, ".git")):
            subprocess.run(["cp", "-r", os.path.join(TEMP_DIR, ".git"), PROJECT_DIR])

        subprocess.run(["rm", "-rf", TEMP_DIR])

        install_dependencies()
        fix_permissions()
        restart_service()
        step_ok("Обновление с ветки dev завершено. Проверяю запуск бота...")
        wait_for_bot_startup()
    except Exception as e:
        step_fail(f"Обновление упало: {e}")
        if backup_path and safe_confirm("Откатить проект из свежего бэкапа?", default=True):
            if _restore_backup_unattended(backup_path):
                step_ok(f"✓ Проект восстановлен из {backup_path}")
                restart_service()
            else:
                step_fail(f"Автооткат не удался. Восстановите вручную: пункт 8 меню → {backup_path}")
        else:
            step_warn(f"Для ручного отката: пункт 8 меню → {backup_path or 'нет бэкапа'}")


def _do_update_to_tag(tag_name: str, update_buttons: bool, update_img: bool, update_redis_cache: bool) -> None:
    """Общая логика обновления до указанного тега (релиз или произвольный тег)."""
    if not settings_gate(PROJECT_DIR, "release", console.print, safe_confirm, CONFIG_BUILDER_URL):
        step_warn("Обновление отменено. Обновите config и texts и запустите снова.")
        return

    subprocess.run(["rm", "-rf", TEMP_DIR])
    run_with_status(
        ["git", "clone", "--branch", tag_name, "--depth", "1", GITHUB_REPO, TEMP_DIR],
        status_text=f"Клонирование тега {tag_name}",
        check=True,
    )

    step_fail("Начинается перезапись файлов бота!")
    subprocess.run(["sudo", "rm", "-rf", os.path.join(PROJECT_DIR, "venv")])
    clean_project_dir_safe(
        update_buttons=update_buttons,
        update_img=update_img,
        update_redis_cache=update_redis_cache,
    )

    rsync_cmd = (
        ["rsync", "-a"]
        + _build_update_rsync_excludes(update_buttons, update_img, update_redis_cache)
        + [f"{TEMP_DIR}/", f"{PROJECT_DIR}/"]
    )
    rsync_result = run_with_status(rsync_cmd, status_text=f"Применение тега {tag_name} (rsync)")
    if rsync_result.returncode != 0:
        raise RuntimeError(f"rsync тега {tag_name} не удался")

    migrate_settings_layout(PROJECT_DIR, out=console.print)

    modules_path = os.path.join(PROJECT_DIR, "modules")
    if not os.path.exists(modules_path):
        step_warn("Папка modules отсутствует — создаю вручную...")
        try:
            os.makedirs(modules_path, exist_ok=True)
            step_ok("Папка modules успешно создана.")
        except Exception as e:
            step_fail(f"Не удалось создать папку modules: {e}")

    if os.path.exists(os.path.join(TEMP_DIR, ".git")):
        subprocess.run(["cp", "-r", os.path.join(TEMP_DIR, ".git"), PROJECT_DIR])

    subprocess.run(["rm", "-rf", TEMP_DIR])

    install_dependencies()
    fix_permissions()
    restart_service()
    step_ok(f"Обновление до {tag_name} завершено. Проверяю запуск бота...")
    wait_for_bot_startup()


def update_from_release():
    if not safe_confirm("Подтвердите обновление Solobot до релиза или патча"):
        return

    step_fail("ВНИМАНИЕ! Папка бота будет полностью перезаписана!")
    step_fail("Исключения: папка img, файл кнопок buttons.py и файл core/redis_cache.py")
    if not safe_confirm("Вы точно хотите продолжить?"):
        return

    update_buttons = safe_confirm("Обновлять файл buttons.py?", default=False)
    update_img = safe_confirm("Обновлять папку img?", default=False)
    update_redis_cache = safe_confirm("Обновлять файл core/redis_cache.py?", default=False)

    backup_path = backup_project()
    if not backup_path and not safe_confirm(
        "[warn]Бэкап не создан. Продолжить обновление БЕЗ бэкапа?[/warn]", default=False
    ):
        return
    install_git_if_needed()
    install_rsync_if_needed()

    try:
        rel_resp = http_get(
            "https://api.github.com/repos/Vladless/Solo_bot/releases",
            timeout=10,
        )
        releases = rel_resp.json() if rel_resp.status_code == 200 else []
        release_tag_names = {r["tag_name"] for r in releases}

        tags_resp = http_get(
            "https://api.github.com/repos/Vladless/Solo_bot/tags",
            params={"per_page": 50},
            timeout=10,
        )
        if tags_resp.status_code != 200:
            raise ValueError("Не удалось получить список тегов")
        tags_data = tags_resp.json()
        all_tag_names = [t["name"] for t in tags_data]

        tag_names = [name for name in all_tag_names if _parse_tag_version(name)[0] >= 4]
        tag_names.sort(key=_parse_tag_version)

        if not tag_names:
            raise ValueError("Нет доступных тегов (ожидаются версии начиная с 4)")

        heading("Релизы и патчи", f"установлено: {local_version(PROJECT_DIR) or '—'}")
        for idx, name in enumerate(tag_names, 1):
            label = "релиз" if name in release_tag_names else "патч"
            console.print(f"  [key]{idx}[/key]  [text]{name}[/text]  [faint]{label}[/faint]")

        if _AUTO_YES:
            if _AUTO_TAG and _AUTO_TAG not in tag_names:
                raise ValueError(f"Версия {_AUTO_TAG} недоступна")
            _do_update_to_tag(_AUTO_TAG or tag_names[-1], update_buttons, update_img, update_redis_cache)
            return

        choices = [str(i) for i in range(1, len(tag_names) + 1)]
        selected = safe_prompt(
            f"[key]{_G_PROMPT}[/key] [title]Какую версию поставить[/title]",
            choices=choices,
        )
        tag_name = tag_names[int(selected) - 1]

        if not safe_confirm(f"Установить {tag_name}?"):
            return

        _do_update_to_tag(tag_name, update_buttons, update_img, update_redis_cache)

    except Exception as e:
        step_fail(f"Ошибка при обновлении: {e}")
        if backup_path and safe_confirm("Откатить проект из свежего бэкапа?", default=True):
            if _restore_backup_unattended(backup_path):
                step_ok(f"✓ Проект восстановлен из {backup_path}")
                restart_service()
            else:
                step_fail(f"Автооткат не удался. Восстановите вручную: пункт 8 меню → {backup_path}")
        else:
            step_warn(f"Для ручного отката: пункт 8 меню → {backup_path or 'нет бэкапа'}")


WEB_IMAGE_REPO = "ghcr.io/vladless/solo-brick"
WEB_CONTAINER_NAME = "solo-brick"
WEB_DIR = os.path.join(os.path.expanduser("~"), "solo-brick")
WEB_TAG_FILE = os.path.join(WEB_DIR, ".image-tag")
WEB_TAG_DEFAULT = "latest"
WEB_TAG_CHOICES = ("latest", "dev")


def _web_image(tag: str) -> str:
    return f"{WEB_IMAGE_REPO}:{tag or WEB_TAG_DEFAULT}"


def _get_saved_web_tag() -> str:
    try:
        with open(WEB_TAG_FILE) as f:
            tag = f.read().strip()
        if tag in WEB_TAG_CHOICES:
            return tag
    except Exception:
        pass
    return WEB_TAG_DEFAULT


def _save_web_tag(tag: str) -> None:
    try:
        os.makedirs(WEB_DIR, exist_ok=True)
        with open(WEB_TAG_FILE, "w") as f:
            f.write(tag)
    except Exception:
        pass


def _ensure_web_logs_dir() -> None:
    logs_dir = os.path.join(WEB_DIR, "logs")
    try:
        os.makedirs(logs_dir, exist_ok=True)
        os.chown(logs_dir, 1001, 1001)
    except PermissionError:
        try:
            subprocess.run(["sudo", "chown", "-R", "1001:1001", logs_dir], check=False)
        except Exception:
            pass
    except Exception:
        pass


def _read_env_value(env_path: str, key: str) -> str:
    """Читает значение ключа из .env файла, если файл существует."""
    if not os.path.exists(env_path):
        return ""
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


def _ensure_plugin_builder_token(env_path: str) -> tuple[str, bool]:
    """Возвращает (token, is_new): существующий PLUGIN_BUILDER_TOKEN из .env или свежий 64-hex."""
    existing = _read_env_value(env_path, "PLUGIN_BUILDER_TOKEN")
    if existing and len(existing) >= 32:
        return existing, False
    return secrets.token_hex(32), True


def _generate_vapid_keys() -> tuple[str, str] | None:
    """VAPID keypair (P-256). Returns (public_b64url, private_b64url) или None."""
    try:
        import base64

        from cryptography.hazmat.primitives.asymmetric import ec
    except Exception:
        return None
    priv = ec.generate_private_key(ec.SECP256R1())
    priv_bytes = priv.private_numbers().private_value.to_bytes(32, "big")
    pub_numbers = priv.public_key().public_numbers()
    pub_bytes = b"\x04" + pub_numbers.x.to_bytes(32, "big") + pub_numbers.y.to_bytes(32, "big")

    def _b64url(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")

    return _b64url(pub_bytes), _b64url(priv_bytes)


def _ask_web_tag(default: str = WEB_TAG_DEFAULT) -> str:
    console.print(
        "\n[bold]Канал обновлений:[/bold]\n"
        "  [accent]1[/accent] — [ok]latest[/ok]  стабильный (из ветки main)\n"
        "  [accent]2[/accent] — [warn]dev[/warn]     тестовый (последний коммит dev)"
    )
    default_choice = "2" if default == "dev" else "1"
    choice = safe_prompt(
        f"[key]{_G_PROMPT}[/key] [title]Канал сборки сайта[/title]",
        choices=["1", "2"],
        default=default_choice,
        show_choices=False,
    )
    return "dev" if choice == "2" else "latest"


def _find_local_web_source() -> str | None:
    candidates = [
        os.path.join(PROJECT_DIR, "web-app"),
        os.path.join(os.path.dirname(PROJECT_DIR), "web-app"),
        os.path.join(os.path.expanduser("~"), "Solo_bot", "web-app"),
    ]
    for path in candidates:
        if (
            os.path.isdir(path)
            and os.path.isfile(os.path.join(path, "package.json"))
            and os.path.isfile(os.path.join(path, "Dockerfile"))
        ):
            return path
    return None


def _copy_local_web_source(src: str, dst: str) -> bool:
    subprocess.run(["rm", "-rf", dst], check=False)
    if shutil.which("rsync"):
        result = subprocess.run(
            [
                "rsync",
                "-a",
                "--exclude=node_modules",
                "--exclude=.next",
                "--exclude=.git",
                "--exclude=.env",
                "--exclude=.env.local",
                "--exclude=.env.production",
                "--exclude=logs",
                "--exclude=.deploy",
                "--exclude=.data",
                "--exclude=.claude",
                f"{src}/",
                f"{dst}/",
            ],
            check=False,
        )
        if result.returncode != 0:
            return False
    else:
        try:
            shutil.copytree(
                src,
                dst,
                ignore=shutil.ignore_patterns(
                    "node_modules",
                    ".next",
                    ".git",
                    ".env",
                    ".env.local",
                    ".env.production",
                    "logs",
                    ".deploy",
                    ".data",
                    ".claude",
                ),
            )
        except Exception:
            return False
    return os.path.isfile(os.path.join(dst, "package.json"))


def _prepare_web_sources(dst: str) -> bool:
    local = _find_local_web_source()
    if local:
        console.print(f"[accent]Найден локальный web-app: {local}[/accent]")
        if _copy_local_web_source(local, dst):
            step_ok("✓ Локальные исходники скопированы")
            return True
        step_warn("Не удалось скопировать локальные исходники.")

    step_fail("Локальные исходники web-app не найдены и не удалось использовать.")
    console.print(
        "[warn]Проверьте, что пакет ghcr.io/vladless/solo-brick публичен, либо что рядом с CLI лежит каталог web-app.[/warn]"
    )
    return False


def _pull_web_image(tag: str) -> bool:
    image = _web_image(tag)
    console.print(f"[accent]Загрузка готового образа: {image}[/accent]")
    result = subprocess.run(
        ["docker", "pull", image],
        check=False,
    )
    return result.returncode == 0


def _build_web_image(src_dir: str, tag: str) -> bool:
    if not os.path.isfile(os.path.join(src_dir, "package.json")):
        if not _prepare_web_sources(src_dir):
            return False
    if not os.path.isfile(os.path.join(src_dir, "Dockerfile")):
        step_fail("В исходниках нет Dockerfile")
        return False
    console.print("[accent]Сборка Docker-образа (несколько минут)...[/accent]")
    result = subprocess.run(
        ["docker", "build", "-t", _web_image(tag), "."],
        cwd=src_dir,
        check=False,
    )
    if result.returncode != 0:
        step_fail("Ошибка сборки. Проверьте логи выше.")
        return False
    return True


def _ensure_web_image(src_dir: str, tag: str, force_pull: bool = False) -> bool:
    if _pull_web_image(tag):
        step_ok(f"✓ Образ {_web_image(tag)} получен из GHCR")
        return True

    step_warn("Не удалось скачать образ из GHCR. Пробую локальную сборку.")
    return _build_web_image(src_dir, tag)


def _ensure_rpc_module() -> bool:
    try:
        import core.rpc  # noqa: F401

        return True
    except ImportError:
        pass
    _sync_rpc_files()
    try:
        import core.rpc  # noqa: F401

        return True
    except ImportError:
        return False


def _check_feature(name: str) -> bool:
    _ensure_rpc_module()
    try:
        from core.rpc import check_feature

        return check_feature(name)
    except Exception:
        if name == "web":
            return True
        return False


def _authorize_web_install(code: str, password: str) -> bool:
    _ensure_rpc_module()
    try:
        from core.rpc import authorize_web_install

        return authorize_web_install(code, password, out=console.print)
    except Exception:
        pass

    if os.path.exists(VENV_PYTHON):
        script = (
            "import json, re, sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "creds = json.loads(sys.stdin.read())\n"
            "from core.rpc import authorize_web_install\n"
            "def out(msg):\n"
            "    print(re.sub(r'\\[/?[a-zA-Z #0-9]+\\]', '', str(msg)), flush=True)\n"
            "ok = authorize_web_install(creds['code'], creds['password'], out=out)\n"
            "sys.exit(0 if ok else 1)\n"
        )
        try:
            result = subprocess.run(
                [VENV_PYTHON, "-c", script, PROJECT_DIR],
                input=json.dumps({"code": code, "password": password}),
                text=True,
                cwd=PROJECT_DIR,
            )
            return result.returncode == 0
        except Exception:
            pass

    step_fail("Не удалось загрузить модуль проверки лицензии")
    console.print(
        "[warn]Запустите CLI через Python 3.12, или установите бот в этой папке для использования его venv.[/warn]"
    )
    return False


def _ensure_docker():
    """Проверяет/устанавливает Docker."""
    if shutil.which("docker"):
        try:
            subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            return True
        except subprocess.CalledProcessError:
            step_warn("Docker установлен, но не запущен.")
            subprocess.run(["sudo", "systemctl", "start", "docker"], check=False)
            return True
    console.print("[accent]Установка Docker...[/accent]")
    try:
        subprocess.run("curl -fsSL https://get.docker.com | sh", shell=True, check=True)
        subprocess.run(["sudo", "systemctl", "enable", "docker"], check=False)
        subprocess.run(["sudo", "systemctl", "start", "docker"], check=False)
        return True
    except subprocess.CalledProcessError:
        step_fail("Не удалось установить Docker.")
        return False


def _port_owner(port: int) -> str | None:
    try:
        result = subprocess.run(
            ["ss", "-ltnp", f"sport = :{port}"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        out = (result.stdout or "").strip()
        if result.returncode == 0 and "LISTEN" in out:
            lines = [l for l in out.splitlines() if "LISTEN" in l]
            if lines:
                match = re.search(r'users:\(\("([^"]+)"', lines[0])
                return match.group(1) if match else "занят"
    except Exception:
        return None
    return None


def _check_http_ports_free() -> bool:
    conflicts = []
    for port in (80, 443):
        owner = _port_owner(port)
        if owner and owner != "nginx":
            conflicts.append(f"{port} → {owner}")
    if not conflicts:
        return True
    console.print(
        Panel(
            "[text]Порты HTTP/HTTPS заняты не-nginx процессом:[/text]\n"
            + "\n".join(f"  • [bold]{c}[/bold]" for c in conflicts)
            + "\n\n[text]Остановите конфликтующий процесс и повторите.[/text]",
            border_style="err",
            width=_PANEL_W,
            title="[err.bold]Порты заняты[/err.bold]",
            padding=(1, 2),
        )
    )
    return False


def _ensure_nginx():
    """Проверяет/устанавливает nginx."""
    if not _check_http_ports_free():
        return False
    if shutil.which("nginx"):
        return True
    try:
        run_with_status(["sudo", "apt-get", "update"], status_text="apt update", check=True)
        run_with_status(
            ["sudo", "apt-get", "install", "-y", "nginx"],
            status_text="Установка nginx",
            check=True,
        )
        subprocess.run(["sudo", "systemctl", "enable", "nginx"], check=False)
        subprocess.run(["sudo", "systemctl", "start", "nginx"], check=False)
        return True
    except subprocess.CalledProcessError:
        step_warn("Не удалось установить nginx автоматически.")
        return False


def _public_ip() -> str | None:
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com"):
        try:
            response = http_get(url, timeout=5)
            ip = (response.text or "").strip()
            if response.status_code == 200 and ip:
                return ip
        except Exception:
            continue
    return None


def _resolve_domain_ip(domain: str) -> str | None:
    try:
        import socket

        infos = socket.getaddrinfo(domain, None, socket.AF_INET, socket.SOCK_STREAM)
        if infos:
            return infos[0][4][0]
    except Exception:
        return None
    return None


def _dns_precheck(domain: str) -> bool:
    console.print(f"[faint]Проверяю DNS для {domain}...[/faint]")
    resolved = _resolve_domain_ip(domain)
    if not resolved:
        console.print(
            Panel(
                f"[text]DNS-имя [bold]{domain}[/bold] не резолвится в IP.[/text]\n"
                "[text]Добавьте A-запись в DNS и дождитесь пропагации (5–30 мин).[/text]",
                border_style="err",
                width=_PANEL_W,
                title="[err.bold]DNS не настроен[/err.bold]",
                padding=(1, 2),
            )
        )
        return False
    local = _public_ip()
    if local and resolved != local:
        console.print(
            Panel(
                f"[text]DNS [bold]{domain}[/bold] указывает на [warn]{resolved}[/warn],[/text]\n"
                f"[text]а этот сервер имеет IP [warn]{local}[/warn].[/text]\n\n"
                "[text]Поправьте A-запись, дождитесь пропагации и повторите.[/text]",
                border_style="err",
                width=_PANEL_W,
                title="[err.bold]DNS указывает не на этот сервер[/err.bold]",
                padding=(1, 2),
            )
        )
        return False
    step_ok(f"✓ DNS ок: {domain} → {resolved}")
    return True


def _wait_for_web_container(web_port: int, timeout_sec: int = 60) -> bool:
    import socket

    deadline = time_mod.time() + timeout_sec
    with console.status(f"[brand]Ожидание контейнера на :{web_port}...[/brand]", spinner="dots"):
        while time_mod.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", web_port), timeout=2):
                    return True
            except Exception:
                sleep(2)
    return False


def _check_bot_api_reachable(api_url: str) -> bool:
    probe = api_url.rstrip("/") + "/health"
    console.print(f"[faint]Проверяю доступность API: {probe}[/faint]")
    try:
        response = http_get(probe, timeout=5)
        if 200 <= response.status_code < 500:
            step_ok(f"✓ API отвечает ({response.status_code})")
            return True
        step_warn(f"API ответил {response.status_code}")
        return False
    except Exception as e:
        console.print(
            Panel(
                f"[text]API [bold]{api_url}[/bold] недоступен: {e}[/text]\n\n"
                f"[text]Проверьте: DNS, nginx, SSL, firewall, бот запущен.[/text]",
                border_style="err",
                width=_PANEL_W,
                title="[err.bold]Bot API недоступен[/err.bold]",
                padding=(1, 2),
            )
        )
        return False


def _web_nginx_snippet(domain: str, web_port: int) -> str:
    """Locations для веб-приложения — можно вставить в существующий server-блок."""
    return f"""    # --- Solo web-app ({domain}) ---
    client_max_body_size 100m;

    location /_next/static/ {{
        proxy_pass http://127.0.0.1:{web_port};
        proxy_cache_valid 200 365d;
        add_header Cache-Control "public, immutable, max-age=31536000";
    }}

    location = /sw.js {{
        proxy_pass http://127.0.0.1:{web_port};
        add_header Cache-Control "no-cache";
    }}

    location / {{
        proxy_pass http://127.0.0.1:{web_port};
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 90s;
    }}
    # --- /Solo web-app ---"""


def _print_manual_nginx_hint(domain: str, web_port: int) -> None:
    snippet = _web_nginx_snippet(domain, web_port)
    console.print(
        Panel(
            "[text]CLI не трогал ваш nginx. Вставьте блоки ниже в существующий\n"
            f"[accent]server {{ ... server_name {domain}; ... }}[/accent] (HTTPS-блок),\n"
            "рядом с другими [accent]location[/accent] бота, и перезагрузите nginx:\n"
            "[faint]sudo nginx -t && sudo systemctl reload nginx[/faint]",
            border_style="warn",
            width=_PANEL_W,
            title="[warn.bold]Ручная настройка nginx[/warn.bold]",
            padding=(1, 2),
        )
    )
    console.print(f"\n[faint]---8<--- snippet ---8<---[/faint]\n{snippet}\n[faint]---8<--- end ---8<---[/faint]\n")


def _nginx_domain_conflict(domain: str) -> str | None:
    """Возвращает путь конфига, в котором уже объявлен server_name = domain."""
    sites_dir = "/etc/nginx/sites-enabled"
    if not os.path.isdir(sites_dir):
        return None
    try:
        for entry in os.listdir(sites_dir):
            path = os.path.join(sites_dir, entry)
            try:
                real = os.path.realpath(path)
                with open(real) as f:
                    text = f.read()
            except Exception:
                continue
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped.startswith("server_name"):
                    continue
                names = stripped.rstrip(";").split()[1:]
                if domain in names:
                    return real
    except Exception:
        return None
    return None


def _setup_nginx(domain, web_port=3000):
    """Настраивает отдельный nginx server-блок для веб-приложения."""
    conf = f"""server {{
    listen 80;
    server_name {domain};
{_web_nginx_snippet(domain, web_port)}
}}"""
    conf_path = f"/etc/nginx/sites-available/solo-{domain}"
    enabled_path = f"/etc/nginx/sites-enabled/solo-{domain}"
    try:
        with open("/tmp/_solo_nginx.conf", "w") as f:
            f.write(conf)
        subprocess.run(["sudo", "cp", "/tmp/_solo_nginx.conf", conf_path], check=True)
        subprocess.run(["sudo", "ln", "-sf", conf_path, enabled_path], check=True)
        subprocess.run(["sudo", "nginx", "-t"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["sudo", "systemctl", "reload", "nginx"], check=True)
        return True
    except subprocess.CalledProcessError:
        step_warn("Не удалось настроить nginx.")
        return False


def _detect_proxies() -> dict:
    """Какие реверс-прокси есть на сервере и кто из них запущен."""

    def _active(svc: str) -> bool:
        try:
            r = subprocess.run(["systemctl", "is-active", svc], capture_output=True, text=True)
            return r.stdout.strip() == "active"
        except Exception:
            return False

    return {
        "nginx_installed": bool(shutil.which("nginx")) or os.path.isdir("/etc/nginx"),
        "caddy_installed": bool(shutil.which("caddy")) or os.path.isfile("/etc/caddy/Caddyfile"),
        "nginx_active": _active("nginx"),
        "caddy_active": _active("caddy"),
    }


def _web_caddy_snippet(domain: str, web_port: int) -> str:
    """Site-блок Caddy для веб-приложения. Caddy сам выпускает SSL (Let's Encrypt)."""
    return f"""{domain} {{
    encode gzip
    @solo_next path /_next/static/*
    header @solo_next Cache-Control "public, immutable, max-age=31536000"
    header /sw.js Cache-Control "no-cache"
    reverse_proxy 127.0.0.1:{web_port}
}}"""


def _caddy_domain_conflict(domain: str) -> str | None:
    """Файл Caddy, в котором домен уже объявлен как site-блок."""
    paths = []
    if os.path.isfile("/etc/caddy/Caddyfile"):
        paths.append("/etc/caddy/Caddyfile")
    conf_d = "/etc/caddy/conf.d"
    if os.path.isdir(conf_d):
        paths.extend(os.path.join(conf_d, e) for e in os.listdir(conf_d))
    for path in paths:
        try:
            with open(path) as f:
                text = f.read()
        except Exception:
            continue
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "{" not in s or "reverse_proxy" in s:
                continue
            head = s.split("{")[0]
            addrs = [a.strip().replace("https://", "").replace("http://", "") for a in head.replace(",", " ").split()]
            if domain in addrs:
                return path
    return None


def _ensure_caddy() -> bool:
    """Проверяет/устанавливает Caddy из официального репозитория."""
    if shutil.which("caddy"):
        return True
    if not _check_http_ports_free():
        return False
    try:
        run_with_status(
            [
                "sudo",
                "apt-get",
                "install",
                "-y",
                "debian-keyring",
                "debian-archive-keyring",
                "apt-transport-https",
                "curl",
                "gnupg",
            ],
            status_text="Зависимости Caddy",
            check=True,
        )
        subprocess.run(
            "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg",
            shell=True,
            check=True,
        )
        subprocess.run(
            "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list > /dev/null",
            shell=True,
            check=True,
        )
        run_with_status(["sudo", "apt-get", "update"], status_text="apt update", check=True)
        run_with_status(["sudo", "apt-get", "install", "-y", "caddy"], status_text="Установка Caddy", check=True)
        subprocess.run(["sudo", "systemctl", "enable", "caddy"], check=False)
        subprocess.run(["sudo", "systemctl", "start", "caddy"], check=False)
        return True
    except subprocess.CalledProcessError:
        step_warn("Не удалось установить Caddy автоматически.")
        return False


def _setup_caddy(domain, web_port=3000) -> bool:
    """Добавляет site-блок Caddy (авто-SSL), не трогая остальной Caddyfile."""
    caddyfile = "/etc/caddy/Caddyfile"
    snippet = _web_caddy_snippet(domain, int(web_port))
    try:
        subprocess.run(["sudo", "mkdir", "-p", "/etc/caddy"], check=True)
        if not os.path.isfile(caddyfile):
            subprocess.run(["sudo", "touch", caddyfile], check=True)
        with open("/tmp/_solo_caddy.conf", "w") as f:
            f.write(f"\n# --- Solo web-app ({domain}) ---\n{snippet}\n")
        subprocess.run(["sudo", "bash", "-c", f"cat /tmp/_solo_caddy.conf >> {caddyfile}"], check=True)
        subprocess.run(
            ["sudo", "caddy", "validate", "--adapter", "caddyfile", "--config", caddyfile],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(["sudo", "systemctl", "reload", "caddy"], check=True)
        return True
    except subprocess.CalledProcessError:
        console.print(
            "[warn]Не удалось настроить Caddy (проверьте: sudo caddy validate --config /etc/caddy/Caddyfile).[/warn]"
        )
        return False


def _print_manual_caddy_hint(domain: str, web_port: int) -> None:
    snippet = _web_caddy_snippet(domain, int(web_port))
    console.print(
        Panel(
            "[text]CLI не трогал ваш Caddy. Добавьте site-блок ниже в [accent]/etc/caddy/Caddyfile[/accent]\n"
            "(или в свой conf.d) и перезагрузите Caddy:\n"
            "[faint]sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy[/faint]\n"
            "[faint]Caddy выпустит SSL автоматически — certbot не нужен.[/faint]",
            border_style="warn",
            width=_PANEL_W,
            title="[warn.bold]Ручная настройка Caddy[/warn.bold]",
            padding=(1, 2),
        )
    )
    console.print(f"\n[faint]---8<--- Caddyfile ---8<---[/faint]\n{snippet}\n[faint]---8<--- end ---8<---[/faint]\n")


def _read_config_port(key: str, default: int) -> int:
    try:
        text = open(resolve_config_path(PROJECT_DIR), encoding="utf-8").read()
        m = re.search(rf"^{key}\s*=\s*(\d+)", text, re.M)
        return int(m.group(1)) if m else default
    except Exception:
        return default


def _fetch_bot_proxy_conf(kind: str, host: str, bot_port: int) -> str | None:
    try:
        from core import rpc as _rpc

        fetch = getattr(_rpc, "fetch_bot_proxy_template", None)
        if fetch is None:
            return None
        template = fetch(PROJECT_DIR, kind)
    except Exception:
        return None
    if not template:
        return None
    sub_path = _read_config_str("SUB_PATH") or "/sub/"
    webhook_path = _read_config_str("WEBHOOK_PATH") or "/webhook"
    return (
        template.replace("{DOMAIN}", host)
        .replace("{BOT_PORT}", str(bot_port))
        .replace("{SUB_PATH}", sub_path)
        .replace("{WEBHOOK_PATH}", webhook_path)
    )


def _setup_bot_nginx(domain: str, conf: str) -> bool:
    conf_path = f"/etc/nginx/sites-available/solo-bot-{domain}"
    enabled_path = f"/etc/nginx/sites-enabled/solo-bot-{domain}"
    try:
        with open("/tmp/_solo_bot_nginx.conf", "w") as f:
            f.write(conf)
        subprocess.run(["sudo", "cp", "/tmp/_solo_bot_nginx.conf", conf_path], check=True)
        subprocess.run(["sudo", "ln", "-sf", conf_path, enabled_path], check=True)
        subprocess.run(["sudo", "nginx", "-t"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["sudo", "systemctl", "reload", "nginx"], check=True)
        return True
    except subprocess.CalledProcessError:
        step_warn("Не удалось настроить nginx для бота.")
        return False


def _setup_bot_caddy(domain: str, block: str) -> bool:
    caddyfile = "/etc/caddy/Caddyfile"
    try:
        subprocess.run(["sudo", "mkdir", "-p", "/etc/caddy"], check=True)
        if not os.path.isfile(caddyfile):
            subprocess.run(["sudo", "touch", caddyfile], check=True)
        with open("/tmp/_solo_bot_caddy.conf", "w") as f:
            f.write(f"\n# --- SoloBot ({domain}) ---\n{block}\n")
        subprocess.run(["sudo", "bash", "-c", f"cat /tmp/_solo_bot_caddy.conf >> {caddyfile}"], check=True)
        subprocess.run(
            ["sudo", "caddy", "validate", "--adapter", "caddyfile", "--config", caddyfile],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(["sudo", "systemctl", "reload", "caddy"], check=True)
        return True
    except subprocess.CalledProcessError:
        step_warn("Не удалось настроить Caddy для бота.")
        return False


def _print_manual_bot_proxy_hint(conf: str, prefer_caddy: bool) -> None:
    if prefer_caddy:
        head = (
            "[text]Добавьте блок ниже в [accent]/etc/caddy/Caddyfile[/accent] и перезагрузите Caddy:[/text]\n"
            "[faint]sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy[/faint]\n"
            "[faint]SSL Caddy выпустит сам — certbot не нужен.[/faint]"
        )
    else:
        head = (
            "[text]Сохраните конфиг ниже в [accent]/etc/nginx/sites-available/[/accent], включите его "
            "и выпустите сертификат:[/text]\n"
            "[faint]sudo nginx -t && sudo systemctl reload nginx && sudo certbot --nginx -d ВАШ_ДОМЕН[/faint]"
        )
    console.print(
        Panel(
            head,
            border_style="warn",
            width=_PANEL_W,
            title="[warn.bold]HTTPS для бота — ручная настройка[/warn.bold]",
            padding=(1, 2),
        )
    )
    console.print(f"[faint]---8<---[/faint]\n{conf}\n[faint]---8<---[/faint]")


def setup_bot_https(domain: str) -> None:
    host = (domain or "").replace("https://", "").replace("http://", "").strip("/ ")
    if not host:
        step_warn("Домен не указан — пропускаю настройку HTTPS. Без него бот не получит сообщения из Telegram.")
        return
    bot_port = _read_config_port("WEBAPP_PORT", 3001)
    px = _detect_proxies()
    if px["nginx_active"] and px["caddy_active"]:
        console.print(
            "[warn]Одновременно запущены nginx и Caddy — они конфликтуют за порты 80/443. "
            "Выберите один и остановите второй.[/warn]"
        )

    opts = [
        ("caddy", "Caddy — сертификат выпускается сам, самый простой вариант"
         + (" (уже установлен)" if px["caddy_installed"] else " — установлю автоматически")),
        ("nginx", "Nginx + сертификат Let's Encrypt (certbot)"
         + (" (уже установлен)" if px["nginx_installed"] else " — установлю автоматически")),
        ("skip", "Пропустить — прокси уже настроен или настрою вручную"),
    ]
    if px["nginx_active"] and not px["caddy_active"]:
        default_idx = 2
    elif px["caddy_active"]:
        default_idx = 1
    else:
        default_idx = 1
    console.print("[accent]Как настроить HTTPS для бота:[/accent]")
    for i, (_, label) in enumerate(opts, 1):
        console.print(f"  {i}. {label}")
    sel = safe_prompt(
        "Выбор", choices=[str(i) for i in range(1, len(opts) + 1)], default=str(default_idx), show_choices=False
    )
    choice = opts[int(sel) - 1][0]

    if choice == "skip":
        step_warn(f"Пропущено. Бот слушает 127.0.0.1:{bot_port} — направьте на него HTTPS-домен {host}.")
        return

    conf = _fetch_bot_proxy_conf(choice, host, bot_port)
    if not conf:
        step_warn(
            "Не удалось получить шаблон прокси с сайта (нет сети или сессия CLI устарела). "
            f"Настройте прокси вручную по вики: {WIKI_URL}"
        )
        return

    if choice == "caddy":
        conflict = _caddy_domain_conflict(host)
        if conflict:
            step_warn(f"Домен {host} уже есть в Caddy ({conflict}) — считаю, что прокси настроен.")
            return
        if _ensure_caddy() and _setup_bot_caddy(host, conf):
            step_ok(f"Caddy настроен: https://{host} → бот. SSL выпустится автоматически.")
        else:
            _print_manual_bot_proxy_hint(conf, prefer_caddy=True)
        return

    conflict = _nginx_domain_conflict(host)
    if conflict:
        step_warn(f"Домен {host} уже есть в nginx ({conflict}) — считаю, что прокси настроен.")
        return
    if _ensure_nginx() and _setup_bot_nginx(host, conf):
        step_ok(f"nginx настроен: {host} → бот.")
        if _setup_ssl(host):
            step_ok(f"SSL выпущен: https://{host} работает.")
        else:
            step_warn(f"SSL пока не выпущен. После настройки DNS: sudo certbot --nginx -d {host}")
    else:
        _print_manual_bot_proxy_hint(conf, prefer_caddy=False)


def _ensure_certbot_nginx() -> bool:
    if not shutil.which("certbot"):
        try:
            run_with_status(
                ["sudo", "apt-get", "install", "-y", "certbot", "python3-certbot-nginx"],
                status_text="Установка certbot и плагина nginx",
                check=True,
            )
            return True
        except subprocess.CalledProcessError:
            step_warn("Не удалось установить certbot.")
            return False

    plugins = subprocess.run(["sudo", "certbot", "plugins"], capture_output=True, text=True)
    if "nginx" in (plugins.stdout + plugins.stderr):
        return True

    step_warn("certbot есть, но плагин для nginx не установлен. Ставлю плагин...")
    try:
        run_with_status(
            ["sudo", "apt-get", "install", "-y", "python3-certbot-nginx"],
            status_text="Установка плагина certbot-nginx",
            check=True,
        )
        return True
    except subprocess.CalledProcessError:
        console.print(
            "[warn]Не удалось установить плагин. Установите вручную: "
            "sudo apt-get install -y python3-certbot-nginx[/warn]"
        )
        return False


def _setup_ssl(domain):
    """Получает SSL сертификат через certbot."""
    if not _dns_precheck(domain):
        return False
    if not _ensure_certbot_nginx():
        return False
    try:
        subprocess.run(
            [
                "sudo",
                "certbot",
                "--nginx",
                "-d",
                domain,
                "--non-interactive",
                "--agree-tos",
                "--register-unsafely-without-email",
                "--redirect",
            ],
            check=True,
        )
        return True
    except subprocess.CalledProcessError:
        console.print(
            Panel(
                f"[text]Сертификат не удалось выпустить. Причина обычно —[/text]\n"
                f"[text]DNS [bold]{domain}[/bold] ещё не указывает на сервер, либо порт 80/443 закрыт.[/text]\n\n"
                f"[warn]Сайт без SSL открывать нельзя.[/warn] После пропагации DNS:\n"
                f"  1. [bold]dig +short {domain}[/bold]\n"
                f"  2. [bold]sudo certbot --nginx -d {domain}[/bold]",
                border_style="warn",
                width=_PANEL_W,
                title="[warn.bold]SSL отложен[/warn.bold]",
                padding=(1, 2),
            )
        )
        return False


def install_website():
    """Устанавливает веб-приложение (сайт) через Docker."""
    if not _check_feature("web"):
        step_warn("Эта функция недоступна в текущей версии. Обновите бота.")
        return

    show_website_version_banner()
    console.print(
        Panel(
            "[text]CLI установит Docker, скачает готовый образ сайта, настроит nginx и SSL.\n"
            "Бэкенд (бот) может быть на этом же сервере или на другом.[/text]",
            border_style="ok",
            width=_PANEL_W,
            title="[ok.bold]Установка веб-приложения[/ok.bold]",
            padding=(1, 2),
        )
    )

    console.print(
        Panel(
            "[brand]Вариант A:[/brand] Бот и сайт на одном сервере\n"
            "  → API вызывается локально внутри сервера\n\n"
            "[brand]Вариант B:[/brand] Сайт на отдельном сервере\n"
            "  → API вызывается по домену (например api.example.com)\n"
            "  → На сервере бота должен быть nginx+SSL перед API и открыт порт 443",
            border_style="dim",
            width=_PANEL_W,
            title="[faint]Варианты размещения[/faint]",
            padding=(1, 2),
        )
    )

    if not safe_confirm("Начать установку сайта?", default=True):
        return

    step_rule(0, 5, "Авторизация")
    console.print("[faint]Введите логин и пароль от вашего кабинета на сайте Solo.[/faint]")
    console.print("[faint]Данные используются только для проверки лицензии и нигде не сохраняются.[/faint]\n")

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        lc_code = safe_prompt("[accent]Логин (Client Code)[/accent]")
        if not lc_code or not lc_code.strip():
            step_fail("Логин обязателен.")
            return
        try:
            import getpass

            lc_pass = getpass.getpass("  Пароль: ")
        except Exception:
            lc_pass = safe_prompt("[accent]Пароль[/accent]")
        if not lc_pass or not lc_pass.strip():
            step_fail("Пароль обязателен.")
            return

        ok = _authorize_web_install(lc_code.strip(), lc_pass.strip())
        lc_code = None
        lc_pass = None
        if ok:
            break
        if attempt < max_attempts:
            step_warn(f"Попытка {attempt}/{max_attempts} не прошла.")
            if not safe_confirm("Повторить ввод?", default=True):
                return
        else:
            console.print(
                "[err]Исчерпаны попытки авторизации. Проверьте логин/пароль на сайте Solo и повторите установку.[/err]"
            )
            return

    step_rule(1, 5, "Docker")
    if not _ensure_docker():
        return

    step_rule(2, 5, "Настройки")

    console.print(
        "[faint]Домен, по которому будет открываться сайт.\nDNS (A-запись) должна уже указывать на IP этого сервера.[/faint]"
    )
    domain = safe_prompt("[accent]Домен сайта[/accent] (например vpn.example.com)")
    if not domain or not domain.strip():
        step_fail("Домен обязателен.")
        return
    domain = domain.strip()

    try:
        from settings.config import API_PORT as _BOT_API_PORT

        _bot_api_port = int(_BOT_API_PORT)
    except Exception:
        _bot_api_port = 3004

    console.print("\n[faint]Где запущен бот?[/faint]")
    bot_location = safe_prompt(
        "[accent]Размещение бота[/accent]: [1] на этом же сервере  [2] на другом сервере",
        choices=["1", "2"],
        default="1",
        show_choices=False,
    )
    api_domain = ""
    if bot_location == "1":
        api_url = f"http://host.docker.internal:{_bot_api_port}"
        console.print(
            Panel(
                f"[text]API: [bold]{api_url}[/bold] (через docker host-gateway)[/text]\n\n"
                f"[faint]Требования к боту на этом сервере:[/faint]\n"
                f"  • Бот запущен на хосте и слушает [bold]0.0.0.0:{_bot_api_port}[/bold]\n"
                f'  • В config.py: [bold]API_HOST="0.0.0.0"[/bold], [bold]API_PORT={_bot_api_port}[/bold]',
                border_style="dim",
                width=_PANEL_W,
                title="[faint]Размещение: один сервер[/faint]",
                padding=(1, 2),
            )
        )
    else:
        console.print(
            "\n[faint]Домен, по которому web-контейнер будет ходить на API бота.\nНа сервере бота должен стоять nginx+SSL перед портом API.[/faint]"
        )
        api_domain = safe_prompt("[accent]Домен API бота[/accent] (например api.example.com)")
        if not api_domain or not api_domain.strip():
            step_fail("Домен API обязателен.")
            return
        api_domain = api_domain.strip().replace("https://", "").replace("http://", "").strip("/")
        api_url = f"https://{api_domain}"
        console.print(
            Panel(
                f"[text]API: [bold]{api_url}[/bold][/text]\n\n"
                f"[warn]На сервере бота настройте:[/warn]\n"
                f"  • nginx: [bold]https://{api_domain}[/bold] → [bold]http://127.0.0.1:{_bot_api_port}[/bold]\n"
                f"  • SSL сертификат (certbot --nginx -d {api_domain})\n"
                f'  • config.py: [bold]API_HOST="0.0.0.0"[/bold], [bold]API_PORT={_bot_api_port}[/bold]\n'
                f"  • Опционально firewall: порт {_bot_api_port} открыт только с IP web-сервера",
                border_style="warn",
                width=_PANEL_W,
                title="[warn.bold]Размещение: разные серверы[/warn.bold]",
                padding=(1, 2),
            )
        )
        if not safe_confirm("Всё настроено на сервере бота?", default=True):
            step_warn("Настройте сервер бота и повторите установку.")
            return
        if not _check_bot_api_reachable(api_url):
            if not safe_confirm(
                "[warn]API недоступен. Продолжить всё равно (сайт не заработает без API)?[/warn]",
                default=False,
            ):
                return

    console.print(
        "\n[faint]Внутренний порт, на котором запустится сайт.\nNginx проксирует на него запросы. Менять нужно только если порт занят.[/faint]"
    )
    web_port = safe_prompt("[accent]Порт сайта[/accent]", default="3000")

    console.print(
        "\n[faint]Для push-уведомлений на сайте (колокольчик).\nМожно сгенерировать ключи прямо сейчас (приватный ключ печатается — сохраните его).\nЕсли push не нужны — пропустите.[/faint]"
    )
    vapid_key = ""
    vapid_action = safe_prompt(
        "[accent]VAPID ключи[/accent]: [1] сгенерировать  [2] ввести публичный ключ вручную  [3] пропустить",
        choices=["1", "2", "3"],
        default="1",
        show_choices=False,
    )
    if vapid_action == "1":
        pair = _generate_vapid_keys()
        if pair is None:
            console.print("[warn]Не удалось сгенерировать (нет cryptography). Введите вручную или пропустите.[/warn]")
            vapid_key = safe_prompt("[accent]VAPID Public Key[/accent] (Enter — пропустить)", default="")
        else:
            vapid_pub, vapid_priv = pair
            vapid_key = vapid_pub
            vapid_file = os.path.expanduser(f"~/.solobot_vapid_{domain}.txt")
            py_snippet = (
                f'VAPID_PUBLIC_KEY = "{vapid_pub}"\n'
                f'VAPID_PRIVATE_KEY = "{vapid_priv}"\n'
                f'VAPID_CLAIMS_EMAIL = "mailto:admin@{domain}"\n'
            )
            vapid_saved = True
            try:
                with open(vapid_file, "w", encoding="utf-8") as f:
                    f.write(
                        f"# VAPID keypair for {domain}\n"
                        f"# Сгенерировано: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"# Вставьте эти строки КАК ЕСТЬ в config.py бота и перезапустите.\n\n" + py_snippet
                    )
                os.chmod(vapid_file, 0o600)
            except Exception:
                vapid_saved = False
            saved_hint = (
                f"[ok]✓ Ключи сохранены в файл:[/ok] [bold]{vapid_file}[/bold] [faint](chmod 600)[/faint]"
                if vapid_saved
                else "[err]Не удалось записать файл — скопируйте строки ниже СЕЙЧАС.[/err]"
            )
            console.print("\n[warn.bold]VAPID keypair[/warn.bold]")
            console.print(saved_hint)
            console.print("[faint]Скопируйте строки ниже КАК ЕСТЬ (с кавычками) в config.py бота:[/faint]\n")
            console.print(py_snippet)
            console.print(
                "[warn]Публичный ключ CLI пропишет в web .env автоматически.\n"
                "Приватный ключ и email добавьте в config.py бота и перезапустите.[/warn]\n"
            )
    elif vapid_action == "2":
        vapid_key = safe_prompt("[accent]VAPID Public Key[/accent]", default="")

    console.print(
        "\n[faint]Cloudflare Turnstile защищает формы логина от ботов.\nПолучите ключ на dash.cloudflare.com → Turnstile.\nЕсли не нужно — пропустите, формы будут работать без CAPTCHA.[/faint]"
    )
    turnstile_key = safe_prompt("[accent]Turnstile Site Key[/accent] (Enter — пропустить)", default="")

    console.print(
        "\n[faint]Username Telegram-бота (без @) для кнопки «Войти через Telegram» на сайте.\nЕсли не нужно — пропустите.[/faint]"
    )
    tg_bot_username = safe_prompt("[accent]Telegram Bot Username[/accent] (Enter — пропустить)", default="")

    console.print(
        "\n[faint]Для отправки email-кодов (логин, подтверждение, сброс пароля).\nЕсли не нужно — пропустите, регистрация по email+паролю будет работать без этого.[/faint]"
    )
    smtp_host = safe_prompt("[accent]SMTP Host[/accent] (Enter — пропустить)", default="")
    smtp_user = ""
    smtp_password = ""
    smtp_from = ""
    if smtp_host:
        smtp_user = safe_prompt("[accent]SMTP User[/accent]", default="")
        try:
            import getpass

            smtp_password = getpass.getpass("  SMTP Password: ")
        except Exception:
            smtp_password = safe_prompt("[accent]SMTP Password[/accent]", default="")
        smtp_from = safe_prompt("[accent]Email From[/accent]", default=smtp_user)

    web_tag = _ask_web_tag(default=_get_saved_web_tag())

    setup_ssl = safe_confirm("Установить SSL (Let's Encrypt)?", default=True)

    site_url = f"https://{domain}" if setup_ssl else f"http://{domain}"

    console.print(f"\n  Домен:   [ok]{domain}[/ok]")
    console.print(f"  Backend: [ok]{api_url}[/ok]")
    console.print(f"  Канал:   [ok]{web_tag}[/ok]")
    console.print(f"  SSL:     [ok]{'Да' if setup_ssl else 'Нет'}[/ok]")

    if not safe_confirm("\n[warn]Всё верно?[/warn]", default=True):
        return

    step_rule(3, 5, "Запуск сайта")
    os.makedirs(WEB_DIR, exist_ok=True)

    from urllib.parse import urlparse

    parsed_api = urlparse(api_url)
    api_port_from_url = ""
    if parsed_api.port is not None:
        api_port_from_url = str(parsed_api.port)
    elif parsed_api.scheme == "https":
        api_port_from_url = "443"
    elif parsed_api.scheme == "http":
        api_port_from_url = "80"

    env_path = os.path.join(WEB_DIR, ".env")
    plugin_builder_token, plugin_builder_token_is_new = _ensure_plugin_builder_token(env_path)
    with open(env_path, "w") as f:
        f.write(f"API_URL={api_url}\n")
        f.write(f"API_BASE_URL={api_url}\n")
        f.write(f"NEXT_PUBLIC_API_URL={api_url}\n")
        f.write(f"NEXT_PUBLIC_API_BASE_URL={api_url}\n")
        f.write(f"NEXT_PUBLIC_API_PORT={api_port_from_url}\n")
        f.write(f"NEXT_PUBLIC_SITE_URL={site_url}\n")
        f.write(f"NEXT_PUBLIC_VAPID_PUBLIC_KEY={vapid_key}\n")
        f.write(f"NEXT_PUBLIC_TURNSTILE_SITE_KEY={turnstile_key}\n")
        f.write("NEXT_PUBLIC_LOG_LEVEL=info\n")
        f.write(f"WEB_PORT={web_port}\n")
        f.write(f"PLUGIN_BUILDER_TOKEN={plugin_builder_token}\n")
        if tg_bot_username:
            f.write(f"NEXT_PUBLIC_TELEGRAM_BOT_USERNAME={tg_bot_username}\n")
        if smtp_host:
            f.write(f"EMAIL_SMTP_HOST={smtp_host}\n")
            f.write("EMAIL_SMTP_PORT=465\n")
            f.write(f"EMAIL_SMTP_USER={smtp_user}\n")
            f.write(f"EMAIL_SMTP_PASSWORD={smtp_password}\n")
            f.write(f"EMAIL_FROM={smtp_from}\n")

    if plugin_builder_token_is_new:
        console.print(
            Panel(
                f"[bold]PLUGIN_BUILDER_TOKEN[/bold] = {plugin_builder_token}\n\n"
                "[warn]Токен защищает plugin-builder API от посторонних.\n"
                "Сохраните, если планируете использовать внешний билд-воркер для custom-elements —\n"
                "воркер должен слать этот же токен в заголовке Authorization: Bearer <token>.[/warn]",
                border_style="warn",
                width=_PANEL_W,
                title="[warn.bold]PLUGIN_BUILDER_TOKEN — сгенерирован[/warn.bold]",
                padding=(1, 2),
            )
        )

    src_dir = os.path.join(WEB_DIR, "src")
    if not _ensure_web_image(src_dir, web_tag):
        return
    _save_web_tag(web_tag)

    compose_path = os.path.join(WEB_DIR, "docker-compose.yml")
    with open(compose_path, "w") as f:
        f.write(f"""name: {WEB_CONTAINER_NAME}

services:
  web:
    image: {_web_image(web_tag)}
    container_name: {WEB_CONTAINER_NAME}
    ports:
      - "127.0.0.1:{web_port}:3000"
    env_file:
      - .env
    restart: unless-stopped
    extra_hosts:
      - "host.docker.internal:host-gateway"
    healthcheck:
      test: ["CMD", "node", "-e", "fetch('http://127.0.0.1:3000/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
    volumes:
      - ./logs:/app/logs
""")

    _ensure_web_logs_dir()
    console.print("[accent]Запуск контейнера...[/accent]")
    subprocess.run(["docker", "compose", "up", "-d"], cwd=WEB_DIR, check=True)

    if _wait_for_web_container(int(web_port), timeout_sec=60):
        step_ok(f"Контейнер запущен и отвечает на порту {web_port}")
    else:
        console.print(
            Panel(
                f"[text]Контейнер запущен, но не отвечает на http://127.0.0.1:{web_port} за 60 сек.[/text]\n"
                f"[text]Проверьте логи:[/text]\n"
                f"  [bold]cd {WEB_DIR} && docker compose logs -f[/bold]",
                border_style="warn",
                width=_PANEL_W,
                title="[warn.bold]Healthcheck не прошёл[/warn.bold]",
                padding=(1, 2),
            )
        )

    step_rule(4, 5, "Reverse-proxy")
    px = _detect_proxies()
    if px["nginx_active"] and px["caddy_active"]:
        console.print(
            "[warn]Одновременно запущены nginx и Caddy — они конфликтуют за порты 80/443.\n"
            "  80/443 может слушать только один. Выберите владельца и при необходимости остановите второй.[/warn]"
        )
    elif px["nginx_installed"] and px["caddy_installed"]:
        console.print("[faint]На сервере есть и nginx, и Caddy.[/faint]")

    opts = [
        ("nginx", "nginx" + (" (установлен)" if px["nginx_installed"] else " — установить")),
        ("caddy", "Caddy, авто-SSL" + (" (установлен)" if px["caddy_installed"] else " — установить")),
        ("manual", "Вручную (показать конфиг)"),
    ]
    default_idx = 2 if (px["caddy_active"] and not px["nginx_active"]) else 1
    console.print("[accent]Чем настроить домен сайта:[/accent]")
    for i, (_, label) in enumerate(opts, 1):
        console.print(f"  {i}. {label}")
    sel = safe_prompt(
        "Выбор", choices=[str(i) for i in range(1, len(opts) + 1)], default=str(default_idx), show_choices=False
    )
    proxy = opts[int(sel) - 1][0]

    proxy_kind = None
    ssl_deferred = False

    if proxy == "nginx":
        conflict_path = _nginx_domain_conflict(domain)
        if conflict_path:
            console.print(
                f"[warn]На домене [bold]{domain}[/bold] уже есть nginx-конфиг:[/warn] {conflict_path}\n"
                "[warn]Автонастройка создала бы второй server-блок.[/warn]"
            )
            do_auto = safe_confirm("Всё равно создать отдельный server-блок?", default=False)
        else:
            do_auto = True
        if do_auto and _ensure_nginx() and _setup_nginx(domain, int(web_port)):
            step_ok(f"nginx настроен для {domain}")
            proxy_kind = "nginx"
        else:
            _print_manual_nginx_hint(domain, int(web_port))
    elif proxy == "caddy":
        conflict_path = _caddy_domain_conflict(domain)
        if conflict_path:
            console.print(
                f"[warn]Домен [bold]{domain}[/bold] уже есть в Caddy: {conflict_path}. Покажу конфиг для ручной правки.[/warn]"
            )
            _print_manual_caddy_hint(domain, int(web_port))
        elif _ensure_caddy() and _setup_caddy(domain, int(web_port)):
            step_ok(f"Caddy настроен для {domain} (SSL автоматический)")
            proxy_kind = "caddy"
        else:
            _print_manual_caddy_hint(domain, int(web_port))
    else:
        if px["caddy_installed"] and not px["nginx_installed"]:
            _print_manual_caddy_hint(domain, int(web_port))
        else:
            _print_manual_nginx_hint(domain, int(web_port))

    step_rule(5, 5, "SSL")
    if proxy_kind == "caddy":
        console.print(
            "[ok]SSL выпустит Caddy автоматически (Let's Encrypt) при первом запросе — certbot не нужен.[/ok]"
        )
        console.print(f"[faint]Условие: DNS [bold]{domain}[/bold] указывает на сервер и порты 80/443 открыты.[/faint]")
        site_url = f"https://{domain}"
    elif proxy_kind == "nginx":
        if setup_ssl:
            if _setup_ssl(domain):
                step_ok("SSL сертификат установлен")
                site_url = f"https://{domain}"
            else:
                ssl_deferred = True
        else:
            console.print("[faint]SSL пропущен[/faint]")
    else:
        if setup_ssl:
            step_warn("SSL отложен: сначала настройте прокси (конфиг показан выше).")
            console.print(f"[faint]nginx: sudo certbot --nginx -d {domain} · Caddy выпускает SSL сам[/faint]")
            ssl_deferred = True
        else:
            console.print("[faint]SSL пропущен[/faint]")

    smtp_hint = ""
    if not smtp_host:
        smtp_hint = "\n\n[warn]SMTP не настроен — вход по email-коду и сброс пароля не будут работать.\n  Настройте позже через: меню → Управление сайтом → Изменить настройки[/warn]"

    bot_note = (
        f"\n\n[warn]На сервере бота установите в [bold]config.py[/bold]:[/warn]\n"
        f'  SITE_URL = "{site_url}"\n'
        f"[faint]  (используется для TG WebApp-кнопок и gift-ссылок)[/faint]\n"
        f"[faint]  После правки перезапустите бота.[/faint]"
    )

    if ssl_deferred:
        header = (
            f"[warn.bold]Сайт собран, но SSL ещё не получен.[/warn.bold]\n"
            f"[text]Откроется по [bold]{site_url}[/bold] только после выпуска сертификата.[/text]\n\n"
            f"[accent]Что сделать:[/accent]\n"
            f"  1. [bold]dig +short {domain}[/bold] — должен вернуть IP этого сервера\n"
            f"  2. [bold]sudo certbot --nginx -d {domain}[/bold]"
        )
        border = "yellow"
        title = "[warn.bold]Установка почти завершена[/warn.bold]"
    else:
        header = f"[ok.bold]Сайт доступен: {site_url}[/ok.bold]"
        border = "green"
        title = "[ok.bold]Установка завершена[/ok.bold]"

    console.print(
        Panel(
            f"{header}{smtp_hint}{bot_note}\n\n"
            f"[text]Управление:[/text]\n"
            f"  cd {WEB_DIR}\n"
            f"  docker compose logs -f       [faint]— логи[/faint]\n"
            f"  docker compose restart       [faint]— перезапуск[/faint]\n"
            f"  docker compose down          [faint]— остановка[/faint]\n"
            f"  nano .env                    [faint]— настройки[/faint]",
            border_style=border,
            title=title,
            padding=(1, 2),
        )
    )


def _read_env_domain() -> str | None:
    env_path = os.path.join(WEB_DIR, ".env")
    if not os.path.isfile(env_path):
        return None
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("NEXT_PUBLIC_SITE_URL="):
                    url = line.split("=", 1)[1].strip()
                    return url.replace("https://", "").replace("http://", "").strip("/") or None
    except Exception:
        return None
    return None


def _web_container_status() -> str:
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "--format", "{{.State}}"],
            cwd=WEB_DIR,
            capture_output=True,
            text=True,
            timeout=5,
        )
        states = [s.strip() for s in (result.stdout or "").splitlines() if s.strip()]
        if not states:
            return "[faint]не запущен[/faint]"
        running = sum(1 for s in states if s.lower() == "running")
        total = len(states)
        if running == total:
            return f"[ok]running ({running}/{total})[/ok]"
        return f"[warn]{running}/{total} running[/warn]"
    except Exception:
        return "[faint]статус неизвестен[/faint]"


def uninstall_website():
    if not os.path.exists(WEB_DIR):
        step_warn("Сайт не установлен (папка отсутствует).")
        return

    domain = _read_env_domain()
    console.print(
        Panel(
            f"[err.bold]Вы собираетесь полностью удалить сайт.[/err.bold]\n\n"
            f"[text]Будет удалено:[/text]\n"
            f"  • Docker-контейнеры и volumes (данные кабинета)\n"
            f"  • Docker-образ {_web_image(_get_saved_web_tag())}\n"
            f"  • Папка проекта [bold]{WEB_DIR}[/bold] (.env, логи)\n"
            + (f"  • Nginx-конфиг [bold]/etc/nginx/sites-*/solo-{domain}[/bold]\n" if domain else "")
            + (f"  • SSL-сертификат для [bold]{domain}[/bold]\n" if domain else "")
            + "\n[warn]Действие необратимо. Рекомендуется сделать бэкап БД заранее.[/warn]",
            border_style="err",
            width=_PANEL_W,
            title="[err.bold]Удаление сайта[/err.bold]",
            padding=(1, 2),
        )
    )

    if not safe_confirm("Продолжить удаление?", default=False):
        return
    confirm_text = safe_prompt(
        "[err]Введите [bold]DELETE[/bold] заглавными чтобы подтвердить[/err]",
        default="",
    )
    if confirm_text.strip() != "DELETE":
        step_warn("Удаление отменено.")
        return

    if os.path.exists(os.path.join(WEB_DIR, "docker-compose.yml")):
        run_with_status(
            ["docker", "compose", "down", "-v", "--remove-orphans"],
            status_text="Остановка и удаление контейнеров",
            cwd=WEB_DIR,
        )

    try:
        tag = _get_saved_web_tag()
        run_with_status(
            ["docker", "image", "rm", "-f", _web_image(tag)],
            status_text=f"Удаление образа {_web_image(tag)}",
        )
    except Exception:
        pass

    if domain:
        for path in (
            f"/etc/nginx/sites-enabled/solo-{domain}",
            f"/etc/nginx/sites-available/solo-{domain}",
        ):
            subprocess.run(["sudo", "rm", "-f", path], check=False)
        subprocess.run(["sudo", "systemctl", "reload", "nginx"], check=False)

        if shutil.which("certbot"):
            subprocess.run(
                ["sudo", "certbot", "delete", "--non-interactive", "--cert-name", domain],
                check=False,
            )

    subprocess.run(["sudo", "rm", "-rf", WEB_DIR], check=False)

    if domain:
        vapid_file = os.path.expanduser(f"~/.solobot_vapid_{domain}.txt")
        if os.path.exists(vapid_file):
            try:
                os.remove(vapid_file)
            except Exception:
                pass

    step_ok("Сайт удалён.")


def manage_website():
    """Меню управления сайтом."""
    if not _check_feature("web"):
        step_warn("Эта функция недоступна в текущей версии. Обновите бота.")
        return
    heading("Веб-сайт", WEB_DIR)
    show_website_version_banner()
    if not os.path.exists(os.path.join(WEB_DIR, "docker-compose.yml")):
        step_warn("Сайт не установлен")
        if safe_confirm("Установить сейчас?", default=True):
            install_website()
        return

    tag = _get_saved_web_tag()
    console.print(
        f"  [faint]образ[/faint]  [text]{_web_image(tag)}[/text]"
        f"  [faint]{_G_DOT}[/faint]  [faint]статус[/faint] {_web_container_status()}"
    )

    menu(
        "Управление сайтом",
        [
            (
                "Работа",
                [
                    ("1", "◉", "Статус контейнера", True, ""),
                    ("2", "≡", "Логи", True, ""),
                    ("3", "⟳", "Перезапустить", True, ""),
                    ("4", "■", "Остановить", True, ""),
                ],
            ),
            (
                "Обслуживание",
                [
                    ("5", "↑", "Обновить: пересборка и перезапуск", True, ""),
                    ("6", "⚙", "Изменить настройки (.env)", True, ""),
                    ("7", "≡", "Показать .env", True, ""),
                    ("8", "⭯", "Переустановить", True, ""),
                    ("9", "✕", "Удалить сайт", True, ""),
                ],
            ),
            (
                "",
                [
                    ("10", "←", "Назад", True, ""),
                ],
            ),
        ],
        subtitle=tag,
    )

    choice = ask_choice(10, "Действие")

    if choice == "1":
        subprocess.run(["docker", "compose", "ps"], cwd=WEB_DIR)
    elif choice == "2":
        step_info("Живой поток логов. Выход — Ctrl+C.")
        try:
            subprocess.run(["docker", "compose", "logs", "--tail", "80", "-f"], cwd=WEB_DIR)
        except KeyboardInterrupt:
            pass
        console.print()
        step_ok("Просмотр логов завершён.")
    elif choice == "3":
        subprocess.run(["docker", "compose", "restart"], cwd=WEB_DIR)
        step_ok("Перезапущено")
    elif choice == "4":
        subprocess.run(["docker", "compose", "down"], cwd=WEB_DIR)
        step_warn("Сайт остановлен")
    elif choice == "5":
        src_dir = os.path.join(WEB_DIR, "src")
        show_website_version_banner()
        current_tag = _get_saved_web_tag()
        console.print(f"[faint]Текущий канал: [ok]{current_tag}[/ok][/faint]")
        web_tag = _ask_web_tag(default=current_tag)
        if not safe_confirm("Продолжить обновление?", default=True):
            return
        console.print("[accent]Обновление образа...[/accent]")
        if not _ensure_web_image(src_dir, web_tag, force_pull=True):
            return
        compose_path = os.path.join(WEB_DIR, "docker-compose.yml")
        if web_tag != current_tag:
            try:
                with open(compose_path) as f:
                    compose = f.read()
                compose = compose.replace(
                    f"image: {_web_image(current_tag)}",
                    f"image: {_web_image(web_tag)}",
                    1,
                )
                with open(compose_path, "w") as f:
                    f.write(compose)
            except Exception as e:
                step_warn(f"Не удалось обновить docker-compose.yml: {e}")
        try:
            with open(compose_path) as f:
                compose = f.read()
            if "host.docker.internal:host-gateway" not in compose:
                patched = compose.replace(
                    "    restart: unless-stopped\n",
                    '    restart: unless-stopped\n    extra_hosts:\n      - "host.docker.internal:host-gateway"\n',
                    1,
                )
                if patched != compose:
                    with open(compose_path, "w") as f:
                        f.write(patched)
                    console.print(
                        "[faint]docker-compose.yml: добавлен extra_hosts: host.docker.internal → host-gateway[/faint]"
                    )
        except Exception as e:
            step_warn(f"Не удалось пропатчить extra_hosts в docker-compose.yml: {e}")
        _save_web_tag(web_tag)
        _ensure_web_logs_dir()
        subprocess.run(["docker", "compose", "up", "-d", "--force-recreate"], cwd=WEB_DIR)
        step_ok(f"Обновлено до канала {web_tag}")
    elif choice == "6":
        env_path = os.path.join(WEB_DIR, ".env")
        editor = os.environ.get("EDITOR", "nano")
        subprocess.run([editor, env_path])
        if safe_confirm("Перезапустить сайт с новыми настройками?", default=True):
            subprocess.run(["docker", "compose", "restart"], cwd=WEB_DIR)
    elif choice == "7":
        env_path = os.path.join(WEB_DIR, ".env")
        if not os.path.isfile(env_path):
            step_warn(f".env не найден: {env_path}")
        else:
            try:
                with open(env_path, encoding="utf-8") as f:
                    content = f.read()
                console.print(
                    Panel(
                        content or "[faint]пусто[/faint]",
                        border_style="accent.dim",
                        width=_PANEL_W,
                        title=f"[brand]{env_path}[/brand]",
                        padding=(1, 2),
                    )
                )
            except Exception as e:
                step_fail(f"Не удалось прочитать .env: {e}")
    elif choice == "8":
        install_website()
    elif choice == "9":
        uninstall_website()


def show_update_menu():
    if IS_ROOT_DIR:
        step_fail("Обновление невозможно: бот лежит в /root")
        step_info("Перенесите бота в отдельную папку и повторите")
        return

    menu(
        "Обновление",
        [
            (
                "",
                [
                    ("1", "◐", "Бета-ветка (dev)", True, ""),
                    ("2", "●", "Релиз или патч", True, ""),
                    ("3", "←", "Назад", True, ""),
                ],
            ),
        ],
        subtitle=local_version(PROJECT_DIR) or "",
    )
    choice = ask_choice(3, "Источник обновления")

    if choice == "1":
        update_from_beta()
    elif choice == "2":
        update_from_release()


_SEMVER_CLI_RE = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?:-(?P<pre>[0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)


def _parse_solo_brick_semver(tag: str):
    match = _SEMVER_CLI_RE.match(tag.strip())
    if not match:
        return None
    major = int(match.group("major"))
    minor = int(match.group("minor"))
    patch = int(match.group("patch"))
    pre_raw = match.group("pre")
    if not pre_raw:
        return (major, minor, patch, 1, ())
    ids = []
    for part in pre_raw.split("."):
        if part.isdigit():
            ids.append((0, int(part)))
        else:
            ids.append((1, part))
    return (major, minor, patch, 0, tuple(ids))


def _docker_label(kind: str, ref: str) -> str | None:
    """Лейбл версии у образа или контейнера. None — докер не ответил или лейбла нет."""
    try:
        result = subprocess.run(
            [
                "docker",
                kind,
                "inspect",
                "--format",
                '{{index .Config.Labels "org.opencontainers.image.version"}}',
                ref,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None
    label = (result.stdout or "").strip()
    if result.returncode != 0 or not label or label == "<no value>":
        return None
    return label


def read_installed_solo_brick_version() -> str | None:
    """Версия работающего Solo-brick.

    Спрашиваем сперва сам контейнер: только он знает, из какого образа поднят.
    Образ :latest проверять первым нельзя — на канале dev его может не быть
    вовсе, и версия показывалась как «не определено».
    """
    label = _docker_label("container", WEB_CONTAINER_NAME)
    if label:
        return label

    saved_tag = _get_saved_web_tag()
    refs = [f"ghcr.io/{GHCR_IMAGE}:{saved_tag}"]
    for tag in WEB_TAG_CHOICES:
        ref = f"ghcr.io/{GHCR_IMAGE}:{tag}"
        if ref not in refs:
            refs.append(ref)
    refs.append(f"ghcr.io/{GHCR_IMAGE}")

    for image_ref in refs:
        label = _docker_label("image", image_ref)
        if label:
            return label
    return None


def fetch_latest_ghcr_tag(image: str, channel: str = "") -> str | None:
    try:
        token_resp = http_get(f"https://ghcr.io/token?scope=repository:{image}:pull", timeout=8)
        if token_resp.status_code != 200:
            return None
        token = str(token_resp.json().get("token") or "").strip()
        if not token:
            return None
        req = Request(
            f"https://ghcr.io/v2/{image}/tags/list",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        with urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        tags = payload.get("tags") or []
        versions = []
        for raw in tags:
            parsed = _parse_solo_brick_semver(str(raw))
            if parsed is None:
                continue
            prerelease = parsed[3] == 0
            # На канале latest предрелизы не предлагаем, на dev — наоборот, они и нужны.
            if channel == "latest" and prerelease:
                continue
            if channel == "dev" and not prerelease:
                continue
            versions.append((parsed, str(raw)))
        if not versions and channel:
            return fetch_latest_ghcr_tag(image)
        if not versions:
            return None
        versions.sort(key=lambda item: item[0], reverse=True)
        return versions[0][1]
    except Exception:
        return None


def show_website_version_banner():
    """Короткий баннер с установленной и доступной версией сайта."""
    installed = read_installed_solo_brick_version()
    channel = _get_saved_web_tag()
    with console.status("[accent]Проверка версии Solo-brick...[/accent]"):
        latest = fetch_latest_ghcr_tag(GHCR_IMAGE, channel)
    installed_str = installed if installed else "не определено"
    latest_str = latest if latest else "недоступно"
    tag = ""
    if installed and latest:
        cur = _parse_solo_brick_semver(installed)
        nxt = _parse_solo_brick_semver(latest)
        if cur and nxt and nxt > cur:
            tag = "   [warn.bold]доступно обновление[/warn.bold]"
        elif cur and nxt:
            tag = "   [ok]актуально[/ok]"
    console.print(
        f"  [faint]solo-brick[/faint]  [title]{installed_str}[/title]"
        f"  [faint]{_G_DOT}[/faint]  [muted]в реестре[/muted] [text]{latest_str}[/text]{tag}"
    )


def _dc(*args: str, capture: bool = False, check: bool = False):
    """docker compose в папке проекта."""
    cmd = ["docker", "compose", *args]
    return subprocess.run(
        cmd,
        cwd=PROJECT_DIR,
        capture_output=capture,
        text=True,
        check=check,
    )


def _docker_bot_state() -> tuple[str, str]:
    if not os.path.isfile(os.path.join(PROJECT_DIR, "docker-compose.yml")):
        return "faint", "нет docker-compose.yml"
    if not shutil.which("docker"):
        return "faint", "Docker не установлен"
    try:
        result = _dc("ps", "--format", "{{.Name}} {{.State}}", capture=True)
        rows = [r.strip() for r in (result.stdout or "").splitlines() if r.strip()]
    except Exception:
        return "warn", "статус неизвестен"
    if not rows:
        return "faint", "не запущен"
    running = sum(1 for r in rows if r.lower().endswith("running"))
    if running == len(rows):
        return "ok", f"работает ({running}/{len(rows)})"
    return "warn", f"частично ({running}/{len(rows)})"


def _write_docker_env(creds: dict, redis_external: bool) -> bool:
    env_path = os.path.join(PROJECT_DIR, ".env")
    lines = [
        f"DB_NAME={creds['name']}",
        f"DB_USER={creds['user']}",
        f"DB_PASSWORD={creds['password']}",
    ]
    if redis_external:
        lines.append("REDIS_URL=redis://host.docker.internal:6379/0")
    try:
        with open(env_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.chmod(env_path, 0o600)
        return True
    except Exception as e:
        step_fail(f"Не удалось записать .env: {e}")
        return False


def install_bot_docker():
    console.print(
        Panel(
            "[text]CLI соберёт образ бота и поднимет его в Docker вместе с PostgreSQL и Redis "
            "(или подключит к вашим, если они уже на хосте).[/text]\n\n"
            "[warn]Понадобятся два файла с сайта:[/warn] [bold]config.py[/bold] и [bold]texts.py[/bold] — "
            "CLI подскажет, куда их положить.\n\n"
            "[err.bold]Важно:[/err.bold] боту нужен [bold]домен с HTTPS[/bold] — HTTPS CLI умеет настроить сам.",
            border_style="ok",
            width=_PANEL_W,
            title="[ok.bold]Установка бота в Docker[/ok.bold]",
            padding=(1, 2),
        )
    )
    if not safe_confirm("Запустить установку в Docker?", default=True):
        return

    total = 6
    try:
        step_rule(1, total, "Файлы проекта")
        if not bootstrap_project_files(branch="main"):
            step_fail("Не удалось подготовить файлы проекта. Установка прервана.")
            return
        if not os.path.isfile(os.path.join(PROJECT_DIR, "docker-compose.yml")):
            step_fail("В проекте нет docker-compose.yml — обновите бота (пункт 7) и повторите.")
            return
        step_ok("Файлы проекта на месте.")

        step_rule(2, total, "Конфигурация")
        migrate_settings_layout(PROJECT_DIR, out=console.print)

        def _missing_cfg():
            moved = migrate_settings_layout(PROJECT_DIR, out=console.print)
            if moved:
                step_ok("Нашёл файлы в местах от другой версии и перенёс их куда нужно.")
            miss = []
            for path in (resolve_config_path(PROJECT_DIR), resolve_texts_path(PROJECT_DIR)):
                if not os.path.exists(path):
                    miss.append(os.path.relpath(path, PROJECT_DIR))
            return miss

        while True:
            missing = _missing_cfg()
            if not missing:
                break
            config_path = resolve_config_path(PROJECT_DIR)
            texts_path = resolve_texts_path(PROJECT_DIR)
            step_warn("Пока нет файлов: " + ", ".join(missing))
            console.print(
                Panel(
                    f"[text]Скачайте на сайте и положите на сервер:[/text]\n\n"
                    f"  • [accent]config.py[/accent] → [bold]{config_path}[/bold]\n"
                    f"  • [accent]texts.py[/accent] → [bold]{texts_path}[/bold]\n\n"
                    f"[text]Где взять:[/text] [bold]{CONFIG_BUILDER_URL}[/bold]\n\n"
                    f"  [bold]scp config.py root@ВАШ_IP:{config_path}[/bold]\n"
                    f"  [bold]scp texts.py root@ВАШ_IP:{texts_path}[/bold]",
                    border_style="warn",
                    width=_PANEL_W,
                    title="[warn.bold]Нужны config.py и texts.py[/warn.bold]",
                    padding=(1, 2),
                )
            )
            if not safe_confirm("Загрузили файлы? Проверить снова?", default=True):
                step_warn("Установка приостановлена. Запустите снова: sudo solobot")
                return
        step_ok("config.py и texts.py на месте.")

        step_rule(3, total, "Docker")
        if not _ensure_docker():
            step_fail("Docker недоступен. Установка прервана.")
            return
        step_ok("Docker готов.")

        step_rule(4, total, "База данных и Redis")
        console.print("[accent]Где будут база и Redis:[/accent]")
        console.print("  1. Поднять в Docker вместе с ботом (рекомендуется)")
        console.print("  2. Уже установлены на хосте — подключиться к ним")
        mode = safe_prompt("Выбор", choices=["1", "2"], default="1", show_choices=False)
        external = mode == "2"

        creds = _prompt_db_creds()
        if external:
            _write_config_value("PG_HOST", "host.docker.internal")
            redis_external = safe_confirm("Redis тоже на хосте?", default=True)
            console.print(
                "[faint]Не забудьте разрешить подключения из docker-сети: PostgreSQL — listen_addresses='*' "
                "и строка «host all all 172.16.0.0/12 scram-sha-256» в pg_hba.conf; Redis — bind 0.0.0.0 + пароль.[/faint]"
            )
        else:
            _write_config_value("PG_HOST", "postgres")
            redis_external = False
        _write_config_value("BACK_DIR", "/app/backups")
        if not _write_docker_env(creds, redis_external):
            return
        step_ok("Доступы записаны в config.py и .env.")

        step_rule(5, total, "HTTPS для бота")
        domain = _prompt_domain()
        setup_bot_https(domain)

        step_rule(6, total, "Сборка и запуск")
        console.print("[faint]Первая сборка образа занимает 3–5 минут.[/faint]")
        args = ["up", "-d", "--build"]
        if external:
            args += ["--no-deps", "bot"]
        result = _dc(*args)
        if result.returncode != 0:
            step_fail("Не удалось запустить контейнеры. Смотрите вывод выше.")
            return
        step_ok("Контейнеры запущены.")
        console.print()
        step_ok("Установка в Docker завершена.")
        console.print("[faint]Логи: пункт «Логи контейнера» в меню Docker. Проверьте /start в Telegram.[/faint]")
    except KeyboardInterrupt:
        console.print("\n[warn]Установка прервана пользователем.[/warn]")


def update_bot_docker():
    if not safe_confirm("Обновить код из GitHub и пересобрать образ?", default=True):
        return
    if os.path.isdir(os.path.join(PROJECT_DIR, ".git")):
        pull = subprocess.run(["git", "pull"], cwd=PROJECT_DIR)
        if pull.returncode != 0:
            step_warn("git pull не удался — пересобираю из текущих файлов.")
    else:
        step_warn("Проект не под git — пересобираю из текущих файлов.")
    result = _dc("up", "-d", "--build")
    if result.returncode == 0:
        step_ok("Бот обновлён и перезапущен.")
    else:
        step_fail("Пересборка не удалась. Смотрите вывод выше.")


def manage_bot_docker():
    while True:
        _style, state = _docker_bot_state()
        installed = state not in ("нет docker-compose.yml", "Docker не установлен")
        menu(
            "Бот в Docker",
            [
                (
                    "Управление",
                    [
                        ("1", "▶", "Запустить", installed, ""),
                        ("2", "⟳", "Перезапустить", installed, ""),
                        ("3", "■", "Остановить", installed, ""),
                    ],
                ),
                (
                    "Наблюдение",
                    [
                        ("4", "≡", "Логи контейнера", installed, ""),
                        ("5", "◉", "Статус контейнеров", installed, ""),
                    ],
                ),
                (
                    "Обслуживание",
                    [
                        ("6", "↑", "Обновить и пересобрать", installed, ""),
                        ("7", "⚙", "Установить бота в Docker", True, ""),
                    ],
                ),
                (
                    "",
                    [
                        ("8", "←", "Назад", True, ""),
                    ],
                ),
            ],
            subtitle=f"статус: {state}",
        )
        choice = ask_choice(8)
        if choice == "1":
            _dc("up", "-d")
            step_ok("Контейнеры запущены.")
        elif choice == "2":
            _dc("restart")
            step_ok("Контейнеры перезапущены.")
        elif choice == "3":
            if safe_confirm("Остановить бота в Docker?", default=False):
                _dc("stop")
                step_ok("Контейнеры остановлены.")
        elif choice == "4":
            step_info("Живой поток логов. Выход — Ctrl+C.")
            try:
                _dc("logs", "-f", "--tail", "80", "bot")
            except KeyboardInterrupt:
                pass
            console.print()
            step_ok("Просмотр логов завершён.")
        elif choice == "5":
            _dc("ps")
        elif choice == "6":
            update_bot_docker()
        elif choice == "7":
            install_bot_docker()
        elif choice == "8":
            return


def show_menu():
    bot_installed = has_project_code()
    venv_ready = bot_installed and os.path.exists(VENV_PYTHON)
    bot_runtime_ready = venv_ready and is_service_exists(SERVICE_NAME)
    need_install = "Серые пункты станут доступны после установки бота — пункт 9."

    menu(
        "Solobot",
        [
            (
                "Бот",
                [
                    ("1", "▶", "Запустить", bot_runtime_ready, need_install),
                    ("2", "⌁", "Запустить вручную (venv)", venv_ready, need_install),
                    ("3", "⟳", "Перезапустить", bot_runtime_ready, need_install),
                    ("4", "■", "Остановить", bot_runtime_ready, need_install),
                ],
            ),
            (
                "Наблюдение",
                [
                    ("5", "≡", "Логи, последние 80 строк", bot_runtime_ready, need_install),
                    ("6", "◉", "Статус службы", bot_runtime_ready, need_install),
                ],
            ),
            (
                "Обслуживание",
                [
                    ("7", "↑", "Обновить Solobot", bot_installed, need_install),
                    ("8", "▤", "Бэкап и восстановление", True, ""),
                    ("9", "⚙", "Установить или переустановить бота", True, ""),
                ],
            ),
            (
                "Docker",
                [
                    ("10", "▣", "Бот в Docker: установка и управление", True, ""),
                ],
            ),
            (
                "Сайт",
                [
                    ("11", "◈", "Веб-сайт: установка и управление", True, ""),
                ],
            ),
            (
                "",
                [
                    ("12", "✕", "Выход", True, ""),
                ],
            ),
        ],
        subtitle=SERVICE_NAME,
    )


UPDATE_REPORT_FILE = os.path.join(PROJECT_DIR, ".update_report.json")


def _write_update_report(status: str, detail: str, channel: str, tag: str, notify: int = 0) -> None:
    """Отчёт для бота: он прочитает его после перезапуска и доложит админу.

    Пишется в самом конце, после очистки папки проекта, поэтому переживает обновление.
    """
    import json
    import time

    try:
        with open(UPDATE_REPORT_FILE, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "status": status,
                    "detail": detail,
                    "channel": channel,
                    "tag": tag,
                    "notify": int(notify or 0),
                    "finished_at": int(time.time()),
                },
                fh,
                ensure_ascii=False,
            )
    except OSError:
        pass


def run_unattended_update(channel: str, tag: str, overwrite: dict, notify: int = 0) -> int:
    """Обновление без вопросов. Возвращает код выхода."""
    global _AUTO_YES, _AUTO_TAG, _AUTO_OVERWRITE, _AUTO_ABORT_REASON

    _AUTO_YES = True
    _AUTO_TAG = tag or ""
    _AUTO_OVERWRITE = dict(overwrite or {})
    _AUTO_ABORT_REASON = ""

    before = local_version(PROJECT_DIR) or "—"
    try:
        if channel == "beta":
            update_from_beta()
        else:
            update_from_release()
    except Exception as error:
        _write_update_report("error", str(error)[:400], channel, tag, notify)
        console.print(f"[err]Обновление не удалось: {error}[/err]")
        return 1

    if _AUTO_ABORT_REASON:
        _write_update_report("skipped", _AUTO_ABORT_REASON, channel, tag, notify)
        console.print(f"[warn]Обновление отменено: {_AUTO_ABORT_REASON}[/warn]")
        return 2

    after = local_version(PROJECT_DIR) or "—"
    _write_update_report("ok", f"{before} → {after}", channel, tag, notify)
    console.print(f"[ok]Обновление завершено: {before} → {after}[/ok]")
    return 0


def _parse_cli_args(argv: list[str]) -> dict | None:
    """Разбор аргументов. None — обычный интерактивный запуск."""
    if not argv or argv[0] != "--update":
        return None
    job = {"channel": "release", "tag": "", "overwrite": {}, "notify": 0}
    rest = argv[1:]
    if rest and not rest[0].startswith("--"):
        job["channel"] = rest.pop(0)
    while rest:
        item = rest.pop(0)
        if item == "--tag" and rest:
            job["tag"] = rest.pop(0)
        elif item == "--notify" and rest:
            try:
                job["notify"] = int(rest.pop(0))
            except ValueError:
                job["notify"] = 0
        elif item in ("--with-buttons", "--with-img", "--with-redis-cache"):
            job["overwrite"][item.replace("--with-", "").replace("-", "_")] = True
    return job


def main():
    os.chdir(PROJECT_DIR)
    if sys.version_info[:2] != (3, 12):
        python312 = shutil.which("python3.12")
        if python312 and os.path.realpath(python312) != os.path.realpath(sys.executable):
            os.execv(python312, [python312] + sys.argv)

    job = _parse_cli_args(sys.argv[1:])
    if job is not None:
        sys.exit(run_unattended_update(job["channel"], job["tag"], job["overwrite"], job["notify"]))

    auto_update_cli()
    print_logo()
    _ensure_solobot_command()
    if not cli_gate(PROJECT_DIR, lambda label, secret: safe_prompt(label, password=secret), console.print):
        return
    prompt_install_if_needed()
    try:
        while True:
            refresh_service_name()
            show_menu()
            choice = ask_choice(12)
            if choice == "1":
                if is_service_exists(SERVICE_NAME):
                    subprocess.run(["sudo", "systemctl", "start", SERVICE_NAME])
                    wait_for_bot_startup()
                else:
                    step_warn(f"Служба {SERVICE_NAME} не найдена.")
                    if safe_confirm("Установить бота и создать службу сейчас?", default=True):
                        install_bot()
            elif choice == "2":
                if not os.path.exists(VENV_PYTHON):
                    step_warn("Виртуальное окружение ещё не создано.")
                    if safe_confirm("[ok]Подготовить окружение через автоматическую установку?[/ok]", default=True):
                        install_bot()
                    continue
                try:
                    ver_out = subprocess.run(
                        [VENV_PYTHON, "-c", "import sys; print(sys.version_info[:2])"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if not any(v in ver_out.stdout for v in ("(3, 12)", "(3, 13)", "(3, 14)")):
                        console.print(
                            f"[warn]venv использует Python {ver_out.stdout.strip()} — ожидается 3.12+.[/warn]"
                        )
                        if not safe_confirm("Запустить всё равно?", default=False):
                            continue
                except Exception:
                    pass
                if safe_confirm("Вы действительно хотите запустить main.py вручную?"):
                    subprocess.run(["venv/bin/python", "main.py"])
            elif choice == "3":
                if is_service_exists(SERVICE_NAME):
                    if safe_confirm("Вы действительно хотите перезапустить бота?"):
                        subprocess.run(["sudo", "systemctl", "restart", SERVICE_NAME])
                        wait_for_bot_startup()
                else:
                    step_fail(f"Служба {SERVICE_NAME} не найдена.")
            elif choice == "4":
                if is_service_exists(SERVICE_NAME):
                    if safe_confirm("Вы уверены, что хотите остановить бота?"):
                        subprocess.run(["sudo", "systemctl", "stop", SERVICE_NAME])
                else:
                    step_fail(f"Служба {SERVICE_NAME} не найдена.")
            elif choice == "5":
                if is_service_exists(SERVICE_NAME):
                    step_info("Живой поток логов. Выход — Ctrl+C.")
                    try:
                        subprocess.run([
                            "sudo",
                            "journalctl",
                            "-u",
                            SERVICE_NAME,
                            "-n",
                            "80",
                            "-f",
                            "--no-pager",
                        ])
                    except KeyboardInterrupt:
                        pass
                    console.print()
                    step_ok("Просмотр логов завершён.")
                else:
                    step_fail(f"Служба {SERVICE_NAME} не найдена.")
            elif choice == "6":
                if is_service_exists(SERVICE_NAME):
                    subprocess.run(["sudo", "systemctl", "status", SERVICE_NAME])
                else:
                    step_fail(f"Служба {SERVICE_NAME} не найдена.")
            elif choice == "7":
                show_update_menu()
            elif choice == "8":
                manage_backup()
            elif choice == "9":
                install_bot()
            elif choice == "10":
                manage_bot_docker()
            elif choice == "11":
                manage_website()
            elif choice == "12":
                console.print("[brand]Выход из CLI. Удачного дня![/brand]")
                break
    except KeyboardInterrupt:
        console.print("\n[err.bold]Прерывание. Выход из CLI.[/err.bold]")


if __name__ == "__main__":
    main()

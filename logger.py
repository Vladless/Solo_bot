import logging
import os
import sys
import time

from datetime import timedelta
from pathlib import Path

from loguru import logger

from settings import config as cfg


try:
    from settings.cache_config import (
        ERROR_THROTTLE_MAX_KEYS,
        ERROR_THROTTLE_MESSAGE_MAX_LEN,
        ERROR_THROTTLE_WINDOW_SEC,
    )
except ImportError:
    ERROR_THROTTLE_WINDOW_SEC = 60
    ERROR_THROTTLE_MAX_KEYS = 500
    ERROR_THROTTLE_MESSAGE_MAX_LEN = 120


LEVELS = {
    "critical": 50,
    "error": 40,
    "warning": 30,
    "info": 20,
    "debug": 10,
    "notset": 0,
}

PANEL_XUI = "<green>[3x-ui]</green>"
PANEL_REMNA = "<blue>[Remnawave]</blue>"
CLOGGER = logger.opt(colors=True)


def _lvl(v, default="info"):
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        for tok in v.replace(",", " ").split():
            t = tok.strip().lower()
            if t in LEVELS:
                return LEVELS[t]
    return LEVELS[default]


BASE_LEVEL = _lvl(getattr(cfg, "LOGGING_LEVEL", getattr(cfg, "LOG_LEVEL", "info")))
LOG_ROTATION_TIME = getattr(cfg, "LOG_ROTATION_TIME", "1 day")

log_folder = "logs"
os.makedirs(log_folder, exist_ok=True)

logger.remove()

logger.configure(
    patcher=lambda r: r["extra"].update(
        module_tag=(
            f"[MODULE:{Path(r['file'].path).parts[Path(r['file'].path).parts.index('modules') + 1]}]"
            if "modules" in Path(r["file"].path).parts
            else ""
        )
    )
)

level_mapping = {50: "CRITICAL", 40: "ERROR", 30: "WARNING", 20: "INFO", 10: "DEBUG", 0: "NOTSET"}


_HTTP_NOISE_MARKERS = (
    "Invalid method encountered",
    "Bad status line",
    "Pause on PRI/Upgrade",
    "Expected HTTP/",
    "invalid constant string",
)


class InterceptHandler(logging.Handler):
    def emit(self, record):
        message = record.getMessage()
        if record.name.startswith("aiohttp.") and any(m in message for m in _HTTP_NOISE_MARKERS):
            if "\\x16\\x03" in message:
                hint = "TLS/HTTPS-данные (ожидался обычный HTTP)"
            else:
                hint = "не-HTTP данные (сканер/бот?)"
            logger.opt(depth=6).warning(f"[HTTP] На порт пришли {hint}, соединение закрыто (400 Bad Request)")
            return
        logger.opt(depth=6, exception=record.exc_info).log(level_mapping.get(record.levelno, "INFO"), message)


logging.basicConfig(handlers=[InterceptHandler()], level=0)

for name in (
    "httpcore",
    "httpx",
    "apscheduler",
    "apscheduler.executors.default",
    "apscheduler.scheduler",
    "async_api_base",
    "async_api",
    "async_api_client",
    "charset_normalizer",
    "asyncio",
):
    lg = logging.getLogger(name)
    lg.setLevel(logging.ERROR)
    lg.propagate = False

_EXCLUDE = {"async_api_base", "async_api", "async_api_client"}


_error_throttle = {}


def _error_throttle_key(record):
    msg = record.get("message", "")
    if isinstance(msg, str):
        first_line = msg.split("\n")[0].strip()[:ERROR_THROTTLE_MESSAGE_MAX_LEN]
    else:
        first_line = str(msg)[:ERROR_THROTTLE_MESSAGE_MAX_LEN]
    return (record.get("module", ""), record.get("function", ""), first_line)


def _error_throttle_prune():
    if len(_error_throttle) <= ERROR_THROTTLE_MAX_KEYS:
        return
    time.monotonic()
    by_ts = [(v[0], k) for k, v in _error_throttle.items()]
    by_ts.sort()
    for _, k in by_ts[: len(_error_throttle) - ERROR_THROTTLE_MAX_KEYS]:
        _error_throttle.pop(k, None)


def _throttle_allows(record):
    key = _error_throttle_key(record)
    now = time.monotonic()
    if key in _error_throttle:
        first_ts, count = _error_throttle[key]
        if now - first_ts < ERROR_THROTTLE_WINDOW_SEC:
            _error_throttle[key] = (first_ts, count + 1)
            return False
        if count > 0:
            suffix = f" (повторялась {count} раз за последние {int(ERROR_THROTTLE_WINDOW_SEC)} сек)"
            record["message"] = record["message"] + suffix
        _error_throttle[key] = (now, 0)
    else:
        _error_throttle_prune()
        _error_throttle[key] = (now, 0)
    return True


def _filter(record):
    """Пропускает запись, вердикт о подавлении повтора вычисляется один раз на запись."""
    if record.get("name") in _EXCLUDE or record.get("module") in _EXCLUDE:
        return False
    level_no = getattr(record.get("level"), "no", 20)
    if level_no < 40:
        return True
    extra = record.get("extra")
    if not isinstance(extra, dict):
        return _throttle_allows(record)
    if "throttle_verdict" not in extra:
        extra["throttle_verdict"] = _throttle_allows(record)
    return extra["throttle_verdict"]


def _file_filter(record):
    # API-строки доступа пишем в файл всегда, независимо от LOGGING_LEVEL —
    # чтобы вкладка «Апи» в админке работала при любом уровне (debug/info/warning/...).
    if "[API]" not in record["message"]:
        level_no = getattr(record.get("level"), "no", 20)
        if level_no < BASE_LEVEL:
            return False
    return _filter(record)


logger.add(
    sys.stderr,
    level=BASE_LEVEL,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level}</level> | <cyan>{module}:{function}:{line}</cyan> | <magenta>{extra[module_tag]}</magenta> <level>{message}</level>",
    colorize=True,
    filter=_filter,
)

log_file_path = os.path.join(log_folder, "logging.log")
logger.add(
    log_file_path,
    level=0,
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {module}:{function}:{line} | {extra[module_tag]} {message}",
    rotation=LOG_ROTATION_TIME,
    retention=timedelta(days=3),
    filter=_file_filter,
)

logger = logger

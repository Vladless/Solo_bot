import logging
import os
import sys

from datetime import timedelta

from loguru import logger

import config as cfg


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

level_mapping = {50: "CRITICAL", 40: "ERROR", 30: "WARNING", 20: "INFO", 10: "DEBUG", 0: "NOTSET"}


class InterceptHandler(logging.Handler):
    def emit(self, record):
        logger.opt(depth=6, exception=record.exc_info).log(
            level_mapping.get(record.levelno, "INFO"), record.getMessage()
        )


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
):
    lg = logging.getLogger(name)
    lg.setLevel(logging.ERROR)
    lg.propagate = False

_EXCLUDE = {"async_api_base", "async_api", "async_api_client"}


def _filter(record):
    return record.get("name") not in _EXCLUDE and record.get("module") not in _EXCLUDE


logger.add(
    sys.stderr,
    level=BASE_LEVEL,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level}</level> | <cyan>{module}:{function}:{line}</cyan> | <level>{message}</level>",
    colorize=True,
    filter=_filter,
)

log_file_path = os.path.join(log_folder, "logging.log")
logger.add(
    log_file_path,
    level=BASE_LEVEL,
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {module}:{function}:{line} | {message}",
    rotation=LOG_ROTATION_TIME,
    retention=timedelta(days=3),
    filter=_filter,
)

logger = logger

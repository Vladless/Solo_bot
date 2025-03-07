import logging
import os
import sys

from datetime import timedelta

from loguru import logger


log_folder = "logs"

if not os.path.exists(log_folder):
    os.makedirs(log_folder)

logger.remove()

level_mapping = {
    50: "CRITICAL",
    40: "ERROR",
    30: "WARNING",
    20: "INFO",
    10: "DEBUG",
    0: "NOTSET",
}


class InterceptHandler(logging.Handler):
    def emit(self, record):
        logger_opt = logger.opt(depth=6, exception=record.exc_info)
        message = record.getMessage()
        logger_opt.log(level_mapping.get(record.levelno, "INFO"), message)


logging.basicConfig(handlers=[InterceptHandler()], level=0)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger.add(
    sys.stderr,
    level="INFO",
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level}</level> | <cyan>{module}:{function}:{line}</cyan> | <level>{message}</level>",
    colorize=True,
)

log_file_path = os.path.join(log_folder, "logging.log")
logger.add(
    log_file_path,
    level="DEBUG",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {module}:{function}:{line} | {message}",
    rotation=timedelta(minutes=60),
    retention=timedelta(days=3),
)

logger = logger

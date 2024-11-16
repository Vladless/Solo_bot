import logging
import sys

from loguru import logger

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

# Настройка вывода в консоль
logger.add(
    sys.stderr,
    level="INFO",
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level}</level> | <cyan>{module}:{function}:{line}</cyan> | <level>{message}</level>",
    colorize=True,
)

# Настройка записи в файл
logger.add(
    "logging.log",
    level="DEBUG",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {module}:{function}:{line} | {message}",
    rotation="60 minute",
    retention=24,
)

logger = logger

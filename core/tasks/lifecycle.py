import os

from core.tasks.periodic_manager import periodic_task_manager
from core.tasks.registry import register_periodic_tasks
from hooks.hooks import register_hook
from logger import logger


def _should_start_manager() -> bool:
    if os.environ.get("NOTIFICATION_WORKER", "").strip() == "1":
        return True
    if os.environ.get("NOTIFICATION_WORKER_SEPARATE", "").strip() == "1":
        return False
    return True


async def ensure_periodic_task_manager_started(bot, sessionmaker) -> None:
    register_periodic_tasks()
    if not _should_start_manager():
        logger.info("[PeriodicManager] Пропуск старта в текущем процессе")
        return
    await periodic_task_manager.start(bot, sessionmaker)


async def ensure_periodic_task_manager_stopped() -> None:
    await periodic_task_manager.stop()


@register_hook("startup")
async def start_periodic_task_manager(bot, sessionmaker, **_kwargs):
    await ensure_periodic_task_manager_started(bot, sessionmaker)


@register_hook("shutdown")
async def stop_periodic_task_manager(**_kwargs):
    await ensure_periodic_task_manager_stopped()

import asyncio
import atexit
import multiprocessing
import signal

from collections.abc import Callable, Coroutine
from concurrent.futures import BrokenExecutor, ProcessPoolExecutor, ThreadPoolExecutor
from typing import TypeVar

from logger import logger


T = TypeVar("T")

_thread_pool: ThreadPoolExecutor | None = None
_process_pool: ProcessPoolExecutor | None = None
_background_tasks: set[asyncio.Task] = set()


def spawn(coro: Coroutine[object, object, object], *, name: str | None = None) -> asyncio.Task:
    """Запускает фоновую задачу и держит на неё ссылку до завершения."""
    task = asyncio.ensure_future(coro)
    if name:
        task.set_name(name)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def _atexit_shutdown_pools() -> None:
    """Очистка пулов при выходе из процесса (в т.ч. по atexit), уменьшает предупреждения resource_tracker."""
    shutdown_process_pool()
    shutdown_thread_pool()


def _worker_ignore_sigint() -> None:
    """Initializer воркера: игнорирует SIGINT, чтобы Ctrl+C не обрывал queue.get() с трейсбеком."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def get_thread_pool() -> ThreadPoolExecutor:
    """Возвращает общий пул потоков (создаёт при первом вызове)."""
    global _thread_pool
    if _thread_pool is None:
        from settings.config import EXECUTOR_POOL_SIZE

        size = max(1, int(EXECUTOR_POOL_SIZE))
        _thread_pool = ThreadPoolExecutor(max_workers=size, thread_name_prefix="bot-thread")
        logger.debug("[Executor] Пул потоков: {} воркеров", size)
    return _thread_pool


def shutdown_thread_pool() -> None:
    """Останавливает пул потоков (вызывать при shutdown приложения)."""
    global _thread_pool
    if _thread_pool is not None:
        _thread_pool.shutdown(wait=True)
        _thread_pool = None
        logger.debug("[Executor] Пул потоков остановлен")


def get_process_pool() -> ProcessPoolExecutor:
    """
    Возвращает пул процессов для тяжёлых задач (бэкап и т.д.).
    Задачи выполняются в отдельных процессах и могут использовать другие ядра CPU.
    """
    global _process_pool
    if _process_pool is None:
        from settings.config import PROCESS_POOL_SIZE

        size = max(1, min(int(PROCESS_POOL_SIZE), multiprocessing.cpu_count() or 4))
        ctx = multiprocessing.get_context("spawn")
        _process_pool = ProcessPoolExecutor(
            max_workers=size,
            mp_context=ctx,
            initializer=_worker_ignore_sigint,
        )
        atexit.register(_atexit_shutdown_pools)
        logger.debug("[Executor] Пул процессов: {} воркеров", size)
    return _process_pool


def shutdown_process_pool() -> None:
    """Останавливает пул процессов (вызывать при shutdown приложения)."""
    global _process_pool
    if _process_pool is not None:
        try:
            atexit.unregister(_atexit_shutdown_pools)
        except Exception:
            pass
        _process_pool.shutdown(wait=True)
        _process_pool = None
        logger.debug("[Executor] Пул процессов остановлен")


def should_run_heavy_tasks_separately() -> bool:
    """
    True, если есть запас по ядрам/потокам — тогда рассылка и уведомления
    можно выносить в отдельный поток/ядро.
    """
    try:
        from settings.config import EXECUTOR_POOL_SIZE

        pool_size = max(1, int(EXECUTOR_POOL_SIZE))
    except Exception:
        pool_size = 1
    cpu_count = multiprocessing.cpu_count() or 1
    return cpu_count >= 2 or pool_size >= 2


async def run_io[T](fn: Callable[..., T], *args: object) -> T:
    """Выполняет fn(*args) в пуле потоков (I/O). Один вызов для всех блокирующих операций."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(get_thread_pool(), lambda: fn(*args))


def _drop_process_pool() -> None:
    """Сбрасывает пул процессов, не дожидаясь остановки воркеров."""
    global _process_pool
    if _process_pool is None:
        return
    try:
        atexit.unregister(_atexit_shutdown_pools)
    except Exception:
        pass
    try:
        _process_pool.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass
    _process_pool = None


async def run_cpu[T](fn: Callable[..., T], *args: object) -> T:
    """Выполняет fn(*args) в пуле процессов; на сломанном пуле повторяет на новом."""
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(get_process_pool(), fn, *args)
    except BrokenExecutor:
        logger.warning("[Executor] Пул процессов сломан, поднимаю новый: {}", getattr(fn, "__name__", fn))
        _drop_process_pool()
        return await loop.run_in_executor(get_process_pool(), fn, *args)

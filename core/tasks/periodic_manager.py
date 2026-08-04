import asyncio
import fcntl
import inspect
import multiprocessing
import os
import tempfile
import threading

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.executors.pool import (
    ProcessPoolExecutor as APSchedulerProcessPoolExecutor,
    ThreadPoolExecutor as APSchedulerThreadPoolExecutor,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.base import BaseTrigger
from sqlalchemy.ext.asyncio import async_sessionmaker

from logger import logger


LoopRunner = Callable[[Bot, async_sessionmaker], Awaitable[None]]
ThreadLoopRunner = Callable[[threading.Event, Bot, async_sessionmaker], None]
CronRunner = Callable[[], Awaitable[None]] | Callable[[], None]
CronExecutionMode = Literal["async", "thread", "process"]


@dataclass
class ManagedLoopTask:
    task_id: str
    runner: LoopRunner


@dataclass
class ManagedThreadLoopTask:
    task_id: str
    runner: ThreadLoopRunner


@dataclass
class ManagedProcessLoopTask:
    task_id: str
    runner: LoopRunner


@dataclass
class ManagedCronTask:
    task_id: str
    runner: CronRunner
    trigger: BaseTrigger
    execution_mode: CronExecutionMode


@dataclass
class RunningThreadLoopTask:
    thread: threading.Thread
    stop_event: threading.Event


@dataclass
class RunningProcessLoopTask:
    process: multiprocessing.Process


def _run_process_loop_task(task_id: str, runner: LoopRunner) -> None:
    asyncio.run(_run_process_loop_task_async(task_id, runner))


async def _run_process_loop_task_async(task_id: str, runner: LoopRunner) -> None:
    from core.bootstrap import bootstrap
    from core.settings.modes_config import resolve_protect_content
    from database import async_session_maker
    from database.db import reset_async_db_engine
    from settings.config import API_TOKEN

    bot = Bot(
        token=API_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, protect_content=resolve_protect_content()),
    )
    logger.info("[PeriodicManager] Process-loop задача {} запущена, PID={}", task_id, os.getpid())
    try:
        reset_async_db_engine()
        await bootstrap()
        if bot.default is not None:
            bot.default.protect_content = resolve_protect_content()
        await runner(bot, async_session_maker)
    except Exception as error:
        logger.error("[PeriodicManager] Ошибка process-loop задачи {}: {}", task_id, error)
        raise
    finally:
        try:
            await bot.session.close()
        except Exception:
            pass


class PeriodicTaskManager:
    def __init__(self, timezone_name: str = "Europe/Moscow") -> None:
        self.timezone_name = timezone_name
        self._loop_tasks: dict[str, ManagedLoopTask] = {}
        self._thread_loop_tasks: dict[str, ManagedThreadLoopTask] = {}
        self._process_loop_tasks: dict[str, ManagedProcessLoopTask] = {}
        self._cron_tasks: dict[str, ManagedCronTask] = {}
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._running_thread_tasks: dict[str, RunningThreadLoopTask] = {}
        self._running_process_tasks: dict[str, RunningProcessLoopTask] = {}
        self._scheduler: AsyncIOScheduler | None = None
        self._scheduler_process_workers: int | None = None
        self._started = False
        self._lock = asyncio.Lock()
        self._process_lock_file = None
        self._cached_instance_key: str | None = None
        self._process_lock_path = os.path.join(tempfile.gettempdir(), "solo_bot_periodic_manager.lock")

    def _instance_key(self) -> str:
        if self._cached_instance_key is not None:
            return self._cached_instance_key
        import hashlib

        parts: list[str] = []
        try:
            from settings.config import API_TOKEN

            parts.append(str(API_TOKEN or ""))
        except Exception:
            pass
        parts.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
        raw = "|".join(part for part in parts if part) or "default"
        self._cached_instance_key = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
        return self._cached_instance_key

    def _process_lock_candidates(self) -> list[str]:
        filename = f"solo_bot_periodic_manager_{os.getuid()}_{self._instance_key()}.lock"
        candidates: list[str] = []
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "").strip()
        if runtime_dir:
            candidates.append(os.path.join(runtime_dir, filename))
        candidates.append(os.path.join(tempfile.gettempdir(), filename))
        unique_candidates: list[str] = []
        for candidate in candidates:
            if candidate not in unique_candidates:
                unique_candidates.append(candidate)
        return unique_candidates

    def register_loop_task(self, task_id: str, runner: LoopRunner) -> None:
        self._loop_tasks[task_id] = ManagedLoopTask(task_id=task_id, runner=runner)

    def register_thread_loop_task(self, task_id: str, runner: ThreadLoopRunner) -> None:
        self._thread_loop_tasks[task_id] = ManagedThreadLoopTask(task_id=task_id, runner=runner)

    def register_process_loop_task(self, task_id: str, runner: LoopRunner) -> None:
        self._process_loop_tasks[task_id] = ManagedProcessLoopTask(task_id=task_id, runner=runner)

    def set_scheduler_process_workers(self, workers: int | None) -> None:
        self._scheduler_process_workers = None if workers is None else max(0, int(workers))

    def register_cron_task(
        self,
        task_id: str,
        runner: CronRunner,
        trigger: BaseTrigger,
        execution_mode: CronExecutionMode = "async",
    ) -> None:
        if execution_mode not in {"async", "thread", "process"}:
            raise ValueError(f"Unsupported execution_mode: {execution_mode}")
        if execution_mode != "async" and inspect.iscoroutinefunction(runner):
            raise ValueError(f"Cron task {task_id} with execution_mode={execution_mode} must be a sync function")
        self._cron_tasks[task_id] = ManagedCronTask(
            task_id=task_id,
            runner=runner,
            trigger=trigger,
            execution_mode=execution_mode,
        )

    def _acquire_process_lock(self) -> bool:
        if self._process_lock_file is not None:
            return True
        for candidate_path in self._process_lock_candidates():
            try:
                lock_file = open(candidate_path, "a+", encoding="utf-8")
            except OSError as error:
                logger.warning("[PeriodicManager] Не удалось открыть lock-файл {}: {}", candidate_path, error)
                continue
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                lock_file.seek(0)
                lock_file.truncate()
                lock_file.write(str(os.getpid()))
                lock_file.flush()
                self._process_lock_file = lock_file
                self._process_lock_path = candidate_path
                return True
            except OSError:
                lock_file.close()
                return False
        logger.warning("[PeriodicManager] Не удалось создать lock-файл, запуск менеджера пропущен")
        return False

    def _release_process_lock(self) -> None:
        if self._process_lock_file is None:
            return
        try:
            fcntl.flock(self._process_lock_file.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            self._process_lock_file.close()
        except OSError:
            pass
        self._process_lock_file = None

    def _build_scheduler(self) -> AsyncIOScheduler:
        from settings.config import EXECUTOR_POOL_SIZE, PROCESS_POOL_SIZE

        thread_workers = max(1, int(EXECUTOR_POOL_SIZE))
        configured_process_workers = self._scheduler_process_workers
        if configured_process_workers is None:
            configured_process_workers = int(PROCESS_POOL_SIZE)
        process_workers = max(0, min(configured_process_workers, multiprocessing.cpu_count() or 1))
        executors = {
            "default": AsyncIOExecutor(),
            "threadpool": APSchedulerThreadPoolExecutor(max_workers=thread_workers),
        }
        if process_workers > 0:
            executors["processpool"] = APSchedulerProcessPoolExecutor(max_workers=process_workers)
        return AsyncIOScheduler(timezone=self.timezone_name, executors=executors)

    @staticmethod
    def _cron_executor_name(execution_mode: CronExecutionMode) -> str:
        if execution_mode == "thread":
            return "threadpool"
        if execution_mode == "process":
            return "processpool"
        return "default"

    @staticmethod
    def _thread_loop_entry(
        task_id: str,
        runner: ThreadLoopRunner,
        stop_event: threading.Event,
        bot: Bot,
        sessionmaker: async_sessionmaker,
    ) -> None:
        try:
            runner(stop_event, bot, sessionmaker)
        except Exception as error:
            logger.error("[PeriodicManager] Ошибка thread-loop задачи {}: {}", task_id, error)

    async def _join_thread_task(self, task_id: str, running_task: RunningThreadLoopTask) -> None:
        running_task.stop_event.set()
        await asyncio.to_thread(running_task.thread.join, 5)
        if running_task.thread.is_alive():
            logger.warning("[PeriodicManager] Thread-loop задача {} не завершилась вовремя", task_id)

    async def _stop_process_task(self, task_id: str, running_task: RunningProcessLoopTask) -> None:
        process = running_task.process
        if not process.is_alive():
            await asyncio.to_thread(process.join, 1)
            return
        process.terminate()
        await asyncio.to_thread(process.join, 5)
        if process.is_alive():
            process.kill()
            await asyncio.to_thread(process.join, 5)
        if process.is_alive():
            logger.warning("[PeriodicManager] Process-loop задача {} не завершилась вовремя", task_id)

    async def start(self, bot: Bot, sessionmaker: async_sessionmaker) -> None:
        async with self._lock:
            if self._started:
                return
            if not self._acquire_process_lock():
                logger.info("[PeriodicManager] Уже запущен в другом процессе, текущий запуск пропущен")
                return
            scheduler = self._build_scheduler()
            for cron_task in self._cron_tasks.values():
                scheduler.add_job(
                    cron_task.runner,
                    cron_task.trigger,
                    id=cron_task.task_id,
                    executor=self._cron_executor_name(cron_task.execution_mode),
                    replace_existing=True,
                    max_instances=1,
                    coalesce=True,
                )
            scheduler.start()
            self._scheduler = scheduler
            for loop_task in self._loop_tasks.values():
                self._running_tasks[loop_task.task_id] = asyncio.create_task(loop_task.runner(bot, sessionmaker))
            for loop_task in self._thread_loop_tasks.values():
                stop_event = threading.Event()
                thread = threading.Thread(
                    target=self._thread_loop_entry,
                    args=(loop_task.task_id, loop_task.runner, stop_event, bot, sessionmaker),
                    name=f"periodic-{loop_task.task_id}",
                    daemon=True,
                )
                thread.start()
                self._running_thread_tasks[loop_task.task_id] = RunningThreadLoopTask(
                    thread=thread,
                    stop_event=stop_event,
                )
            for loop_task in self._process_loop_tasks.values():
                ctx = multiprocessing.get_context("spawn")
                process = ctx.Process(
                    target=_run_process_loop_task,
                    args=(loop_task.task_id, loop_task.runner),
                    name=f"periodic-{loop_task.task_id}",
                    daemon=True,
                )
                process.start()
                self._running_process_tasks[loop_task.task_id] = RunningProcessLoopTask(process=process)
            self._started = True
            logger.info(
                "[PeriodicManager] Запущен: async-loop=%s thread-loop=%s process-loop=%s cron=%s",
                len(self._loop_tasks),
                len(self._thread_loop_tasks),
                len(self._process_loop_tasks),
                len(self._cron_tasks),
            )

    async def stop(self) -> None:
        async with self._lock:
            if not self._started:
                return
            if self._scheduler is not None:
                try:
                    self._scheduler.shutdown(wait=False)
                except Exception:
                    pass
                self._scheduler = None
            tasks = list(self._running_tasks.values())
            self._running_tasks.clear()
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            thread_tasks = list(self._running_thread_tasks.items())
            self._running_thread_tasks.clear()
            if thread_tasks:
                await asyncio.gather(
                    *(self._join_thread_task(task_id, running_task) for task_id, running_task in thread_tasks),
                    return_exceptions=True,
                )
            process_tasks = list(self._running_process_tasks.items())
            self._running_process_tasks.clear()
            if process_tasks:
                await asyncio.gather(
                    *(self._stop_process_task(task_id, running_task) for task_id, running_task in process_tasks),
                    return_exceptions=True,
                )
            self._started = False
            self._release_process_lock()
            logger.info("[PeriodicManager] Остановлен")


periodic_task_manager = PeriodicTaskManager()

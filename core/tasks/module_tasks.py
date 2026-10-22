import importlib
import inspect

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from logger import logger
from utils.core_features import is_core_replaced_module


ModuleLoopRunner = Callable[[Any, Any], Awaitable[None]]
MODULES_DIR = Path(__file__).resolve().parents[2] / "modules"


def discover_module_loop_tasks() -> list[tuple[str, ModuleLoopRunner]]:
    """Находит фоновые циклы установленных модулей."""
    if not MODULES_DIR.is_dir():
        return []
    try:
        module_paths = sorted(MODULES_DIR.iterdir())
    except OSError as exc:
        logger.warning("[PeriodicManager] Не удалось прочитать каталог модулей: {}", exc)
        return []
    tasks = []
    for module_path in module_paths:
        name = module_path.name
        if not name.isidentifier() or is_core_replaced_module(name) or not (module_path / "tasks.py").is_file():
            continue
        try:
            module = importlib.import_module(f"modules.{name}.tasks")
            factory = getattr(module, "get_loop_tasks", None)
            if not callable(factory) or inspect.iscoroutinefunction(factory):
                raise ValueError("Ожидается синхронная функция get_loop_tasks")
            exported = factory()
            records = exported.items() if isinstance(exported, dict) else exported
            discovered = []
            for task_id, runner in records:
                if not isinstance(task_id, str) or not task_id.strip() or task_id != task_id.strip():
                    raise ValueError("Недопустимый идентификатор фонового цикла")
                if not inspect.iscoroutinefunction(runner):
                    raise ValueError("Ожидается асинхронная функция фонового цикла")
                discovered.append((f"module:{name}:{task_id}", runner))
            tasks.extend(discovered)
        except Exception as exc:
            logger.warning("[PeriodicManager] Фоновые циклы модуля {} не загружены: {}", name, exc)
    return tasks

import asyncio
import inspect

from collections.abc import Callable
from typing import Any

from logger import logger

from .constants import DEFAULT_HOOK_TIMEOUT


_hooks: dict[str, list[tuple[Callable[..., Any], str | None]]] = {}


def owner(func: Callable[..., Any]) -> str | None:
    m = getattr(func, "__module__", "") or ""
    if m.startswith("modules."):
        parts = m.split(".")
        return parts[1] if len(parts) > 1 else None
    return None


def register_hook(name: str, func: Callable[..., Any] | None = None):
    if func is None:

        def deco(f: Callable[..., Any]):
            _hooks.setdefault(name, []).append((f, owner(f)))
            logger.info("[Hook] {} -> {}", name, f.__name__)
            return f

        return deco
    _hooks.setdefault(name, []).append((func, owner(func)))
    logger.info("[Hook] {} -> {}", name, func.__name__)


def unregister_module_hooks(module_name: str):
    for k, lst in list(_hooks.items()):
        filtered = [(f, owner) for (f, owner) in lst if owner != module_name]
        if filtered:
            _hooks[k] = filtered
        else:
            _hooks.pop(k, None)


async def run_hooks(name: str, require_enabled: bool = True, **kwargs) -> list[Any]:
    """Вызывает зарегистрированные хуки и собирает результаты."""
    results: list[Any] = []
    for func, owner in _hooks.get(name, []):
        if require_enabled and owner:
            try:
                from utils.modules_manager import manager

                if not manager.is_enabled(owner):
                    continue
            except Exception:
                pass
        try:
            if inspect.iscoroutinefunction(func):
                coro = func(**kwargs)
            else:
                from core.executor import run_io

                coro = run_io(lambda: func(**kwargs))

            result = await asyncio.wait_for(coro, timeout=DEFAULT_HOOK_TIMEOUT)
            if result:
                results.append(result)
        except TimeoutError:
            logger.error(
                "[Hook:{}] Таймаут {} с в {}",
                name,
                DEFAULT_HOOK_TIMEOUT,
                getattr(func, "__name__", func),
                exc_info=True,
            )
        except Exception as e:
            logger.error(
                "[Hook:{}] Ошибка в {}: {}",
                name,
                getattr(func, "__name__", func),
                e,
                exc_info=True,
            )
    return results

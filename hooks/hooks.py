import asyncio
import inspect

from collections.abc import Callable
from typing import Any

from logger import logger

from .constants import DEFAULT_HOOK_TIMEOUT


_hooks: dict[str, list[tuple[Callable[..., Any], str | None]]] = {}
_DATA_HOOK_LOAD_ERRORS: dict[str, Exception] = {}


def owner(func: Callable[..., Any]) -> str | None:
    m = getattr(func, "__module__", "") or ""
    if m.startswith("modules."):
        parts = m.split(".")
        return parts[1] if len(parts) > 1 else None
    return None


def register_hook(name: str, func: Callable[..., Any] | None = None):
    if func is None:

        def deco(f: Callable[..., Any]):
            register_hook(name, f)
            return f

        return deco
    if name == "user_data_transfer":
        for registered, _module in _hooks.get(name, []):
            if (
                (registered.__module__, registered.__qualname__) == (func.__module__, func.__qualname__)
                and getattr(registered, "__data_transfer_guard__", False)
                and not getattr(func, "__data_transfer_guard__", False)
            ):
                registered.__wrapped__ = func
                func = registered
                break
        _hooks[name] = [
            (registered, module)
            for registered, module in _hooks.get(name, [])
            if (registered.__module__, registered.__qualname__) != (func.__module__, func.__qualname__)
        ]
    _hooks.setdefault(name, []).append((func, owner(func)))
    logger.info("[Hook] {} -> {}", name, func.__name__)


def unregister_module_hooks(module_name: str):
    for k, lst in list(_hooks.items()):
        if k == "user_data_transfer":
            continue
        filtered = [(f, owner) for (f, owner) in lst if owner != module_name]
        if filtered:
            _hooks[k] = filtered
        else:
            _hooks.pop(k, None)


def clear_missing_module_data_hooks(installed: set[str]) -> None:
    """Удаляет переносы и ошибки отсутствующих дополнений."""
    for module in set(_DATA_HOOK_LOAD_ERRORS) - installed:
        _DATA_HOOK_LOAD_ERRORS.pop(module, None)
    if "user_data_transfer" in _hooks:
        _hooks["user_data_transfer"] = [
            (func, module) for func, module in _hooks["user_data_transfer"] if module is None or module in installed
        ]


async def run_hooks(name: str, require_enabled: bool = True, *, raise_on_error: bool = False, **kwargs) -> list[Any]:
    """Вызывает зарегистрированные хуки и собирает результаты."""
    if name == "user_data_transfer" and raise_on_error and _DATA_HOOK_LOAD_ERRORS:
        module, error = next(iter(_DATA_HOOK_LOAD_ERRORS.items()))
        raise RuntimeError(f"Не загружен перенос данных модуля {module}: {error}") from error
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
            if raise_on_error:
                raise
        except Exception as e:
            logger.error(
                "[Hook:{}] Ошибка в {}: {}",
                name,
                getattr(func, "__name__", func),
                e,
                exc_info=True,
            )
            if raise_on_error:
                raise
    return results

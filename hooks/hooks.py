import inspect

from collections.abc import Callable
from typing import Any

from logger import logger


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
            logger.info(f"[Hook] Зарегистрирован хук '{name}': {f.__name__}")
            return f

        return deco
    _hooks.setdefault(name, []).append((func, owner(func)))
    logger.info(f"[Hook] Зарегистрирован хук '{name}': {func.__name__}")


def unregister_module_hooks(module_name: str):
    for k, lst in list(_hooks.items()):
        filtered = [(f, owner) for (f, owner) in lst if owner != module_name]
        if filtered:
            _hooks[k] = filtered
        else:
            _hooks.pop(k, None)


async def run_hooks(name: str, require_enabled: bool = True, **kwargs) -> list[Any]:
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
                result = await func(**kwargs)
            else:
                result = func(**kwargs)
            if result:
                results.append(result)
        except Exception as e:
            logger.error(f"[HOOK:{name}] Ошибка в {getattr(func, '__name__', func)}: {e}")
    return results

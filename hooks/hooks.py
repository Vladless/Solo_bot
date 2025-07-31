import inspect

from collections.abc import Awaitable, Callable
from typing import Any

from logger import logger


_hooks: dict[str, list[Callable[..., Any]]] = {}


def register_hook(name: str, func: Callable[..., Any]):
    if name not in _hooks:
        _hooks[name] = []
    _hooks[name].append(func)
    logger.info(f"[Hook] Зарегистрирован хук '{name}': {func.__name__}")


async def run_hooks(name: str, **kwargs) -> list[Any]:
    results = []
    for func in _hooks.get(name, []):
        try:
            if inspect.iscoroutinefunction(func):
                result = await func(**kwargs)
            else:
                result = func(**kwargs)
            if result:
                results.append(result)
        except Exception as e:
            from logger import logger

            logger.error(f"[HOOK:{name}] Ошибка в {func.__name__}: {e}")
    return results

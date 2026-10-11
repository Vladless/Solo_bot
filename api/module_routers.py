import importlib

from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.routing import APIRoute

from core.executor import run_io
from logger import logger
from utils.core_features import is_core_replaced_module
from utils.modules_manager import manager


MODULES_DIR = Path(__file__).resolve().parents[1] / "modules"


def _module_dependency(name: str):
    """Проверяет доступность дополнения при каждом веб-запросе."""

    async def require_module_enabled() -> None:
        """Блокирует запросы отключённого или удалённого дополнения."""
        enabled = await run_io(manager.is_available, name)
        if not enabled or not (MODULES_DIR / name / "api.py").is_file():
            raise HTTPException(status_code=404, detail="Модуль недоступен")

    return require_module_enabled


def load_module_api_routers(version: int) -> list[APIRouter]:
    """Находит необязательные API-дополнения без привязки к их именам."""
    routers = []
    if not MODULES_DIR.is_dir():
        return routers
    for directory in sorted(MODULES_DIR.iterdir()):
        name = directory.name
        if not name.isidentifier() or is_core_replaced_module(name) or not (directory / "api.py").is_file():
            continue
        try:
            module = importlib.import_module(f"modules.{name}.api")
            extension_routers = module.get_api_routers(version)
            for router in extension_routers:
                if not isinstance(router, APIRouter):
                    raise TypeError("API-дополнение должно возвращать APIRouter")
                guarded = APIRouter(dependencies=[Depends(_module_dependency(name))])
                guarded.include_router(router)
                routers.append(guarded)
        except Exception as exc:
            logger.warning("[Modules:API] Не удалось загрузить {}: {}", name, type(exc).__name__)
    return routers


def register_module_api_routers(app: FastAPI, default_router: APIRouter, version: int) -> None:
    """Подключает дополнения и убирает затенённые маршруты ядра."""
    overrides = set()
    for router in load_module_api_routers(version):
        app.include_router(router)
        for route in router.routes:
            if isinstance(route, APIRoute):
                overrides.update((route.path, method) for method in route.methods)
    default_start = len(app.router.routes)
    app.include_router(default_router)
    app.router.routes[default_start:] = [
        route
        for route in app.router.routes[default_start:]
        if not isinstance(route, APIRoute)
        or not route.methods
        or not all((route.path, method) in overrides for method in route.methods)
    ]

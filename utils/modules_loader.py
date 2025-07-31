import importlib
import pkgutil

from pathlib import Path

from aiogram import Router

from logger import logger


def load_modules_from_folder(folder: str = "modules") -> list[Router]:
    routers = []
    base_path = Path(folder)

    if not base_path.exists():
        logger.warning(f"[Modules] Папка {folder} не найдена, пропускаем загрузку модулей.")
        return []

    for _finder, name, _ispkg in pkgutil.iter_modules([str(base_path)]):
        module_path = f"{folder}.{name}.router"
        try:
            mod = importlib.import_module(module_path)
            if hasattr(mod, "router") and isinstance(mod.router, Router):
                routers.append(mod.router)
                logger.info(f"[Modules] Загружен модуль: {module_path}")
            else:
                logger.warning(f"[Modules] В модуле {module_path} не найден router")
        except Exception as e:
            logger.error(f"[Modules] Ошибка при загрузке {module_path}: {e}")
    return routers

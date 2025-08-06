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


def load_module_webhooks(folder: str = "modules") -> list[dict]:
    webhooks = []
    base_path = Path(folder)

    if not base_path.exists():
        logger.warning(f"[Modules] Папка {folder} не найдена, пропускаем загрузку вебхуков.")
        return []

    for _finder, name, _ispkg in pkgutil.iter_modules([str(base_path)]):
        module_path = f"{folder}.{name}"
        try:
            router_module = importlib.import_module(f"{module_path}.router")
            if hasattr(router_module, "get_webhook_data"):
                webhook_data = router_module.get_webhook_data()
                if isinstance(webhook_data, dict) and "path" in webhook_data and "handler" in webhook_data:
                    webhooks.append(webhook_data)
                    logger.info(f"[Modules] Найден вебхук в модуле {name}: {webhook_data['path']}")
                        
        except Exception as e:
            logger.error(f"[Modules] Ошибка при загрузке вебхуков из {module_path}: {e}")
    
    return webhooks


def load_module_fast_flow_handlers(folder: str = "modules") -> dict:
    handlers = {}
    base_path = Path(folder)

    if not base_path.exists():
        logger.warning(f"[Modules] Папка {folder} не найдена, пропускаем загрузку быстрого флоу.")
        return {}

    for _finder, name, _ispkg in pkgutil.iter_modules([str(base_path)]):
        module_path = f"{folder}.{name}"
        try:
            router_module = importlib.import_module(f"{module_path}.router")
            if hasattr(router_module, "get_fast_flow_handler"):
                fast_flow_data = router_module.get_fast_flow_handler()
                if fast_flow_data and isinstance(fast_flow_data, dict) and "payment_key" in fast_flow_data and "handler" in fast_flow_data:
                    payment_key = fast_flow_data["payment_key"]
                    handler = fast_flow_data["handler"]
                    handlers[payment_key] = handler
                    logger.info(f"[Modules] Найден обработчик быстрого флоу в модуле {name}: {payment_key}")
                elif fast_flow_data is None:
                    logger.info(f"[Modules] Быстрое флоу отключено в модуле {name}")
                        
        except Exception as e:
            logger.error(f"[Modules] Ошибка при загрузке быстрого флоу из {module_path}: {e}")
    
    return handlers

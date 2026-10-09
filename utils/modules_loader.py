import importlib
import pkgutil

from pathlib import Path

from aiogram import Router

from hooks.hooks import _DATA_HOOK_LOAD_ERRORS, clear_missing_module_data_hooks, register_hook
from logger import logger
from utils.core_features import is_core_replaced_module

from .modules_manager import manager


modules_hub = Router(name="modules_hub")


def _is_safe_module_name(name: str) -> bool:
    return bool(name and name.isidentifier() and "." not in name and "/" not in name and "\\" not in name)


def _load_module_data_hooks(folder: str, name: str, base_path: Path) -> None:
    """Загружает перенос данных независимо от интерфейса модуля."""
    if is_core_replaced_module(name):
        _DATA_HOOK_LOAD_ERRORS.pop(name, None)
        return
    data_path = base_path / name / "data_hooks.py"
    partner_path = base_path / name / "services.py"
    if not data_path.is_file() and not (name == "partner_program" and partner_path.is_file()):
        _DATA_HOOK_LOAD_ERRORS.pop(name, None)
        return
    try:
        if data_path.is_file():
            importlib.import_module(f"{folder}.{name}.data_hooks")
        else:
            mod = importlib.import_module(f"{folder}.{name}.services")

            async def transfer(**kwargs):
                from database.module_data import partner_transfer_schema_available

                if await partner_transfer_schema_available(kwargs["session"]):
                    await transfer.__wrapped__(**kwargs)

            transfer.__module__ = mod.transfer_partner_data.__module__
            transfer.__qualname__ = mod.transfer_partner_data.__qualname__
            transfer.__wrapped__ = mod.transfer_partner_data
            transfer.__data_transfer_guard__ = True
            register_hook("user_data_transfer", transfer)
        _DATA_HOOK_LOAD_ERRORS.pop(name, None)
    except Exception as exc:
        _DATA_HOOK_LOAD_ERRORS[name] = exc
        logger.error("[Modules] Не загружен перенос данных {}: {}", name, exc)


def load_modules_from_folder(folder: str = "modules") -> list[Router]:
    routers = []
    base_path = Path(folder)
    modules = list(pkgutil.iter_modules([str(base_path)]))
    if folder == "modules":
        clear_missing_module_data_hooks({name.strip() for _, name, _ in modules if _is_safe_module_name(name.strip())})

    for _finder, name, _ispkg in modules:
        name = (name or "").strip()
        if not _is_safe_module_name(name):
            logger.warning(f"[Modules] Пропуск недопустимого имени модуля: {name!r}")
            continue
        _load_module_data_hooks(folder, name, base_path)
        if not manager.should_autostart(name):
            logger.info(f"[Modules] Пропуск автозапуска модуля '{name}' (отключён).")
            continue

        module_path = f"{folder}.{name}.router"
        try:
            mod = importlib.import_module(module_path)
            router = getattr(mod, "router", None)
            if isinstance(router, Router):
                modules_hub.include_router(router)
                manager.adopt(name, router)
                routers.append(router)
                logger.info(f"[Modules] Загружен модуль: {module_path}")
            else:
                logger.warning(f"[Modules] В модуле {module_path} не найден router")
        except Exception as e:
            logger.error(f"[Modules] Ошибка при загрузке {module_path}: {e}")
    return routers


def load_module_webhooks(folder: str = "modules") -> list[dict]:
    webhooks = []
    base_path = Path(folder)

    for _finder, name, _ispkg in pkgutil.iter_modules([str(base_path)]):
        name = (name or "").strip()
        if not _is_safe_module_name(name):
            continue
        if not manager.should_autostart(name):
            logger.info(f"[Modules] Пропуск вебхуков модуля '{name}' (отключён).")
            continue

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

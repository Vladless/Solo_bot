import importlib
import json
import os
import sys

from aiogram import Router

from hooks.hooks import unregister_module_hooks
from logger import logger


IGNORE_SUBMODULES = {"models", "schemas", "db"}
STATE_FILE = os.getenv("MODULES_STATE_FILE", "storage/modules_state.json")


def _normalize_module_name(name: str | None) -> str:
    return (name or "").strip()


class ModuleRecord:
    def __init__(self, name: str, pkg: str) -> None:
        self.name = name
        self.pkg = pkg
        self.router: Router | None = None
        self.enabled: bool = False


class ModulesManager:
    def __init__(self, base: str = "modules") -> None:
        self.base = base
        self.registry: dict[str, ModuleRecord] = {}
        self.disabled: set[str] = set()
        self._load_state()

    def pkg(self, name: str) -> str:
        return f"{self.base}.{name}"

    def _load_state(self) -> None:
        try:
            if os.path.isfile(STATE_FILE):
                with open(STATE_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                raw = data.get("disabled", [])
                self.disabled = {_normalize_module_name(n) for n in raw if _normalize_module_name(n)}
            else:
                os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
                self._save_state()
        except Exception as e:
            logger.warning(f"[Modules] Не удалось загрузить состояние: {e}")

    def _save_state(self) -> None:
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({"disabled": sorted(self.disabled)}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[Modules] Не удалось сохранить состояние: {e}")

    def adopt(self, name: str, router: Router):
        name = _normalize_module_name(name)
        rec = self.registry.get(name) or ModuleRecord(name, self.pkg(name))
        rec.router = router
        rec.enabled = True
        self.registry[name] = rec

    def _is_safe_module_name(self, name: str) -> bool:
        return bool(name and name.isidentifier() and "." not in name and "/" not in name and "\\" not in name)

    async def start(self, name: str) -> None:
        name = _normalize_module_name(name)
        if not self._is_safe_module_name(name):
            raise ValueError(f"[Modules] Недопустимое имя модуля: {name!r}")
        rec = self.registry.get(name) or ModuleRecord(name, self.pkg(name))
        if rec.enabled:
            logger.info(f"[Modules] {name} уже активен.")
            return

        try:
            unregister_module_hooks(name)
        except Exception:
            pass

        self.purge_selective(rec.pkg)

        mod = importlib.import_module(f"{rec.pkg}.router")
        router = getattr(mod, "router", None)
        if not isinstance(router, Router):
            raise RuntimeError(f"[Modules] В модуле {name} не найден router")

        from utils.modules_loader import modules_hub

        modules_hub.include_router(router)

        rec.router = router
        rec.enabled = True
        self.registry[name] = rec

        if name in self.disabled:
            self.disabled.discard(name)
            self._save_state()

        logger.info(f"[Modules] {name} запущен.")

    async def stop(self, name: str) -> None:
        name = _normalize_module_name(name)
        rec = self.registry.get(name)
        if not rec or not rec.enabled:
            logger.info(f"[Modules] {name} уже остановлен или не найден.")
            if name not in self.disabled:
                self.disabled.add(name)
                self._save_state()
            return

        try:
            unregister_module_hooks(name)
        except Exception:
            pass

        from utils.modules_loader import modules_hub

        sub = getattr(modules_hub, "_sub_routers", None) or getattr(modules_hub, "sub_routers", None)
        if sub and rec.router in sub:
            sub.remove(rec.router)

        rec.router = None
        rec.enabled = False

        if name not in self.disabled:
            self.disabled.add(name)
            self._save_state()

        logger.info(f"[Modules] {name} остановлен.")

    async def restart(self, name: str) -> None:
        name = _normalize_module_name(name)
        logger.info(f"[Modules] Перезапуск {name}...")
        await self.stop(name)
        await self.start(name)

    def purge_selective(self, root_pkg: str) -> None:
        to_del = []
        for m in list(sys.modules):
            if m == root_pkg or m.startswith(root_pkg + "."):
                tail = m[len(root_pkg) :].lstrip(".")
                top = tail.split(".", 1)[0] if tail else ""
                if top and top in IGNORE_SUBMODULES:
                    continue
                to_del.append(m)
        for m in to_del:
            sys.modules.pop(m, None)
        importlib.invalidate_caches()

    def is_enabled(self, name: str) -> bool:
        name = _normalize_module_name(name)
        rec = self.registry.get(name)
        if not rec or not rec.router:
            return False
        try:
            from utils.modules_loader import modules_hub
        except Exception:
            return bool(rec.enabled)
        sub = getattr(modules_hub, "_sub_routers", None) or getattr(modules_hub, "sub_routers", None)
        return bool(sub and rec.router in sub)

    def is_disabled(self, name: str) -> bool:
        return _normalize_module_name(name) in self.disabled

    def should_autostart(self, name: str) -> bool:
        return _normalize_module_name(name) not in self.disabled


manager = ModulesManager()

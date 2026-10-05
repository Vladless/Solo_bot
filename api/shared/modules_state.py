import pkgutil

from pathlib import Path

from utils.modules_loader import _is_safe_module_name
from utils.modules_manager import manager


MODULES_DIR = Path(__file__).resolve().parents[2] / "modules"


def available_module_names() -> list[str]:
    """Имена модулей из папки modules, прошедшие проверку безопасности."""
    candidates: set[str] = set()
    if MODULES_DIR.is_dir():
        for _finder, name, _ispkg in pkgutil.iter_modules([str(MODULES_DIR)]):
            name = (name or "").strip()
            if name and _is_safe_module_name(name):
                candidates.add(name)
    return sorted(candidates)


def prune_missing_state(installed: set[str]) -> None:
    """Удаляет из состояния менеджера модули, которых нет в файловой системе."""
    changed = False
    stale_disabled = {name for name in manager.disabled if name not in installed}
    if stale_disabled:
        for name in stale_disabled:
            manager.disabled.discard(name)
        changed = True
    stale_registry = [name for name in list(manager.registry.keys()) if name not in installed]
    if stale_registry:
        for name in stale_registry:
            manager.registry.pop(name, None)
        changed = True
    if changed:
        save_state = getattr(manager, "_save_state", None)
        if callable(save_state):
            save_state()


def module_state(name: str) -> dict:
    """Состояние модуля: enabled, loaded, autostart."""
    normalized = name.strip()
    record = manager.registry.get(normalized)
    return {
        "name": normalized,
        "enabled": manager.is_enabled(normalized),
        "loaded": bool(record and record.enabled),
        "autostart": manager.should_autostart(normalized),
    }


def read_local_module_version(name: str) -> str | None:
    """Читает VERSION из папки модуля."""
    version_file = MODULES_DIR / name / "VERSION"
    if not version_file.exists() or not version_file.is_file():
        return None
    try:
        with version_file.open("r", encoding="utf-8") as handle:
            for line in handle:
                value = line.strip()
                if value:
                    return value
    except Exception:
        return None
    return None


def sync_list_modules() -> list:
    """Вся синхронная работа со списком модулей (файлы, состояние). Вызывать через run_io()."""
    refresh = getattr(manager, "refresh_state", None) or getattr(manager, "_load_state", None)
    if callable(refresh):
        refresh()
    module_names = available_module_names()
    prune_missing_state(set(module_names))
    modules = [module_state(name) for name in module_names]
    for item in modules:
        item["local_version"] = read_local_module_version(str(item.get("name") or "").strip())
    return modules

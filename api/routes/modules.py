import pkgutil
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.depends import verify_admin_token
from utils.modules_loader import _is_safe_module_name
from utils.modules_manager import manager


router = APIRouter(prefix="/modules", tags=["Modules"])


MODULES_DIR = Path(__file__).resolve().parents[2] / "modules"


class ModuleAction(BaseModel):
    action: Literal["start", "stop", "restart"]


def _available_module_names() -> list[str]:
    candidates: set[str] = set()
    if MODULES_DIR.is_dir():
        for _finder, name, _ispkg in pkgutil.iter_modules([str(MODULES_DIR)]):
            name = (name or "").strip()
            if name and _is_safe_module_name(name):
                candidates.add(name)

    return sorted(n for n in candidates if _is_safe_module_name(n))


def _prune_missing_state(installed: set[str]) -> None:
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


def _module_state(name: str) -> dict:
    normalized = name.strip()
    record = manager.registry.get(normalized)
    is_enabled = manager.is_enabled(normalized)
    return {
        "name": normalized,
        "enabled": is_enabled,
        "loaded": bool(record and record.enabled),
        "autostart": manager.should_autostart(normalized),
    }


def _read_local_module_version(name: str) -> str | None:
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


@router.get("/")
async def list_modules(admin=Depends(verify_admin_token)):
    refresh = getattr(manager, "refresh_state", None)
    if callable(refresh):
        refresh()
    else:
        legacy_refresh = getattr(manager, "_load_state", None)
        if callable(legacy_refresh):
            legacy_refresh()
    module_names = _available_module_names()
    _prune_missing_state(set(module_names))
    modules = [_module_state(name) for name in module_names]

    for item in modules:
        name = str(item.get("name") or "").strip()
        local_version = _read_local_module_version(name)
        item["local_version"] = local_version

    return {"items": modules}


@router.post("/{module_name}/actions")
async def control_module(module_name: str, payload: ModuleAction, admin=Depends(verify_admin_token)):
    name = (module_name or "").strip()
    if not _is_safe_module_name(name):
        raise HTTPException(status_code=404, detail="Module not found")

    try:
        if payload.action == "start":
            await manager.start(name)
        elif payload.action == "stop":
            await manager.stop(name)
        elif payload.action == "restart":
            await manager.restart(name)
        else:
            raise HTTPException(status_code=400, detail="Unsupported action")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"item": _module_state(name)}

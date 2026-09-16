from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.depends import verify_identity_admin
from api.shared.modules_state import module_state, sync_list_modules
from core.executor import run_io
from logger import logger
from utils.modules_loader import _is_safe_module_name
from utils.modules_manager import manager


router = APIRouter(prefix="/modules", tags=["Modules"])


class ModuleAction(BaseModel):
    action: Literal["start", "stop", "restart"]


@router.get("/")
async def list_modules(identity=Depends(verify_identity_admin)):
    """Список модулей с состоянием и локальной версией."""
    modules = await run_io(sync_list_modules)
    return {"items": modules}


@router.post("/{module_name}/actions")
async def control_module(module_name: str, payload: ModuleAction, identity=Depends(verify_identity_admin)):
    """Запуск, остановка или перезапуск модуля."""
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
        logger.error("[Modules] action failed for {}: {}", name, exc)
        raise HTTPException(status_code=500, detail="Ошибка при выполнении операции модуля") from exc
    return {"item": module_state(name)}

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_session, verify_admin_token
from api.schemas.settings import SettingResponse, SettingUpsert
from database.models import Setting
from database.settings import set_setting
from core.settings.buttons_config import BUTTONS_CONFIG, update_buttons_config
from core.settings.modes_config import MODES_CONFIG, update_modes_config
from core.settings.money_config import MONEY_CONFIG, update_money_config
from core.settings.notifications_config import NOTIFICATIONS_CONFIG, update_notifications_config
from core.settings.payments_config import PAYMENTS_CONFIG, update_payments_config
from core.settings.providers_order_config import PROVIDERS_ORDER, update_providers_order
from core.settings.tariffs_config import TARIFFS_CONFIG, update_tariffs_config
from pydantic import BaseModel


router = APIRouter()


class ConfigUpdatePayload(BaseModel):
    value: dict[str, Any] | None = None


@router.get("/", response_model=list[SettingResponse])
async def get_all_settings(
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(Setting))
    return result.scalars().all()


@router.get("/configs")
async def get_configs(admin=Depends(verify_admin_token)):
    return {
        "payments": dict(PAYMENTS_CONFIG),
        "buttons": dict(BUTTONS_CONFIG),
        "notifications": dict(NOTIFICATIONS_CONFIG),
        "modes": dict(MODES_CONFIG),
        "money": dict(MONEY_CONFIG),
        "providers_order": dict(PROVIDERS_ORDER),
        "tariffs": dict(TARIFFS_CONFIG),
    }


@router.post("/configs/{scope}")
async def update_config_scope(
    scope: str,
    payload: ConfigUpdatePayload,
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    data = dict(payload.value or {})
    normalized = scope.strip().lower().replace("-", "_")

    if normalized == "payments":
        cleaned = {key: bool(value) for key, value in data.items()}
        await update_payments_config(session, cleaned)
        return {"payments": dict(PAYMENTS_CONFIG)}

    if normalized == "buttons":
        cleaned = {key: bool(value) for key, value in data.items()}
        await update_buttons_config(session, cleaned)
        return {"buttons": dict(BUTTONS_CONFIG)}

    if normalized == "notifications":
        await update_notifications_config(session, data)
        return {"notifications": dict(NOTIFICATIONS_CONFIG)}

    if normalized == "modes":
        cleaned = {key: bool(value) for key, value in data.items()}
        await update_modes_config(session, cleaned)
        return {"modes": dict(MODES_CONFIG)}

    if normalized == "money":
        await update_money_config(session, data)
        return {"money": dict(MONEY_CONFIG)}

    if normalized == "providers_order":
        cleaned: dict[str, int] = {}
        for key, value in data.items():
            try:
                cleaned[key] = int(value)
            except (TypeError, ValueError):
                continue
        await update_providers_order(session, cleaned)
        return {"providers_order": dict(PROVIDERS_ORDER)}

    if normalized == "tariffs":
        cleaned = dict(data)
        if "ALLOW_DOWNGRADE" in cleaned:
            cleaned["ALLOW_DOWNGRADE"] = bool(cleaned.get("ALLOW_DOWNGRADE"))
        if "KEY_ADDONS_RECALC_PRICE" in cleaned:
            cleaned["KEY_ADDONS_RECALC_PRICE"] = bool(cleaned.get("KEY_ADDONS_RECALC_PRICE"))
        if "KEY_ADDONS_PACK_MODE" in cleaned:
            mode = str(cleaned.get("KEY_ADDONS_PACK_MODE") or "").strip().lower()
            cleaned["KEY_ADDONS_PACK_MODE"] = mode if mode in {"", "traffic", "devices", "all"} else ""
        await update_tariffs_config(session, cleaned)
        return {"tariffs": dict(TARIFFS_CONFIG)}

    raise HTTPException(status_code=404, detail="Unsupported config scope")


@router.get("/{key}", response_model=SettingResponse)
async def get_setting_by_key(
    key: str,
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(Setting).where(Setting.key == key))
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail="Setting not found")
    return obj


@router.post("/{key}", response_model=SettingResponse)
async def upsert_setting(
    key: str,
    payload: SettingUpsert,
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    obj = await set_setting(
        session=session,
        key=key,
        value=payload.value,
        description=payload.description,
    )
    await session.commit()
    await session.refresh(obj)
    return obj


@router.delete("/{key}", response_model=dict)
async def delete_setting(
    key: str,
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(Setting).where(Setting.key == key))
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail="Setting not found")
    await session.delete(obj)
    await session.commit()
    return {"detail": "Setting deleted"}



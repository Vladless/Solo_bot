from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_session, verify_identity_admin
from api.v2.schemas import SettingResponse, SettingUpsert
from core.settings.buttons_config import BUTTONS_CONFIG, update_buttons_config
from core.settings.management_config import MANAGEMENT_CONFIG, update_management_config
from core.settings.modes_config import MODES_CONFIG, update_modes_config
from core.settings.money_config import MONEY_CONFIG, update_money_config
from core.settings.notifications_config import NOTIFICATIONS_CONFIG, update_notifications_config
from core.settings.payments_config import PAYMENTS_CONFIG, update_payments_config
from core.settings.providers_order_config import PROVIDERS_ORDER, update_providers_order
from core.settings.remnawave_config import REMNAWAVE_CONFIG, update_remnawave_config
from core.settings.tariffs_config import TARIFFS_CONFIG, update_tariffs_config
from core.settings.web_config import WEB_CONFIG, update_web_config
from database.models import Setting
from database.settings import set_setting
from database.settings_cache import settings_cache
from handlers.admin.settings.settings_config import (
    BUTTON_TITLES,
    MANAGEMENT_TITLES,
    MODES_TITLES,
    MONEY_FIELDS,
    NOTIFICATION_TIME_FIELDS,
    NOTIFICATION_TITLES,
    PAYMENT_PROVIDER_TITLES,
    REMNAWAVE_TITLES,
    TARIFFS_TITLES,
    WEB_TITLES,
)
from handlers.admin.settings.settings_descriptions import SECTION_DESCRIPTIONS, SETTING_HINTS


router = APIRouter()


class ConfigUpdatePayload(BaseModel):
    value: dict[str, Any] | None = None


@router.get("/", response_model=list[SettingResponse])
async def get_all_settings(identity=Depends(verify_identity_admin)):
    """Список всех настроек (из кэша, без запроса к БД)."""
    return settings_cache.get_all()


@router.get("/configs")
async def get_configs(identity=Depends(verify_identity_admin)):
    """Все конфиги: payments, buttons, notifications, modes, money,
    providers_order, tariffs, web, remnawave, management."""
    return {
        "payments": dict(PAYMENTS_CONFIG),
        "buttons": dict(BUTTONS_CONFIG),
        "notifications": dict(NOTIFICATIONS_CONFIG),
        "modes": dict(MODES_CONFIG),
        "money": dict(MONEY_CONFIG),
        "providers_order": dict(PROVIDERS_ORDER),
        "tariffs": dict(TARIFFS_CONFIG),
        "web": dict(WEB_CONFIG),
        "remnawave": dict(REMNAWAVE_CONFIG),
        "management": dict(MANAGEMENT_CONFIG),
    }


_SCHEMA_SECTIONS: list[tuple[str, str, dict, dict]] = [
    ("payments", "Кассы", PAYMENTS_CONFIG, PAYMENT_PROVIDER_TITLES),
    ("money", "Деньги", MONEY_CONFIG, MONEY_FIELDS),
    ("buttons", "Кнопки", BUTTONS_CONFIG, BUTTON_TITLES),
    ("notifications", "Уведомления", NOTIFICATIONS_CONFIG, {**NOTIFICATION_TITLES, **NOTIFICATION_TIME_FIELDS}),
    ("modes", "Режимы", MODES_CONFIG, MODES_TITLES),
    ("tariffs", "Тарификация", TARIFFS_CONFIG, TARIFFS_TITLES),
    ("web", "Сайт", WEB_CONFIG, WEB_TITLES),
    ("remnawave", "Remnawave", REMNAWAVE_CONFIG, REMNAWAVE_TITLES),
    ("management", "Управление", MANAGEMENT_CONFIG, MANAGEMENT_TITLES),
]

_SCHEMA_HIDDEN_KEYS = {
    "NODE_HEALTH_LAST_STATES",
    "CLIENT_CONNECTION_TARGETS",
    "SQUAD_INBOUNDS",
    "HOST_AUTO_DISABLED",
    "KEY_ADDONS_PRICE_BASE_MODE",
}

_FIELD_LABELS: dict[str, str] = {
    "CURRENCY_MODE": "Режим валют",
}

_FIELD_OPTIONS: dict[str, list[dict[str, str]]] = {
    "KEY_ADDONS_PACK_MODE": [
        {"value": "", "label": "Выключено"},
        {"value": "traffic", "label": "Только трафик"},
        {"value": "devices", "label": "Только устройства"},
        {"value": "all", "label": "Трафик и устройства"},
    ],
    "CURRENCY_MODE": [
        {"value": "RUB", "label": "Только рубли"},
        {"value": "USD", "label": "Только доллары"},
        {"value": "RUB+USD", "label": "Рубли + доллары (два экрана)"},
        {"value": "RUB+USD_ONE_SCREEN", "label": "Рубли + доллары (один экран)"},
    ],
    "SITE_MODE": [
        {"value": "full", "label": "Полный сайт"},
        {"value": "cabinet_only", "label": "Только кабинет"},
    ],
}


def _field_type(value: Any) -> str | None:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "text" if "\n" in value or len(value) > 80 else "string"
    return None


@router.get("/schema")
async def get_settings_schema(identity=Depends(verify_identity_admin)):
    """Схема настроек бота для веб-админки: секции, ключи, названия, типы, текущие значения."""
    sections = []
    for scope, title, config, titles in _SCHEMA_SECTIONS:
        fields = []
        for key, value in config.items():
            if key in _SCHEMA_HIDDEN_KEYS:
                continue
            options = _FIELD_OPTIONS.get(key)
            if key not in titles and options is None:
                continue
            field_type = "enum" if options else _field_type(value)
            if field_type is None:
                continue
            field: dict[str, Any] = {
                "key": key,
                "label": titles.get(key) or _FIELD_LABELS.get(key, key),
                "type": field_type,
                "value": value,
            }
            hint = SETTING_HINTS.get(key)
            if hint:
                field["hint"] = hint
            if options:
                field["options"] = options
            fields.append(field)
        sections.append({
            "scope": scope,
            "title": title,
            "description": SECTION_DESCRIPTIONS.get(scope, ""),
            "fields": fields,
        })
    return {"sections": sections}


@router.post("/configs/{scope}")
async def update_config_scope(
    scope: str,
    payload: ConfigUpdatePayload,
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Обновление конфига по scope (payments, buttons, notifications, modes, money, providers_order, tariffs)."""
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
    if normalized == "web":
        merged = dict(WEB_CONFIG)
        merged.update(data)
        await update_web_config(session, merged)
        return {"web": dict(WEB_CONFIG)}
    if normalized == "remnawave":
        merged = dict(REMNAWAVE_CONFIG)
        merged.update({k: v for k, v in data.items() if k not in _SCHEMA_HIDDEN_KEYS})
        await update_remnawave_config(session, merged)
        return {"remnawave": dict(REMNAWAVE_CONFIG)}
    if normalized == "management":
        merged = dict(MANAGEMENT_CONFIG)
        merged.update(data)
        await update_management_config(session, merged)
        return {"management": dict(MANAGEMENT_CONFIG)}
    raise HTTPException(status_code=404, detail="Unsupported config scope")


@router.get("/{key}", response_model=SettingResponse)
async def get_setting_by_key(key: str, identity=Depends(verify_identity_admin)):
    """Настройка по ключу (из кэша, без запроса к БД)."""
    obj = settings_cache.get(key)
    if not obj:
        raise HTTPException(status_code=404, detail="Setting not found")
    return obj


@router.post("/{key}", response_model=SettingResponse)
async def upsert_setting(
    key: str,
    payload: SettingUpsert,
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Создание или обновление настройки по ключу."""
    obj = await set_setting(
        session=session,
        key=key,
        value=payload.value,
        description=payload.description,
    )
    await session.refresh(obj)
    settings_cache.update(
        key,
        obj.value,
        obj.description,
        created_at=getattr(obj, "created_at", None),
        updated_at=getattr(obj, "updated_at", None),
    )
    return obj


@router.delete("/{key}", response_model=dict)
async def delete_setting(
    key: str,
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Удаление настройки по ключу."""
    result = await session.execute(select(Setting).where(Setting.key == key))
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail="Setting not found")
    await session.delete(obj)
    settings_cache.delete(key)
    return {"detail": "Setting deleted"}

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.admin_permissions import require_full_admin
from api.depends import get_session, verify_identity_admin
from api.v2.schemas import SettingResponse, SettingUpsert
from core.settings.bonus_config import (
    BONUS_CONFIG,
    BONUS_HINTS,
    BONUS_MODES,
    BONUS_MODE_OPTIONS,
    BONUS_SECTION_DESCRIPTION,
    BONUS_SECTION_SLUG,
    BONUS_SECTION_TITLE,
    BONUS_TITLES,
    update_bonus_config,
)
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
from core.settings.yookassa_autopay_config import (
    AUTOPAY_SETTING_LIMITS,
    YOOKASSA_AUTOPAY_CONFIG,
    update_yookassa_autopay_config,
    validate_yookassa_autopay_settings,
)
from core.settings.yookassa_config import YOOKASSA_CONFIG, update_yookassa_config
from database.models import Setting
from database.settings import set_setting
from database.settings_cache import settings_cache
from handlers.admin.settings.settings_config import (
    ADMIN_NOTIFICATION_TITLES,
    BUTTON_TITLES,
    MANAGEMENT_TITLES,
    MODES_TITLES,
    MONEY_FIELDS,
    NOTIFICATION_TIME_FIELDS,
    NOTIFICATION_TITLES,
    PAYMENT_CASHBOX_GROUPS,
    PAYMENT_CASHBOX_TITLES,
    PAYMENT_PROVIDER_TITLES,
    REMNAWAVE_TITLES,
    TARIFFS_TITLES,
    WEB_TITLES,
    YOOKASSA_AUTOPAY_OPTIONS,
    YOOKASSA_AUTOPAY_TITLES,
    YOOKASSA_TITLES,
)
from handlers.admin.settings.settings_descriptions import SECTION_DESCRIPTIONS, SETTING_HINTS
from settings.texts import (
    SETTING_INVALID_VALUE,
    TRAFFIC_SETTINGS_INVALID,
    YOOKASSA_MARKUP_INVALID,
)


router = APIRouter()


class ConfigUpdatePayload(BaseModel):
    value: dict[str, Any] | None = None


@router.get("/", response_model=list[SettingResponse])
async def get_all_settings(identity=Depends(verify_identity_admin)):
    """Список всех настроек (из кэша, без запроса к БД)."""
    return settings_cache.get_all()


@router.get("/configs")
async def get_configs(identity=Depends(verify_identity_admin)):
    """Возвращает текущие настройки по разделам."""
    return {
        "payments": dict(PAYMENTS_CONFIG),
        "buttons": dict(BUTTONS_CONFIG),
        BONUS_SECTION_SLUG: dict(BONUS_CONFIG),
        "notifications": dict(NOTIFICATIONS_CONFIG),
        "modes": dict(MODES_CONFIG),
        "money": dict(MONEY_CONFIG),
        "providers_order": dict(PROVIDERS_ORDER),
        "tariffs": dict(TARIFFS_CONFIG),
        "web": dict(WEB_CONFIG),
        "remnawave": dict(REMNAWAVE_CONFIG),
        "management": dict(MANAGEMENT_CONFIG),
        "yookassa": dict(YOOKASSA_CONFIG),
        "yookassa_autopay": {
            key: value for key, value in YOOKASSA_AUTOPAY_CONFIG.items() if key in YOOKASSA_AUTOPAY_TITLES
        },
    }


_SCHEMA_SECTIONS: list[tuple[str, str, dict, dict]] = [
    ("payments", "Кассы", PAYMENTS_CONFIG, PAYMENT_PROVIDER_TITLES),
    ("money", "Деньги", MONEY_CONFIG, MONEY_FIELDS),
    ("buttons", "Кнопки", BUTTONS_CONFIG, BUTTON_TITLES),
    (
        "notifications",
        "Уведомления",
        NOTIFICATIONS_CONFIG,
        {**NOTIFICATION_TITLES, **ADMIN_NOTIFICATION_TITLES, **NOTIFICATION_TIME_FIELDS},
    ),
    ("modes", "Режимы", MODES_CONFIG, MODES_TITLES),
    ("tariffs", "Тарификация", TARIFFS_CONFIG, TARIFFS_TITLES),
    ("web", "Сайт", WEB_CONFIG, WEB_TITLES),
    (BONUS_SECTION_SLUG, BONUS_SECTION_TITLE, BONUS_CONFIG, BONUS_TITLES),
    ("remnawave", "Remnawave", REMNAWAVE_CONFIG, REMNAWAVE_TITLES),
    ("management", "Управление", MANAGEMENT_CONFIG, MANAGEMENT_TITLES),
]

_EXTRA_SECTION_DESCRIPTIONS: dict[str, str] = {BONUS_SECTION_SLUG: BONUS_SECTION_DESCRIPTION}

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
        {"value": "webapp_only", "label": "Только веб-апп"},
    ],
    "DAILY_BONUS_MODE": BONUS_MODE_OPTIONS,
    **YOOKASSA_AUTOPAY_OPTIONS,
}

_FIELD_NUMBER_LIMITS: dict[str, dict[str, int | float]] = {
    key: {"min": minimum, "max": maximum} for key, (minimum, maximum) in AUTOPAY_SETTING_LIMITS.items()
}


def _field_type(value: Any) -> str | None:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "text" if "\n" in value or len(value) > 80 else "string"
    return None


def _schema_fields(config: dict, titles: dict, scope: str | None = None) -> list[dict[str, Any]]:
    """Описывает поля раздела и место их сохранения."""
    fields = []
    for key, value in config.items():
        if key in _SCHEMA_HIDDEN_KEYS:
            continue
        options = _FIELD_OPTIONS.get(key)
        if key not in titles and options is None:
            continue
        if key == "QUICK_AMOUNTS" and isinstance(value, list | tuple):
            value = ", ".join(str(amount) for amount in value)
        field_type = "enum" if options else _field_type(value)
        if scope == "yookassa" and key == "MARKUP_PERCENT":
            field_type = "number"
        if field_type is None:
            continue
        field: dict[str, Any] = {
            "key": key,
            "label": titles.get(key) or _FIELD_LABELS.get(key, key),
            "type": field_type,
            "value": value,
        }
        if scope:
            field["scope"] = scope
        if scope == "yookassa" and key == "MARKUP_PERCENT":
            field.update(min=0, max=100)
        field.update(_FIELD_NUMBER_LIMITS.get(key, {}))
        hint = SETTING_HINTS.get(key) or BONUS_HINTS.get(key)
        if hint:
            field["hint"] = hint
        if options:
            field["options"] = options
        fields.append(field)
    return fields


@router.get("/schema")
async def get_settings_schema(identity=Depends(verify_identity_admin)):
    """Возвращает схему настроек веб-админки."""
    sections = []
    for scope, title, config, titles in _SCHEMA_SECTIONS:
        fields = _schema_fields(config, titles)
        section = {
            "scope": scope,
            "title": title,
            "description": SECTION_DESCRIPTIONS.get(scope) or _EXTRA_SECTION_DESCRIPTIONS.get(scope, ""),
            "fields": fields,
        }
        if scope == "payments":
            children = []
            for cashbox, providers in PAYMENT_CASHBOX_GROUPS.items():
                cashbox_fields = _schema_fields(
                    {key: PAYMENTS_CONFIG[key] for key in providers if key in PAYMENTS_CONFIG},
                    PAYMENT_PROVIDER_TITLES,
                    "payments",
                )
                description = ""
                if cashbox == "YOOKASSA":
                    cashbox_fields.extend(_schema_fields(YOOKASSA_CONFIG, YOOKASSA_TITLES, "yookassa"))
                    cashbox_fields.extend(
                        _schema_fields(YOOKASSA_AUTOPAY_CONFIG, YOOKASSA_AUTOPAY_TITLES, "yookassa_autopay")
                    )
                    description = SECTION_DESCRIPTIONS.get("yookassa", "")
                children.append({
                    "scope": f"payment_cashbox_{cashbox.lower()}",
                    "title": PAYMENT_CASHBOX_TITLES[cashbox],
                    "description": description,
                    "fields": cashbox_fields,
                })
            section["children"] = children
        sections.append(section)
    return {"sections": sections}


@router.post("/configs/{scope}")
async def update_config_scope(
    scope: str,
    payload: ConfigUpdatePayload,
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Сохраняет настройки выбранного раздела."""
    data = dict(payload.value or {})
    normalized = scope.strip().lower().replace("-", "_")
    if normalized == "payments":
        cleaned = {**PAYMENTS_CONFIG, **{key: bool(value) for key, value in data.items()}}
        await update_payments_config(session, cleaned)
        return {"payments": dict(PAYMENTS_CONFIG)}
    if normalized == "buttons":
        cleaned = {**BUTTONS_CONFIG, **{key: bool(value) for key, value in data.items()}}
        await update_buttons_config(session, cleaned)
        return {"buttons": dict(BUTTONS_CONFIG)}
    if normalized == "notifications":
        boolean_keys = {*NOTIFICATION_TITLES, *ADMIN_NOTIFICATION_TITLES}
        if any(key in boolean_keys and not isinstance(value, bool) for key, value in data.items()):
            raise HTTPException(status_code=400, detail=SETTING_INVALID_VALUE)
        try:
            await update_notifications_config(session, data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=TRAFFIC_SETTINGS_INVALID) from exc
        return {"notifications": dict(NOTIFICATIONS_CONFIG)}
    if normalized == "yookassa":
        if "MARKUP_ENABLED" in data and not isinstance(data["MARKUP_ENABLED"], bool):
            raise HTTPException(status_code=400, detail=SETTING_INVALID_VALUE)
        try:
            await update_yookassa_config(session, data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=YOOKASSA_MARKUP_INVALID) from exc
        return {"yookassa": dict(YOOKASSA_CONFIG)}
    if normalized == "yookassa_autopay":
        await require_full_admin(session, identity)
        if any(key not in YOOKASSA_AUTOPAY_TITLES for key in data):
            raise HTTPException(status_code=400, detail=SETTING_INVALID_VALUE)
        try:
            values = validate_yookassa_autopay_settings(data)
            await update_yookassa_autopay_config(session, values)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "yookassa_autopay": {
                key: value for key, value in YOOKASSA_AUTOPAY_CONFIG.items() if key in YOOKASSA_AUTOPAY_TITLES
            }
        }
    if normalized == "modes":
        if "SINGLE_SUBSCRIPTION_OPEN_PROFILE" in data and not isinstance(
            data["SINGLE_SUBSCRIPTION_OPEN_PROFILE"], bool
        ):
            raise HTTPException(status_code=400, detail=SETTING_INVALID_VALUE)
        cleaned = {**MODES_CONFIG, **{key: bool(value) for key, value in data.items()}}
        await update_modes_config(session, cleaned)
        return {"modes": dict(MODES_CONFIG)}
    if normalized == "money":
        await update_money_config(session, {**MONEY_CONFIG, **data})
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
        cleaned = {**TARIFFS_CONFIG, **data}
        if "ALLOW_DOWNGRADE" in cleaned:
            cleaned["ALLOW_DOWNGRADE"] = bool(cleaned.get("ALLOW_DOWNGRADE"))
        if "KEY_ADDONS_RECALC_PRICE" in cleaned:
            cleaned["KEY_ADDONS_RECALC_PRICE"] = bool(cleaned.get("KEY_ADDONS_RECALC_PRICE"))
        if "KEY_ADDONS_PACK_MODE" in cleaned:
            mode = str(cleaned.get("KEY_ADDONS_PACK_MODE") or "").strip().lower()
            cleaned["KEY_ADDONS_PACK_MODE"] = mode if mode in {"", "traffic", "devices", "all"} else ""
        await update_tariffs_config(session, cleaned)
        return {"tariffs": dict(TARIFFS_CONFIG)}
    if normalized == BONUS_SECTION_SLUG:
        merged = dict(BONUS_CONFIG)
        merged.update(data)
        merged["DAILY_BONUS_ENABLED"] = bool(merged.get("DAILY_BONUS_ENABLED"))
        merged["DAILY_BONUS_REQUIRE_SUBSCRIPTION"] = bool(merged.get("DAILY_BONUS_REQUIRE_SUBSCRIPTION"))
        merged["DAILY_BONUS_STREAK_RESTART"] = bool(merged.get("DAILY_BONUS_STREAK_RESTART"))
        mode = str(merged.get("DAILY_BONUS_MODE") or "fixed").strip().lower()
        merged["DAILY_BONUS_MODE"] = mode if mode in BONUS_MODES else "fixed"
        await update_bonus_config(session, merged)
        return {BONUS_SECTION_SLUG: dict(BONUS_CONFIG)}
    if normalized == "web":
        if "EMAIL_BINDING_REMINDER_ENABLED" in data and not isinstance(data["EMAIL_BINDING_REMINDER_ENABLED"], bool):
            raise HTTPException(
                status_code=400, detail="Включение напоминания о почте должно быть логическим значением"
            )
        if "EMAIL_BINDING_REMINDER_INTERVAL_DAYS" in data:
            interval = data["EMAIL_BINDING_REMINDER_INTERVAL_DAYS"]
            if isinstance(interval, bool) or not isinstance(interval, int) or not 1 <= interval <= 365:
                raise HTTPException(
                    status_code=400, detail="Интервал напоминания о почте: целое число от 1 до 365 дней"
                )
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
        if "ADMIN_GRANULAR_PERMISSIONS_ENABLED" in data and not isinstance(
            data["ADMIN_GRANULAR_PERMISSIONS_ENABLED"], bool
        ):
            raise HTTPException(status_code=400, detail=SETTING_INVALID_VALUE)
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

import html

from datetime import datetime, timezone

import pytz

from handlers.admin.panel.headers import strip_tags
from handlers.utils import (
    fill_text,
    render_text,
)
from services.tariffs.tariff_display import plain_tariff_name
from settings.texts import (
    KEY_VIEW_TEXT,
    MY_DEVICES_CARD_TEXT,
    SINGLE_SUB_EXPIRED_STATUS,
    SINGLE_SUB_FOOTER,
    SINGLE_SUB_FOOTER_EXPIRED,
    SINGLE_SUB_TEXT,
    UNLIMITED_ROW_VALUE,
)


moscow_tz = pytz.timezone("Europe/Moscow")


def build_key_view_text(
    key: str,
    formatted_expiry_date: str,
    days_left_message: str,
    country: str | None = None,
    hwid_count: int = 0,
    tariff_name: str = "",
    traffic_limit: int = 0,
    device_limit: int = 0,
    is_remnawave: bool = False,
    remna_used_gb: float | None = None,
) -> str:
    """Собирает экран подписки по шаблону из файла текстов."""
    traffic = traffic_limit or 0
    devices = device_limit or 0
    expiry = formatted_expiry_date.replace(" года", "").strip()
    left = strip_tags(days_left_message).strip()
    is_expired = "Осталось" not in left

    return render_text(
        KEY_VIEW_TEXT,
        key=key,
        left="" if is_expired else left.split(":", 1)[-1].split(",")[0].strip(),
        expiry="" if is_expired else expiry,
        expired_at=expiry if is_expired else "",
        tariff_name=plain_tariff_name(tariff_name),
        traffic=f"{traffic} ГБ" if traffic > 0 else UNLIMITED_ROW_VALUE,
        used=f"{remna_used_gb} ГБ" if is_remnawave and traffic > 0 and remna_used_gb is not None else "",
        devices=devices if devices > 0 else UNLIMITED_ROW_VALUE,
        connected=hwid_count if devices > 0 and hwid_count > 0 else "",
        country=country or "",
    )


def build_single_subscription_text(
    username: str,
    tg_id: int,
    balance: str,
    key: str,
    tariff_name: str = "",
    traffic_limit: int = 0,
    used_traffic_gb: float | None = None,
    device_limit: int = 0,
    base_device_limit: int = 0,
    hwid_count: int = 0,
    expiry_date: str = "",
    is_expired: bool = False,
) -> str:
    """Собирает кабинет с единственной подпиской по шаблону из файла текстов."""
    if traffic_limit and traffic_limit > 0:
        traffic = f"{used_traffic_gb} / {traffic_limit} ГБ" if used_traffic_gb is not None else f"{traffic_limit} ГБ"
    else:
        traffic = UNLIMITED_ROW_VALUE

    if device_limit and device_limit > 0:
        addon_devices = device_limit - base_device_limit if base_device_limit else 0
        devices = f"{base_device_limit} + {addon_devices}" if addon_devices > 0 else str(device_limit)
    else:
        devices = UNLIMITED_ROW_VALUE

    return render_text(
        SINGLE_SUB_TEXT,
        username=username,
        tg_id=tg_id,
        balance=balance,
        key=key,
        expiry="" if is_expired else expiry_date.replace(" года", "").strip(),
        expired=SINGLE_SUB_EXPIRED_STATUS if is_expired else "",
        tariff_name=plain_tariff_name(tariff_name),
        traffic=traffic,
        devices=devices,
        connected=hwid_count if hwid_count and not is_expired else "",
        footer=SINGLE_SUB_FOOTER_EXPIRED if is_expired else SINGLE_SUB_FOOTER,
    )


def _format_device_date(raw) -> str:
    value = str(raw or "")[:19].replace("T", " ")
    if not value:
        return "—"
    try:
        parsed = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except ValueError:
        return html.escape(value)
    return parsed.astimezone(moscow_tz).strftime("%d.%m.%Y %H:%M")


def _format_device_hwid(raw) -> str:
    hwid = str(raw or "")
    if not hwid:
        return "—"
    return html.escape(hwid if len(hwid) <= 16 else f"{hwid[:8]}…{hwid[-4:]}")


def _format_device_block(idx: int, device: dict) -> str:
    platform = html.escape(str(device.get("platform") or ""))
    os_version = html.escape(str(device.get("osVersion") or ""))
    return fill_text(
        MY_DEVICES_CARD_TEXT,
        index=idx,
        model=html.escape(str(device.get("deviceModel") or "—")),
        system=" · ".join(part for part in (platform, os_version) if part),
        created=_format_device_date(device.get("createdAt")),
        updated=_format_device_date(device.get("updatedAt")),
        hwid=_format_device_hwid(device.get("hwid")),
    )

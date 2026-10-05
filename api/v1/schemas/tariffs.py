from datetime import datetime
from typing import Any

from pydantic import BaseModel


class TariffBase(BaseModel):
    name: str
    group_code: str
    duration_days: int
    price_rub: int
    traffic_limit: int | None = None
    device_limit: int | None = None
    is_active: bool = True
    subgroup_title: str | None = None
    sort_order: int | None = None
    vless: bool = False
    external_squad: str | None = None

    configurable: bool = False

    device_options: list[int] | None = None
    traffic_options_gb: list[int] | None = None

    device_step_rub: int | None = None
    device_overrides: dict[str, int] | None = None

    traffic_step_rub: int | None = None
    traffic_overrides: dict[str, int] | None = None


class TariffResponse(TariffBase):
    id: int
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class TariffUpdate(BaseModel):
    name: str | None = None
    group_code: str | None = None
    duration_days: int | None = None
    price_rub: int | None = None
    traffic_limit: int | None = None
    device_limit: int | None = None
    is_active: bool | None = None
    subgroup_title: str | None = None
    sort_order: int | None = None
    vless: bool | None = None
    external_squad: str | None = None

    configurable: bool | None = None

    device_options: list[int] | None = None
    traffic_options_gb: list[int] | None = None

    device_step_rub: int | None = None
    device_overrides: dict[str, int] | None = None

    traffic_step_rub: int | None = None
    traffic_overrides: dict[str, int] | None = None

    class Config:
        from_attributes = True

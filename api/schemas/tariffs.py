from pydantic import BaseModel
from typing import Optional
from datetime import datetime



class TariffBase(BaseModel):
    name: str
    group_code: str
    duration_days: int
    price_rub: int
    traffic_limit: Optional[int] = None
    device_limit: Optional[int] = None
    is_active: bool = True
    subgroup_title: Optional[str] = None


class TariffResponse(TariffBase):
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class TariffUpdate(BaseModel):
    name: Optional[str] = None
    group_code: Optional[str] = None
    duration_days: Optional[int] = None
    price_rub: Optional[int] = None
    traffic_limit: Optional[int] = None
    device_limit: Optional[int] = None
    is_active: Optional[bool] = None
    subgroup_title: Optional[str] = None

    class Config:
        from_attributes = True
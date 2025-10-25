from datetime import datetime

from pydantic import BaseModel


class GiftBase(BaseModel):
    sender_tg_id: int
    recipient_tg_id: int | None = None
    selected_months: int | None = None
    expiry_time: datetime
    gift_link: str
    is_used: bool = False
    is_unlimited: bool | None = False
    max_usages: int | None = None
    tariff_id: int | None = None


class GiftResponse(GiftBase):
    gift_id: str
    created_at: datetime

    class Config:
        from_attributes = True


class GiftUsageResponse(BaseModel):
    gift_id: str
    tg_id: int
    used_at: datetime

    class Config:
        from_attributes = True


class GiftUpdate(BaseModel):
    recipient_tg_id: int | None = None
    selected_months: int | None = None
    expiry_time: datetime | None = None
    gift_link: str | None = None
    is_used: bool | None = None
    is_unlimited: bool | None = None
    max_usages: int | None = None
    tariff_id: int | None = None

    class Config:
        from_attributes = True

from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class GiftBase(BaseModel):
    sender_tg_id: int
    recipient_tg_id: Optional[int] = None
    selected_months: int
    expiry_time: datetime
    gift_link: str
    is_used: bool = False
    is_unlimited: bool = False
    max_usages: Optional[int] = None
    tariff_id: Optional[int] = None


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
from datetime import datetime

from pydantic import BaseModel, field_validator

from database.access.resolution import public_tg_id


class GiftBase(BaseModel):
    sender_user_id: int
    recipient_user_id: int | None = None
    selected_months: int | None = None
    expiry_time: datetime
    gift_link: str
    telegram_gift_link: str | None = None
    site_gift_link: str | None = None
    is_used: bool = False
    is_unlimited: bool | None = False
    max_usages: int | None = None
    tariff_id: int | None = None


class GiftResponse(GiftBase):
    sender_user_id: int | None
    gift_id: str
    created_at: datetime

    class Config:
        from_attributes = True


class GiftUsageResponse(BaseModel):
    gift_id: str
    user_id: int
    tg_id: int | None
    used_at: datetime

    _public_tg_id = field_validator("tg_id")(public_tg_id)

    class Config:
        from_attributes = True


class GiftUpdate(BaseModel):
    recipient_user_id: int | None = None
    selected_months: int | None = None
    expiry_time: datetime | None = None
    gift_link: str | None = None
    telegram_gift_link: str | None = None
    site_gift_link: str | None = None
    is_used: bool | None = None
    is_unlimited: bool | None = None
    max_usages: int | None = None
    tariff_id: int | None = None

    class Config:
        from_attributes = True

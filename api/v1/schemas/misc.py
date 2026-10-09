from datetime import datetime

from pydantic import BaseModel, field_validator

from database.access.resolution import public_tg_id


class PaymentBase(BaseModel):
    user_id: int
    tg_id: int | None = None
    amount: float
    payment_system: str
    status: str


class PaymentResponse(PaymentBase):
    user_id: int | None
    id: int
    created_at: datetime

    _public_tg_id = field_validator("tg_id")(public_tg_id)

    class Config:
        from_attributes = True


class ReferralResponse(BaseModel):
    referred_user_id: int
    referrer_user_id: int
    reward_issued: bool = False

    class Config:
        from_attributes = True


class NotificationResponse(BaseModel):
    user_id: int
    tg_id: int | None
    notification_type: str
    last_notification_time: datetime

    _public_tg_id = field_validator("tg_id")(public_tg_id)

    class Config:
        from_attributes = True


class GiftBase(BaseModel):
    sender_user_id: int
    recipient_user_id: int | None = None
    selected_months: int
    expiry_time: datetime
    gift_link: str
    telegram_gift_link: str | None = None
    site_gift_link: str | None = None
    is_used: bool = False
    is_unlimited: bool = False
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


class ManualBanResponse(BaseModel):
    user_id: int
    tg_id: int | None = None
    banned_at: datetime
    reason: str | None
    banned_by: int | None
    until: datetime | None = None

    _public_tg_id = field_validator("tg_id")(public_tg_id)

    class Config:
        from_attributes = True


class TemporaryDataResponse(BaseModel):
    user_id: int
    tg_id: int | None = None
    state: str
    data: dict
    updated_at: datetime

    _public_tg_id = field_validator("tg_id")(public_tg_id)

    class Config:
        from_attributes = True


class BlockedUserResponse(BaseModel):
    user_id: int
    tg_id: int | None = None

    _public_tg_id = field_validator("tg_id")(public_tg_id)

    class Config:
        from_attributes = True


class MonthlyStats(BaseModel):
    month: str
    registrations: int
    trials: int
    new_purchases_count: int
    new_purchases_amount: float
    repeat_purchases_count: int
    repeat_purchases_amount: float


class TrackingSourceResponse(BaseModel):
    id: int
    name: str
    code: str
    type: str
    created_by: int
    created_at: datetime

    registrations: int = 0
    trials: int = 0
    payments: int = 0
    total_amount: float = 0.0

    monthly: list[MonthlyStats] = []

    class Config:
        from_attributes = True

from datetime import datetime

from pydantic import BaseModel


class PaymentBase(BaseModel):
    tg_id: int
    amount: float
    payment_system: str
    status: str


class PaymentResponse(PaymentBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class ReferralResponse(BaseModel):
    referred_tg_id: int
    referrer_tg_id: int
    reward_issued: bool = False

    class Config:
        from_attributes = True


class NotificationResponse(BaseModel):
    tg_id: int
    notification_type: str
    last_notification_time: datetime

    class Config:
        from_attributes = True


class GiftBase(BaseModel):
    sender_tg_id: int
    recipient_tg_id: int | None = None
    selected_months: int
    expiry_time: datetime
    gift_link: str
    is_used: bool = False
    is_unlimited: bool = False
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


class ManualBanResponse(BaseModel):
    tg_id: int
    banned_at: datetime
    reason: str
    banned_by: int
    until: datetime | None = None

    class Config:
        from_attributes = True


class TemporaryDataResponse(BaseModel):
    tg_id: int
    state: str
    data: dict
    updated_at: datetime

    class Config:
        from_attributes = True


class BlockedUserResponse(BaseModel):
    tg_id: int

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

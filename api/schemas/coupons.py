from pydantic import BaseModel, Field, model_validator
from typing import Optional
from datetime import datetime


class CouponBase(BaseModel):
    code: str
    amount: Optional[int] = Field(default=None)
    usage_limit: int
    usage_count: int = 0
    is_used: bool = False
    days: Optional[int] = Field(default=None)

    @model_validator(mode="after")
    def check_exactly_one_of_amount_or_days(self) -> "CouponBase":
        has_amount = self.amount not in (None, 0)
        has_days = self.days is not None

        if has_amount and has_days:
            raise ValueError("Coupon must have exactly one of: 'amount' or 'days'")
        if not has_amount and not has_days:
            raise ValueError("Coupon must have exactly one of: 'amount' or 'days'")
        return self


class CouponResponse(CouponBase):
    id: int

    class Config:
        from_attributes = True


class CouponUpdate(BaseModel):
    code: Optional[str] = None
    amount: Optional[int] = None
    usage_limit: Optional[int] = None
    usage_count: Optional[int] = None
    is_used: Optional[bool] = None
    days: Optional[int] = Field(default=None)

    @model_validator(mode="after")
    def validate_amount_or_days(self) -> "CouponUpdate":
        if self.amount is None and self.days is None:
            return self
        if self.amount is not None and self.days is not None:
            raise ValueError("Specify only one of: 'amount' or 'days'")
        return self


class CouponUsageResponse(BaseModel):
    coupon_id: int
    user_id: int
    used_at: datetime

    class Config:
        from_attributes = True
from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class CouponBase(BaseModel):
    code: str
    amount: int | None = Field(default=None)
    usage_limit: int
    usage_count: int = 0
    is_used: bool = False
    days: int | None = Field(default=None)

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
    code: str | None = None
    amount: int | None = None
    usage_limit: int | None = None
    usage_count: int | None = None
    is_used: bool | None = None
    days: int | None = Field(default=None)

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

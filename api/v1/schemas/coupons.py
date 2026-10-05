from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class CouponBase(BaseModel):
    code: str
    amount: int | None = Field(default=None)
    usage_limit: int
    usage_count: int = 0
    is_used: bool = False
    days: int | None = Field(default=None)

    percent: int | None = Field(default=None)
    max_discount_amount: int | None = Field(default=None)
    min_order_amount: int | None = Field(default=None)

    new_users_only: bool = False

    @model_validator(mode="after")
    def check_coupon_type(self) -> "CouponBase":
        has_amount = self.amount not in (None, 0)
        has_days = self.days is not None
        has_percent = self.percent is not None

        provided = int(has_amount) + int(has_days) + int(has_percent)
        if provided != 1:
            raise ValueError("Coupon must have exactly one of: 'amount', 'days', 'percent'")

        if has_days and self.days is not None and self.days <= 0:
            raise ValueError("'days' must be > 0")

        if has_percent and self.percent is not None and not (1 <= self.percent <= 100):
            raise ValueError("'percent' must be between 1 and 100")

        if has_percent:
            if self.min_order_amount is not None and self.min_order_amount < 0:
                raise ValueError("'min_order_amount' must be >= 0")
            if self.max_discount_amount is not None and self.max_discount_amount < 0:
                raise ValueError("'max_discount_amount' must be >= 0")

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

    percent: int | None = None
    max_discount_amount: int | None = None
    min_order_amount: int | None = None

    new_users_only: bool | None = None

    @model_validator(mode="after")
    def validate_coupon_update(self) -> "CouponUpdate":
        has_amount = self.amount not in (None, 0)
        has_days = self.days is not None
        has_percent = self.percent is not None

        provided = int(has_amount) + int(has_days) + int(has_percent)
        if provided > 1:
            raise ValueError("Specify only one of: 'amount', 'days', 'percent'")

        if has_days and self.days is not None and self.days <= 0:
            raise ValueError("'days' must be > 0")

        if has_percent and self.percent is not None and not (1 <= self.percent <= 100):
            raise ValueError("'percent' must be between 1 and 100")

        if has_percent:
            if self.min_order_amount is not None and self.min_order_amount < 0:
                raise ValueError("'min_order_amount' must be >= 0")
            if self.max_discount_amount is not None and self.max_discount_amount < 0:
                raise ValueError("'max_discount_amount' must be >= 0")

        return self


class CouponUsageResponse(BaseModel):
    coupon_id: int
    user_id: int
    used_at: datetime

    class Config:
        from_attributes = True

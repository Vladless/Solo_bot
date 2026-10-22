from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class UserProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str | None = Field(None, max_length=64)
    first_name: str | None = Field(None, max_length=128)
    last_name: str | None = Field(None, max_length=128)

    @field_validator("username", "first_name", "last_name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        """Убирает пробелы и пустые строки профиля."""
        return value.strip() or None if value is not None else None


class UserBalanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Decimal = Field(..., max_digits=12, decimal_places=2, allow_inf_nan=False)

    @field_validator("amount")
    @classmethod
    def reject_zero(cls, value: Decimal) -> Decimal:
        """Запрещает пустую операцию с балансом."""
        if value == 0:
            raise ValueError("Сумма должна отличаться от нуля")
        return value

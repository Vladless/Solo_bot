from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


CabinetTab = Literal["keys", "profile", "instructions", "referrals", "partners", "gifts", "notifications"]


class CreateUserSubscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tariff_id: int = Field(..., gt=0, strict=True)
    cluster_id: str = Field(..., min_length=1, max_length=255)
    duration_days: int | None = Field(None, gt=0, le=36500, strict=True)
    selected_device_limit: int | None = Field(None, ge=0, le=1000000, strict=True)
    selected_traffic_gb: int | None = Field(None, ge=0, le=1000000000, strict=True)


class UserMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1, max_length=4096)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        """Запрещает пустое сообщение."""
        if not value.strip():
            raise ValueError("Введите текст сообщения")
        return value


class UserCabinetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tab: CabinetTab = "keys"

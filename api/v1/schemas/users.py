from datetime import datetime

from pydantic import BaseModel, field_validator

from database.access.resolution import public_tg_id


class UserBase(BaseModel):
    tg_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    language_code: str | None = None
    is_bot: bool | None = False
    balance: float | None = 0.0
    trial: int | None = 0
    source_code: str | None = None


class UserResponse(UserBase):
    id: int
    tg_id: int | None
    created_at: datetime | None
    updated_at: datetime | None

    _public_tg_id = field_validator("tg_id")(public_tg_id)

    class Config:
        from_attributes = True


class UserUpdate(BaseModel):
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    language_code: str | None = None
    is_bot: bool | None = None
    balance: float | None = None
    trial: int | None = None
    source_code: str | None = None

    class Config:
        from_attributes = True

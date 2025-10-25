from datetime import datetime

from pydantic import BaseModel


class UserBase(BaseModel):
    tg_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    language_code: str | None = None
    is_bot: bool | None = False
    balance: float = 0.0
    trial: int = 0
    source_code: str | None = None


class UserResponse(UserBase):
    created_at: datetime | None
    updated_at: datetime | None

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

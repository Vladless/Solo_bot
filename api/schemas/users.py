from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class UserBase(BaseModel):
    tg_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    language_code: Optional[str] = None
    is_bot: Optional[bool] = False
    balance: float = 0.0
    trial: int = 0
    source_code: Optional[str] = None


class UserResponse(UserBase):
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class UserUpdate(BaseModel):
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    language_code: Optional[str] = None
    is_bot: Optional[bool] = None
    balance: Optional[float] = None
    trial: Optional[int] = None
    source_code: Optional[str] = None

    class Config:
        from_attributes = True

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class SettingUpsert(BaseModel):
    value: Any | None = None
    description: str | None = None


class SettingResponse(BaseModel):
    key: str
    value: Any | None = None
    description: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        from_attributes = True

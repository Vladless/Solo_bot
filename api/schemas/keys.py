from pydantic import BaseModel
from typing import Optional


class KeyBase(BaseModel):
    tg_id: int
    client_id: str
    email: Optional[str] = None
    created_at: Optional[int] = None
    expiry_time: int
    key: Optional[str] = None
    server_id: Optional[str] = None
    remnawave_link: Optional[str] = None
    tariff_id: Optional[int] = None
    is_frozen: Optional[bool] = False
    alias: Optional[str] = None
    notified: Optional[bool] = False
    notified_24h: Optional[bool] = False


class KeyResponse(KeyBase):
    class Config:
        from_attributes = True


class KeyDetailsResponse(BaseModel):
    key: Optional[str]
    remnawave_link: Optional[str]
    server_id: Optional[str]
    created_at: Optional[int]
    expiry_time: Optional[int]
    client_id: str
    tg_id: int
    email: Optional[str]
    is_frozen: bool
    balance: float
    alias: Optional[str]
    expiry_date: str
    days_left_message: str
    link: Optional[str]
    cluster_name: Optional[str]
    location_name: Optional[str]
    tariff_id: Optional[int]

    class Config:
        from_attributes = True


class KeyUpdate(BaseModel):
    email: Optional[str] = None
    expiry_time: Optional[int] = None
    key: Optional[str] = None
    server_id: Optional[str] = None
    remnawave_link: Optional[str] = None
    tariff_id: Optional[int] = None
    is_frozen: Optional[bool] = None
    alias: Optional[str] = None
    notified: Optional[bool] = None
    notified_24h: Optional[bool] = None

    class Config:
        from_attributes = True


class KeyCreateRequest(BaseModel):
    tg_id: int
    cluster_id: str
    tariff_id: int
    email: Optional[str] = None
    alias: Optional[str] = None
    remnawave_link: Optional[str] = None
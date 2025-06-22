from pydantic import BaseModel
from typing import Optional


class ServerBase(BaseModel):
    cluster_name: str
    server_name: str
    api_url: str
    subscription_url: Optional[str] = None  
    inbound_id: str
    panel_type: str
    max_keys: Optional[int] = None       
    tariff_group: Optional[str] = ""
    enabled: bool = True


class ServerResponse(ServerBase):
    id: int

    class Config:
        from_attributes = True


class ServerUpdate(BaseModel):
    cluster_name: Optional[str] = None
    server_name: Optional[str] = None
    api_url: Optional[str] = None
    subscription_url: Optional[str] = None
    inbound_id: Optional[str] = None
    panel_type: Optional[str] = None
    max_keys: Optional[int] = None
    tariff_group: Optional[str] = None
    enabled: Optional[bool] = None

    class Config:
        from_attributes = True
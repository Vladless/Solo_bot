from pydantic import BaseModel


class ServerBase(BaseModel):
    cluster_name: str
    server_name: str
    api_url: str
    subscription_url: str | None = None
    inbound_id: str
    panel_type: str
    max_keys: int | None = None
    tariff_group: str | None = ""
    enabled: bool = True


class ServerResponse(ServerBase):
    id: int

    class Config:
        from_attributes = True


class ServerUpdate(BaseModel):
    cluster_name: str | None = None
    server_name: str | None = None
    api_url: str | None = None
    subscription_url: str | None = None
    inbound_id: str | None = None
    panel_type: str | None = None
    max_keys: int | None = None
    tariff_group: str | None = None
    enabled: bool | None = None

    class Config:
        from_attributes = True

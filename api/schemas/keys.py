from pydantic import BaseModel, Field


class KeyBase(BaseModel):
    tg_id: int
    client_id: str
    email: str | None = None
    created_at: int | None = None
    expiry_time: int
    key: str | None = None
    server_id: str | None = None
    remnawave_link: str | None = None
    tariff_id: int | None = None
    is_frozen: bool | None = False
    alias: str | None = None
    notified: bool | None = False
    notified_24h: bool | None = False


class KeyResponse(KeyBase):
    class Config:
        from_attributes = True


class KeyDetailsResponse(BaseModel):
    key: str | None
    remnawave_link: str | None
    server_id: str | None
    created_at: int | None
    expiry_time: int | None
    client_id: str
    tg_id: int
    email: str | None
    is_frozen: bool
    balance: float
    alias: str | None
    expiry_date: str
    days_left_message: str
    link: str | None
    cluster_name: str | None
    location_name: str | None
    tariff_id: int | None

    class Config:
        from_attributes = True


class KeyUpdate(BaseModel):
    email: str | None = None
    expiry_time: int | None = None
    key: str | None = None
    server_id: str | None = None
    remnawave_link: str | None = None
    tariff_id: int | None = None
    is_frozen: bool | None = None
    alias: str | None = None
    notified: bool | None = None
    notified_24h: bool | None = None

    class Config:
        from_attributes = True


class KeyCreateRequest(BaseModel):
    tg_id: int = Field(..., description="Telegram ID пользователя")
    cluster_id: str = Field(..., description="Имя кластера или сервера")
    tariff_id: int = Field(..., description="ID тарифа из базы данных")
    client_id: str = Field(..., description="UUID клиента (уникальный)")
    expiry_timestamp: int = Field(..., description="Срок окончания в миллисекундах")

    email: str | None = Field(None, description="Условное имя подписки")
    alias: str | None = Field(None, description="пользовательское имя")
    remnawave_link: str | None = Field(None, description="Ссылка на подписку Remnawave")
    hwid_limit: int | None = Field(None, description="Ограничение по HWID")
    traffic_limit_bytes: int | None = Field(None, description="Ограничение трафика в байтах")
    is_trial: bool | None = Field(False, description="Флаг триального ключа")

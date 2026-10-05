from pydantic import BaseModel


class TariffGroup(BaseModel):
    """Группа тарифов (group_code) для выбора в лендинге и др."""

    group_code: str


class TariffPublic(BaseModel):
    """Публичный список тарифов (без авторизации)."""

    id: int
    name: str
    group_code: str
    duration_days: int
    price_rub: int
    traffic_limit: int | None
    device_limit: int | None
    subgroup_title: str | None
    sort_order: int | None
    vless: bool = False
    configurable: bool = False
    device_options: list[int] | None = None
    traffic_options_gb: list[int] | None = None
    addon_device_options: list[int] | None = None
    addon_traffic_options: list[int] | None = None
    cooldown_days: int = 0

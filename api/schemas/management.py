from pydantic import BaseModel


class MaintenanceUpdate(BaseModel):
    enabled: bool


class DomainChange(BaseModel):
    domain: str

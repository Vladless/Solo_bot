from fastapi import APIRouter

from api.routes.base_crud import generate_crud_router
from api.schemas import TariffBase, TariffResponse, TariffUpdate
from database.models import Tariff


router: APIRouter = generate_crud_router(
    model=Tariff,
    schema_response=TariffResponse,
    schema_create=TariffBase,
    schema_update=TariffUpdate,
    identifier_field="name",
    parameter_name="name",
    enabled_methods=["get_all", "get_one", "create", "update", "delete"],
)

from fastapi import APIRouter

from api.routes.base_crud import generate_crud_router
from api.schemas import ServerBase, ServerResponse, ServerUpdate
from database.models import Server

router: APIRouter = generate_crud_router(
    model=Server,
    schema_response=ServerResponse,
    schema_create=ServerBase,
    schema_update=ServerUpdate,
    identifier_field="server_name",
    parameter_name="server_name",
    enabled_methods=["get_all", "get_one", "create", "update", "delete"],
)

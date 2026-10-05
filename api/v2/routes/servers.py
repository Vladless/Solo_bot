import time

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_session, verify_identity_admin
from api.v2.base_crud import generate_crud_router
from api.v2.schemas import ServerBase, ServerResponse, ServerUpdate
from database.models import Key, Server


stats_router = APIRouter()


@stats_router.get("/stats")
async def server_stats(
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Метрики серверов: активные подписки по кластеру/серверу."""
    now_ms = int(time.time() * 1000)
    rows = (
        await session.execute(
            select(Key.server_id, func.count())
            .where(Key.expiry_time > now_ms, Key.is_frozen.is_(False))
            .group_by(Key.server_id)
            .order_by(func.count().desc())
        )
    ).all()
    return {"active_by_cluster": [{"server": (sid or "—"), "active": int(c or 0)} for sid, c in rows]}


router = generate_crud_router(
    model=Server,
    schema_response=ServerResponse,
    schema_create=ServerBase,
    schema_update=ServerUpdate,
    identifier_field="server_name",
    parameter_name="server_name",
    enabled_methods=["get_all", "get_one", "create", "update", "delete"],
)

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AuditEventResponse(BaseModel):
    id: int | None = None
    event_type: str
    channel: str
    actor_identity_id: str | None
    actor_tg_id: int | None
    path_or_handler: str
    entity_type: str | None
    entity_id: str | None
    result: str
    reason: str | None
    metadata: dict[str, Any] | None
    request_id: str | None
    created_at: datetime | None


class AuditEventListResponse(BaseModel):
    items: list[AuditEventResponse]
    limit: int
    offset: int

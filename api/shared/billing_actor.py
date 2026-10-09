from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_request_actor
from database import identities as idb
from database.access.resolution import UserId


async def resolve_billing_user_id(request: Request, identity, session: AsyncSession) -> int:
    """Определяет ID клиента для расчётов по запросу или личности."""
    actor = get_request_actor(request)
    billing_user_id = actor.billing_user_id if actor and actor.billing_user_id is not None else None
    if billing_user_id is None:
        billing_user_id = await idb.ensure_billing_user_for_identity(session, identity)
    return UserId(billing_user_id)


async def resolve_billing_actor(request: Request, identity, session: AsyncSession) -> tuple[int, int | None]:
    """Возвращает ID клиента и привязанный чат Telegram."""
    user_id = await resolve_billing_user_id(request, identity, session)
    actor = get_request_actor(request)
    return user_id, actor.telegram_chat_id if actor else None

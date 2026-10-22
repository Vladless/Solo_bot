from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Identity, Ticket


async def get_ticket_delivery_identity(session: AsyncSession, ticket_id: str) -> Identity | None:
    """Возвращает актуального получателя переписки тикета."""
    return await session.scalar(
        select(Identity)
        .join(Ticket, Ticket.identity_id == Identity.id)
        .where(Ticket.id == ticket_id)
        .execution_options(populate_existing=True)
    )

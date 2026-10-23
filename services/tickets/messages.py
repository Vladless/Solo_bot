from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Ticket, TicketMessage

from .events import publish_tickets_changed
from .queue import queue_message, queue_sync


def message_permissions(
    ticket: Ticket,
    msg: TicketMessage,
    *,
    identity_id: str | None = None,
    tg_id: int | None = None,
    role: str = "user",
) -> dict[str, bool]:
    """Определяет доступ к изменению сообщения."""
    allowed = False
    if msg.ticket_id == ticket.id and msg.deleted_at is None:
        if role == "admin":
            allowed = True
        elif msg.author == "client":
            allowed = role == "user" and bool(identity_id) and ticket.identity_id == identity_id
        elif msg.author in {"agent", "note"} and role in {"agent", "moderator"}:
            allowed = bool(identity_id and msg.author_identity_id == identity_id) or bool(
                tg_id and tg_id > 0 and msg.agent_tg_id and msg.agent_tg_id > 0 and msg.agent_tg_id == tg_id
            )
    return {"can_edit": allowed, "can_delete": allowed}


async def _locked_message(
    session: AsyncSession,
    ticket_id: str,
    message_id: str,
    expected_revision: int | None,
) -> TicketMessage | None:
    """Блокирует сообщение и проверяет его версию."""
    ticket = await session.scalar(select(Ticket).where(Ticket.id == ticket_id).with_for_update())
    if ticket is None:
        return None
    msg = await session.scalar(
        select(TicketMessage)
        .where(TicketMessage.id == message_id, TicketMessage.ticket_id == ticket_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if msg is None:
        return None
    if msg.deleted_at is not None:
        raise ValueError("Сообщение уже удалено")
    if expected_revision is not None and msg.revision != expected_revision:
        raise ValueError("Сообщение уже изменено")
    return msg


async def edit_message(
    session: AsyncSession,
    *,
    ticket_id: str,
    message_id: str,
    body: str,
    attachments: list | None = None,
    expected_revision: int | None = None,
) -> TicketMessage | None:
    """Редактирует сообщение без изменения порядка переписки."""
    msg = await _locked_message(session, ticket_id, message_id, expected_revision)
    if msg is None:
        return None
    body = (body or "").strip()
    next_attachments = msg.attachments if attachments is None else attachments or None
    if msg.body == body and msg.attachments == next_attachments:
        return msg
    now = datetime.now(UTC)
    msg.body = body
    msg.attachments = next_attachments
    msg.edited_at = now
    msg.revision += 1
    ticket = await session.get(Ticket, ticket_id)
    if ticket is not None:
        ticket.updated_at = now
    await session.flush()
    if ticket is not None:
        await queue_message(session, msg)
        await queue_sync(session, msg)
        await publish_tickets_changed(session, ticket.identity_id, client=msg.author != "note")
    return msg


async def delete_message(
    session: AsyncSession,
    *,
    ticket_id: str,
    message_id: str,
    expected_revision: int | None = None,
) -> TicketMessage | None:
    """Удаляет содержимое и сохраняет отметку в истории."""
    msg = await _locked_message(session, ticket_id, message_id, expected_revision)
    if msg is None:
        return None
    now = datetime.now(UTC)
    msg.body = ""
    msg.attachments = None
    msg.deleted_at = now
    msg.edited_at = now
    msg.revision += 1
    ticket = await session.get(Ticket, ticket_id)
    if ticket is not None:
        ticket.updated_at = now
    await session.flush()
    if ticket is not None:
        await queue_sync(session, msg)
        await publish_tickets_changed(session, ticket.identity_id, client=msg.author != "note")
    return msg

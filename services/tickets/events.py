from .outbox import enqueue


TICKETS_EVENTS_CHANNEL = "tickets:events"


def tickets_client_channel(identity_id: str) -> str:
    return f"tickets:client:{identity_id}"


async def publish_tickets_changed(session, identity_id: str | None = None, *, client: bool = True) -> None:
    """Откладывает обновление интерфейсов до фиксации изменений."""
    await enqueue(session, kind="event", payload={"channel": TICKETS_EVENTS_CHANNEL})
    if identity_id and client:
        await publish_client_ticket_changed(session, identity_id)


async def publish_client_ticket_changed(session, identity_id: str) -> None:
    """Сохраняет персональное событие в транзакции обращения."""
    if identity_id:
        await enqueue(session, kind="event", payload={"channel": tickets_client_channel(identity_id)})

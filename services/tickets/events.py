from core.redis_cache import cache_publish


TICKETS_EVENTS_CHANNEL = "tickets:events"


def tickets_client_channel(identity_id: str) -> str:
    return f"tickets:client:{identity_id}"


async def publish_tickets_changed() -> None:
    await cache_publish(TICKETS_EVENTS_CHANNEL, {"t": "changed"})


async def publish_client_ticket_changed(identity_id: str) -> None:
    if identity_id:
        await cache_publish(tickets_client_channel(identity_id), {"t": "changed"})

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Admin, Identity, Ticket, TicketMessage
from database.tickets import get_ticket_delivery_identity
from database.users import get_billing_user_id_for_identity
from database.web_notifications import create_notification
from settings import config

from .events import publish_client_ticket_changed


def support_persona() -> str:
    name = (getattr(config, "PROJECT_NAME", "") or "").strip()
    return f"Поддержка {name}" if name else "Поддержка"


def support_bot_instance():
    """Возвращает бота поддержки, если он запущен."""
    try:
        from support_bot import support_bot

        return support_bot
    except Exception:
        return None


async def notify_agents_new_ticket(session: AsyncSession, *, ticket: Ticket) -> None:
    short = ticket.id.split("-")[0][:8]
    cat = f" · {ticket.category}" if ticket.category else ""
    rows = [int(t) for t in (await session.execute(select(Admin.tg_id))).scalars().all() if t]

    from .queue import queue_new_ticket

    await queue_new_ticket(session, ticket)
    await _webpush_agents(session, [t for t in rows if t > 0], f"Новое обращение {short}", f"Категория{cat or ': —'}")


async def notify_client_ticket_closed(session: AsyncSession, *, ticket: Ticket) -> None:
    from .queue import queue_closed

    if ticket.rating is None:
        await queue_closed(session, ticket)


async def _webpush_agents(session: AsyncSession, agent_tgs: list[int], title: str, body: str) -> None:
    if not agent_tgs:
        return
    try:
        from services.web_push import push_enabled, send_push_to_many

        if not push_enabled():
            return
        from database.web_notifications import get_push_subscriptions_by_identity

        ident_ids = (await session.execute(select(Identity.id).where(Identity.tg_id.in_(agent_tgs)))).scalars().all()
        sub_infos = []
        for iid in ident_ids:
            for s in await get_push_subscriptions_by_identity(session, iid):
                sub_infos.append({"endpoint": s.endpoint, "keys": s.keys_json})
        if sub_infos:
            await send_push_to_many(sub_infos, title=title, body=body, url="/support", tag="ticket-new")
    except Exception:
        pass


async def _client_ticket_href(session: AsyncSession, ticket_id: str) -> str:
    """Определяет адрес переписки в кабинете клиента."""
    try:
        from database.web_layout import block_location_href, find_block_locations, support_block_types

        locations = await find_block_locations(session, support_block_types())
        if locations:
            return block_location_href(locations[0], {"ticket": ticket_id})
    except Exception:
        pass
    return "/dashboard/notifications"


async def _webpush_client(session: AsyncSession, identity_id: str, title: str, body: str, url: str) -> None:
    try:
        from services.web_push import push_enabled, send_push_to_many

        if not push_enabled():
            return
        from database.web_notifications import get_push_subscriptions_by_identity

        subs = await get_push_subscriptions_by_identity(session, identity_id)
        if not subs:
            return
        sub_infos = [{"endpoint": s.endpoint, "keys": s.keys_json} for s in subs]
        await send_push_to_many(sub_infos, title=title, body=body, url=url, tag="ticket-reply")
    except Exception:
        pass


async def _email_client_reply(client: Identity, ticket: Ticket, body: str) -> None:
    email = (getattr(client, "email", None) or "").strip()
    if not email or not getattr(client, "email_verified", False):
        return
    try:
        from mail import send_support_reply_email, smtp_configured

        if not smtp_configured():
            return
        await send_support_reply_email(email, ticket.id.split("-")[0][:8], body or "Новый ответ по вашему обращению")
    except Exception:
        pass


async def notify_client_of_reply(session: AsyncSession, *, ticket: Ticket, msg: TicketMessage) -> None:
    client = await get_ticket_delivery_identity(session, ticket.id)
    if client is None:
        return
    body = (msg.body or "").strip()
    preview = body[:120] or "Новый ответ по вашему обращению"
    owner_id = await get_billing_user_id_for_identity(session, client.id, client.tg_id)
    if owner_id is not None:
        await create_notification(
            session,
            user_id=owner_id,
            identity_id=client.id,
            type="ticket",
            title=f"{support_persona()} ответила",
            message=preview,
            data={"ticket_id": ticket.id},
        )
    await _webpush_client(
        session,
        client.id,
        f"{support_persona()} ответила",
        preview,
        await _client_ticket_href(session, ticket.id),
    )
    await publish_client_ticket_changed(session, client.id)
    client = await get_ticket_delivery_identity(session, ticket.id)
    if client is None:
        return
    tg_id = client.tg_id or 0
    if tg_id <= 0:
        await _email_client_reply(client, ticket, body)
        return
    from .queue import queue_message

    await queue_message(session, msg)

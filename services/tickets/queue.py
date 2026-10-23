from sqlalchemy import select

from database.models import Admin
from settings import config

from .outbox import enqueue


def telegram_configured() -> bool:
    """Определяет наличие Telegram-интеграции поддержки."""
    from .delivery import support_bot_instance

    return bool(str(getattr(config, "SUPPORT_BOT_TOKEN", "") or "").strip()) or support_bot_instance() is not None


async def queue_message(session, msg) -> None:
    """Планирует доставку текущей версии сообщения всем получателям."""
    from .forum import forum_chat_id

    if not telegram_configured():
        return
    destinations = ["telegram_client"] if msg.author == "agent" else []
    if forum_chat_id() is not None:
        destinations.append("telegram_forum")
    elif msg.author == "client":
        destinations.append("telegram_agent")
    for kind in destinations:
        await enqueue(
            session,
            kind=kind,
            ticket_id=msg.ticket_id,
            ticket_message_id=msg.id,
            payload={"revision": msg.revision},
            dedupe_key=f"{kind}:{msg.id}:{msg.revision}",
        )


async def queue_sync(session, msg) -> None:
    """Планирует синхронизацию уже существующих Telegram-копий."""
    await enqueue(
        session,
        kind="telegram_sync",
        ticket_id=msg.ticket_id,
        ticket_message_id=msg.id,
        payload={"revision": msg.revision},
        dedupe_key=f"telegram_sync:{msg.id}:{msg.revision}",
    )


async def queue_new_ticket(session, ticket) -> None:
    """Планирует уведомления операторов без форумной группы."""
    from .forum import forum_chat_id

    if not telegram_configured() or forum_chat_id() is not None:
        return
    admins = (await session.scalars(select(Admin.tg_id).where(Admin.tg_id > 0))).all()
    for tg_id in admins:
        await enqueue(
            session,
            kind="telegram_notice",
            ticket_id=ticket.id,
            payload={"notice": "new", "agent_tg_id": int(tg_id)},
            dedupe_key=f"telegram_notice:new:{ticket.id}:{tg_id}",
        )


async def queue_topic_state(session, ticket) -> None:
    """Откладывает изменение состояния темы до сохранения обращения."""
    if not telegram_configured():
        return
    await enqueue(session, kind="forum_state", ticket_id=ticket.id)


async def queue_topic_delete(session, ticket) -> None:
    """Сохраняет удаление темы независимо от удаляемого обращения."""
    from .delivery import support_bot_instance
    from .forum import forum_chat_id

    bot = support_bot_instance()
    chat_id = ticket.topic_chat_id or forum_chat_id()
    bot_id = ticket.topic_bot_id or (bot.id if bot else None)
    if not ticket.topic_id or chat_id is None or bot_id is None:
        return
    await enqueue(
        session,
        kind="forum_delete",
        payload={"chat_id": chat_id, "topic_id": ticket.topic_id, "bot_id": bot_id},
        dedupe_key=f"forum_delete:{bot_id}:{chat_id}:{ticket.topic_id}",
    )


async def queue_closed(session, ticket) -> None:
    """Планирует запрос оценки по актуальному получателю обращения."""
    if not telegram_configured():
        return
    await enqueue(
        session,
        kind="telegram_notice",
        ticket_id=ticket.id,
        payload={"notice": "closed", "updated_at": ticket.updated_at.isoformat()},
        dedupe_key=f"telegram_notice:closed:{ticket.id}:{ticket.updated_at.isoformat()}",
    )


async def queue_assignment(session, ticket) -> None:
    """Планирует уведомление о назначении действующему оператору."""
    if not telegram_configured() or not ticket.assigned_agent_tg_id or ticket.assigned_agent_tg_id <= 0:
        return
    await enqueue(
        session,
        kind="telegram_notice",
        ticket_id=ticket.id,
        payload={"notice": "assigned", "agent_tg_id": ticket.assigned_agent_tg_id},
        dedupe_key=f"telegram_notice:assigned:{ticket.id}:{ticket.updated_at.isoformat()}:{ticket.assigned_agent_tg_id}",
    )

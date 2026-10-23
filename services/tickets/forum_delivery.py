from html import escape

from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select

from database.models import Ticket, TicketDelivery
from database.tickets import get_ticket_delivery_identity


def _missing_topic(error: TelegramBadRequest) -> bool:
    """Распознаёт подтверждённое отсутствие Telegram-темы."""
    reason = str(error).lower()
    return "message thread not found" in reason or "topic_id_invalid" in reason or "topic_deleted" in reason


async def ensure_delivery_topic(session, ticket: Ticket) -> int:
    """Создаёт тему либо закрепляет существующую за конкретным ботом и чатом."""
    from .delivery import support_bot_instance
    from .forum import forum_chat_id

    bot = support_bot_instance()
    chat_id = forum_chat_id()
    if bot is None:
        raise RuntimeError("Бот поддержки недоступен")
    if chat_id is None:
        raise ValueError("Форум поддержки не настроен")
    if ticket.topic_id and ticket.topic_chat_id in (None, chat_id) and ticket.topic_bot_id in (None, bot.id):
        ticket.topic_chat_id = chat_id
        ticket.topic_bot_id = bot.id
        await session.flush()
        return ticket.topic_id
    short = ticket.id.split("-")[0][:8]
    topic = await bot.create_forum_topic(chat_id=chat_id, name=f"#{short} · {ticket.category or 'обращение'}"[:120])
    ticket.topic_id = topic.message_thread_id
    ticket.topic_chat_id = chat_id
    ticket.topic_bot_id = bot.id
    ticket.topic_header_message_id = None
    if ticket.status == "closed":
        from .queue import queue_topic_state

        await queue_topic_state(session, ticket)
    await session.flush()
    return ticket.topic_id


async def ensure_delivery_header(session, ticket: Ticket) -> bool:
    """Сохраняет адрес шапки темы перед доставкой сообщений."""
    if ticket.topic_header_message_id is not None:
        return False
    from .delivery import support_bot_instance
    from .forum import _context_lines, agent_controls_kb, forum_chat_id
    from .service import build_client_context, resolve_billing_user_ref

    bot = support_bot_instance()
    chat_id = forum_chat_id()
    if bot is None:
        raise RuntimeError("Бот поддержки недоступен")
    if not ticket.topic_id or ticket.topic_chat_id != chat_id or ticket.topic_bot_id != bot.id:
        raise ValueError("Тема не привязана к текущему форуму поддержки")
    client = await get_ticket_delivery_identity(session, ticket.id)
    context = await build_client_context(session, client) if client is not None else None
    admin_ref = await resolve_billing_user_ref(session, client) if client is not None else None
    short = ticket.id.split("-")[0][:8]
    header = f"🎫 <b>Обращение</b> <code>{escape(short)}</code>"
    if ticket.subject:
        header += "\n" + escape(ticket.subject)
    if client is not None:
        context_text = _context_lines(client, context)
        if context_text:
            header += "\n" + context_text
    header += (
        "\n\nОтвечайте в этой теме — сообщение уйдёт клиенту."
        "\nДоп. команды: /note заметка · /assign [id] · /tag метка"
        "\nУдаление своего сообщения: /delete ответом на него."
    )
    try:
        sent = await bot.send_message(
            chat_id=chat_id,
            text=header,
            message_thread_id=ticket.topic_id,
            reply_markup=agent_controls_kb(ticket, admin_ref),
            parse_mode="HTML",
        )
    except TelegramBadRequest as error:
        reason = str(error).lower()
        if "topic_closed" in reason or "topic is closed" in reason:
            from .queue import queue_topic_state

            await bot.reopen_forum_topic(chat_id=chat_id, message_thread_id=ticket.topic_id)
            await queue_topic_state(session, ticket)
            return True
        if not _missing_topic(error):
            raise
        ticket.topic_id = None
        ticket.topic_header_message_id = None
        return True
    ticket.topic_header_message_id = sent.message_id
    await session.flush()
    return True


async def deliver_forum_action(job: dict, *, sessionmaker) -> None:
    """Применяет отложенное состояние темы или удаляет сохранённый адрес."""
    from .delivery import support_bot_instance
    from .forum import forum_chat_id
    from .worker import _lease

    async with sessionmaker() as session:
        ticket = None
        if job["kind"] == "forum_state":
            ticket = await session.scalar(select(Ticket).where(Ticket.id == job["ticket_id"]).with_for_update())
            if ticket is None:
                return
        row = await _lease(session, job)
        bot = support_bot_instance()
        if bot is None:
            raise RuntimeError("Бот поддержки недоступен")
        if job["kind"] == "forum_delete":
            address = row.payload
            chat_id, topic_id, bot_id = (address.get(key) for key in ("chat_id", "topic_id", "bot_id"))
            if any(type(value) is not int for value in (chat_id, topic_id, bot_id)) or topic_id <= 0:
                raise ValueError("Некорректный адрес удаляемой темы")
            if bot.id != bot_id:
                raise ValueError("Удаляемая тема принадлежит другому боту")
            try:
                await bot.delete_forum_topic(chat_id=chat_id, message_thread_id=topic_id)
            except TelegramBadRequest as error:
                if not _missing_topic(error):
                    raise
        elif job["kind"] == "forum_state":
            chat_id = forum_chat_id()
            if (
                chat_id is None
                or not ticket.topic_id
                or ticket.topic_chat_id not in (None, chat_id)
                or ticket.topic_bot_id not in (None, bot.id)
            ):
                return
            if ticket.status == "closed":
                pending = await session.scalar(
                    select(TicketDelivery.id)
                    .where(
                        TicketDelivery.ticket_id == ticket.id,
                        TicketDelivery.kind.in_(["telegram_forum", "telegram_sync"]),
                        TicketDelivery.status.in_(["pending", "retry", "running"]),
                    )
                    .limit(1)
                )
                if pending is not None:
                    raise RuntimeError("Доставка переписки перед закрытием темы не завершена")
            try:
                call = bot.close_forum_topic if ticket.status == "closed" else bot.reopen_forum_topic
                await call(chat_id=chat_id, message_thread_id=ticket.topic_id)
            except TelegramBadRequest as error:
                reason = str(error).lower()
                if "topic_not_modified" not in reason and "topic is already" not in reason:
                    raise
        else:
            raise ValueError("Неизвестное действие с темой поддержки")
        await session.commit()

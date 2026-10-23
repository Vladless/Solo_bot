from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from database import db
from database.models import Admin, Ticket, TicketDelivery, TicketMessage, TicketTelegramMessage
from database.tickets import get_ticket_delivery_identity


class DeliveryLeaseLostError(RuntimeError):
    pass


async def _lease(session, job):
    """Проверяет право обработки перед каждой отправляемой частью."""
    now = datetime.now(UTC)
    row = await session.scalar(
        select(TicketDelivery)
        .where(TicketDelivery.id == job["id"])
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or row.status != "running" or row.lease_token != job["lease_token"]:
        raise DeliveryLeaseLostError("Аренда доставки завершилась")
    if row.lease_until is not None:
        until = row.lease_until if row.lease_until.tzinfo else row.lease_until.replace(tzinfo=UTC)
        if until <= now:
            raise DeliveryLeaseLostError("Аренда доставки истекла")
    row.lease_until = now + timedelta(seconds=90)
    return row


async def _agent_allowed(session, tg_id: int) -> bool:
    """Проверяет текущую роль получателя операторской переписки."""
    admin = await session.get(Admin, tg_id)
    return admin is not None and (admin.role or "admin").strip().lower() != "designer"


async def _forum_part(session, ticket, msg, bot, chat_id):
    """Восстанавливает доступность темы без потери сохранённых частей."""
    from aiogram.exceptions import TelegramBadRequest

    from .queue import queue_topic_state
    from .telegram import send_message_parts

    try:
        return await send_message_parts(session, msg, chat_id=chat_id, topic_id=ticket.topic_id, one_part=True)
    except TelegramBadRequest as error:
        reason = str(error).lower()
        if "topic_closed" in reason or "topic is closed" in reason:
            await bot.reopen_forum_topic(chat_id, ticket.topic_id)
            await queue_topic_state(session, ticket)
            return True
        if "message thread not found" in reason or "topic_id_invalid" in reason or "topic_deleted" in reason:
            ticket.topic_id = None
            ticket.topic_header_message_id = None
            return True
        raise


async def _message(job, *, sessionmaker) -> None:
    """Доставляет переписку с фиксацией каждой известной Telegram-копии."""
    from .delivery import support_bot_instance
    from .forum import forum_chat_id
    from .telegram import _delete_ref, send_message_parts

    while True:
        async with sessionmaker() as session:
            ticket = await session.scalar(select(Ticket).where(Ticket.id == job["ticket_id"]).with_for_update())
            if ticket is None:
                return
            delivery = await _lease(session, job)
            msg = await session.get(TicketMessage, job["ticket_message_id"])
            if msg is None or msg.ticket_id != ticket.id:
                return
            bot = support_bot_instance()
            if bot is None:
                raise RuntimeError("Бот поддержки недоступен")
            forum_chat = forum_chat_id()
            if job["kind"] == "telegram_sync":
                refs = list(
                    (
                        await session.scalars(
                            select(TicketTelegramMessage).where(
                                TicketTelegramMessage.ticket_message_id == msg.id,
                                TicketTelegramMessage.bot_id == bot.id,
                                TicketTelegramMessage.deleted.is_(False),
                            )
                        )
                    ).all()
                )
                if msg.deleted_at is not None:
                    ref = next((item for item in refs if not item.deleted), None)
                    if ref is None:
                        await session.commit()
                        return
                    await _delete_ref(bot, ref)
                    ref.applied_revision = msg.revision
                    await session.commit()
                    continue
                client = await get_ticket_delivery_identity(session, ticket.id)
                chats = sorted({ref.chat_id for ref in refs if not ref.source})
                changed = False
                for chat_id in chats:
                    if chat_id == forum_chat:
                        from .forum_delivery import ensure_delivery_header, ensure_delivery_topic

                        before = (ticket.topic_id, ticket.topic_chat_id, ticket.topic_bot_id)
                        await ensure_delivery_topic(session, ticket)
                        if before != (ticket.topic_id, ticket.topic_chat_id, ticket.topic_bot_id):
                            changed = True
                            break
                        if await ensure_delivery_header(session, ticket):
                            changed = True
                            break
                        topic_id = ticket.topic_id
                    elif msg.author == "agent" and client is not None and client.tg_id == chat_id:
                        topic_id = None
                    elif (
                        msg.author == "client"
                        and ticket.assigned_agent_tg_id == chat_id
                        and await _agent_allowed(session, chat_id)
                    ):
                        topic_id = None
                    else:
                        continue
                    if topic_id is not None:
                        changed = await _forum_part(session, ticket, msg, bot, chat_id)
                    else:
                        changed = await send_message_parts(session, msg, chat_id=chat_id, one_part=True)
                    if changed:
                        break
            elif msg.deleted_at is not None:
                await session.commit()
                return
            elif job["kind"] == "telegram_agent":
                target = ticket.assigned_agent_tg_id
                if msg.author != "client" or not target or target <= 0 or not await _agent_allowed(session, target):
                    delivery.payload = {**delivery.payload, "skipped": True}
                    await session.commit()
                    return
                changed = await send_message_parts(session, msg, chat_id=target, one_part=True)
            elif job["kind"] == "telegram_client":
                client = await get_ticket_delivery_identity(session, ticket.id)
                if msg.author != "agent" or client is None or not client.tg_id or client.tg_id <= 0:
                    delivery.payload = {**delivery.payload, "skipped": True}
                    await session.commit()
                    return
                changed = await send_message_parts(session, msg, chat_id=client.tg_id, one_part=True)
            else:
                if forum_chat is None:
                    raise ValueError("Форум поддержки не настроен")
                source = await session.scalar(
                    select(TicketTelegramMessage.id).where(
                        TicketTelegramMessage.ticket_message_id == msg.id,
                        TicketTelegramMessage.bot_id == bot.id,
                        TicketTelegramMessage.chat_id == forum_chat,
                        TicketTelegramMessage.source.is_(True),
                    )
                )
                if source is not None:
                    await session.commit()
                    return
                from .forum_delivery import ensure_delivery_header, ensure_delivery_topic

                before = (ticket.topic_id, ticket.topic_chat_id, ticket.topic_bot_id)
                await ensure_delivery_topic(session, ticket)
                if before != (ticket.topic_id, ticket.topic_chat_id, ticket.topic_bot_id):
                    await session.commit()
                    continue
                if await ensure_delivery_header(session, ticket):
                    await session.commit()
                    continue
                changed = await _forum_part(session, ticket, msg, bot, forum_chat)
            await session.commit()
            if not changed:
                return


async def _notice(job, *, sessionmaker) -> None:
    """Доставляет служебное уведомление актуальному участнику обращения."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    from .delivery import support_bot_instance

    async with sessionmaker() as session:
        ticket = await session.scalar(select(Ticket).where(Ticket.id == job["ticket_id"]).with_for_update())
        if ticket is None:
            return
        row = await _lease(session, job)
        if row.payload.get("sent_message_id"):
            return
        bot = support_bot_instance()
        if bot is None:
            raise RuntimeError("Бот поддержки недоступен")
        notice = row.payload.get("notice")
        short = ticket.id.split("-")[0][:8]
        kwargs = {}
        if notice == "closed":
            if ticket.status != "closed" or ticket.rating is not None:
                return
            client = await get_ticket_delivery_identity(session, ticket.id)
            if client is None or not client.tg_id or client.tg_id <= 0:
                return
            target = client.tg_id
            from settings.texts import SUPPORT_TICKET_CLOSED_RATING_TEXT

            text = SUPPORT_TICKET_CLOSED_RATING_TEXT
            kwargs["reply_markup"] = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=f"{n}⭐", callback_data=f"sc:rate:{ticket.id}:{n}") for n in range(1, 6)]
                ]
            )
        elif notice in {"new", "assigned"}:
            target = row.payload.get("agent_tg_id")
            if notice == "assigned" and ticket.assigned_agent_tg_id != target:
                return
            if not target or target <= 0:
                return
            if not await _agent_allowed(session, target):
                return
            text = (
                f"🆕 Новое обращение <code>{short}</code>"
                if notice == "new"
                else f"🎫 Вам назначено обращение <code>{short}</code>."
            )
            if notice == "assigned" and ticket.topic_id and ticket.topic_chat_id:
                group = str(ticket.topic_chat_id).removeprefix("-100")
                text += f"\nhttps://telegram.me/c/{group}/{ticket.topic_id}"
        else:
            raise ValueError("Неизвестное уведомление поддержки")
        sent = await bot.send_message(target, text, **kwargs)
        row.payload = {**row.payload, "sent_message_id": sent.message_id, "sent_chat_id": target}
        await session.commit()


async def deliver(job: dict, *, sessionmaker=None) -> None:
    """Выполняет сохранённое намерение доставки поддержки."""
    maker = sessionmaker or db.async_session_maker
    if job["kind"] in {"telegram_forum", "telegram_client", "telegram_agent", "telegram_sync"}:
        await _message(job, sessionmaker=maker)
    elif job["kind"] == "telegram_notice":
        await _notice(job, sessionmaker=maker)
    else:
        from .forum_delivery import deliver_forum_action

        await deliver_forum_action(job, sessionmaker=maker)

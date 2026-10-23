from html import escape

from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Identity, Ticket, TicketMessage
from services.tickets.delivery import support_bot_instance
from settings import config


def forum_chat_id() -> int | None:
    raw = str(getattr(config, "SUPPORT_FORUM_CHAT_ID", "") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except Exception:
        return None


def forum_enabled() -> bool:
    return forum_chat_id() is not None and support_bot_instance() is not None


def _client_bot_link(admin_ref: int | None):
    if not admin_ref:
        return None
    username = str(getattr(config, "USERNAME_BOT", "") or "").replace("@", "").strip()
    if not username:
        return None
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👤 Открыть клиента в боте",
                    url=f"https://telegram.me/{username}?start=suser_u{int(admin_ref)}",
                )
            ]
        ]
    )


def agent_controls_kb(ticket: Ticket, admin_ref: int | None = None):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    rows = []
    link = _client_bot_link(admin_ref)
    if link is not None:
        rows.extend(link.inline_keyboard)
    tid = ticket.id
    closed = ticket.status == "closed"
    high = (ticket.priority or "normal") == "high"
    if closed:
        rows.append([InlineKeyboardButton(text="🔓 Открыть заново", callback_data=f"sf:open:{tid}")])
    else:
        rows.append([
            InlineKeyboardButton(text="✅ Закрыть", callback_data=f"sf:close:{tid}"),
            InlineKeyboardButton(text="🙋 Взять себе", callback_data=f"sf:mine:{tid}"),
        ])
        rows.append([
            InlineKeyboardButton(text="⏳ В ожидание", callback_data=f"sf:pending:{tid}"),
            InlineKeyboardButton(
                text=("🔻 Обычный приоритет" if high else "🔺 Высокий приоритет"),
                callback_data=f"sf:prio:{tid}",
            ),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _post(topic_id: int, text: str | None = None, attachments: list | None = None, reply_markup=None) -> None:
    bot = support_bot_instance()
    chat_id = forum_chat_id()
    if bot is None or chat_id is None or not topic_id:
        return
    from aiogram.types import FSInputFile

    from .telegram import _local_upload, _media_kind

    if text:
        try:
            await bot.send_message(chat_id, text[:4000], message_thread_id=topic_id, reply_markup=reply_markup)
        except Exception:
            pass
    for att in attachments or []:
        local = _local_upload(att)
        if local is None or not local.is_file():
            continue
        try:
            kind = _media_kind(local)
            if kind == "animation":
                await bot.send_animation(chat_id, FSInputFile(str(local)), message_thread_id=topic_id)
            elif kind == "photo":
                await bot.send_photo(chat_id, FSInputFile(str(local)), message_thread_id=topic_id)
            else:
                await bot.send_document(chat_id, FSInputFile(str(local)), message_thread_id=topic_id)
        except Exception:
            pass


def _context_lines(client: Identity, context: dict | None) -> str:
    parts = []
    who = []
    if getattr(client, "email", None):
        who.append(escape(str(client.email)))
    if getattr(client, "tg_id", None) and int(client.tg_id) > 0:
        who.append(f"tg <code>{client.tg_id}</code>")
    if who:
        parts.append("👤 " + " · ".join(who))
    if context:
        parts.append(
            f"🔑 ключи {context.get('keys_active', 0)}/{context.get('keys_total', 0)}"
            + (f" · до {escape(str(context['nearest_expiry'])[:10])}" if context.get("nearest_expiry") else "")
        )
        lp = context.get("last_payment")
        bal = context.get("balance")
        line = []
        if bal is not None:
            line.append(f"баланс {escape(str(bal))}₽")
        if lp:
            line.append(f"платёж {escape(str(lp.get('amount')))}₽/{escape(str(lp.get('status')))}")
        if line:
            parts.append("💳 " + " · ".join(line))
    return "\n".join(parts)


async def ensure_topic(
    session: AsyncSession,
    ticket: Ticket,
    *,
    client: Identity | None = None,
    context: dict | None = None,
    first_body: str | None = None,
    first_attachments: list | None = None,
    first_message: TicketMessage | None = None,
) -> int | None:
    """Планирует создание темы и доставку сохранённого первого сообщения."""
    if forum_chat_id() is None:
        return None
    from .queue import queue_message

    if first_message is None:
        from .service import get_thread

        thread = await get_thread(session, ticket.id)
        first_message = thread[0] if thread else None
    if first_message is not None:
        await queue_message(session, first_message)
    return ticket.topic_id


async def post_client_message(
    ticket: Ticket,
    body: str,
    attachments: list | None = None,
    *,
    session: AsyncSession | None = None,
    message_id: str | None = None,
) -> None:
    """Планирует доставку сохранённого сообщения клиента в форум."""
    if forum_chat_id() is None:
        return
    if not message_id:
        raise ValueError("Для доставки в форум требуется сохранённое сообщение")
    if session is None:
        from database import async_session_maker

        async with async_session_maker() as delivery_session:
            await post_client_message(ticket, body, attachments, session=delivery_session, message_id=message_id)
            await delivery_session.commit()
        return
    from .queue import queue_message

    msg = await session.get(TicketMessage, message_id)
    if msg is not None and msg.ticket_id == ticket.id:
        await queue_message(session, msg)


async def post_system(
    ticket: Ticket,
    text: str,
    *,
    session: AsyncSession | None = None,
    message: TicketMessage | None = None,
) -> None:
    """Планирует доставку сохранённого ответа или заметки в форум."""
    if forum_chat_id() is None:
        return
    if message is None or message.ticket_id != ticket.id:
        raise ValueError("Для доставки в форум требуется сохранённое сообщение")
    if session is None:
        from database import async_session_maker

        async with async_session_maker() as delivery_session:
            fresh = await delivery_session.get(TicketMessage, message.id)
            if fresh is not None:
                await post_system(ticket, text, session=delivery_session, message=fresh)
            await delivery_session.commit()
        return
    from .queue import queue_message

    await queue_message(session, message)


async def set_topic_state(ticket: Ticket, *, closed: bool, session: AsyncSession | None = None) -> None:
    """Сохраняет намерение синхронизировать актуальное состояние темы."""
    if forum_chat_id() is None:
        return
    from .queue import queue_topic_state

    if session is not None:
        await queue_topic_state(session, ticket)
        return
    from database import async_session_maker

    async with async_session_maker() as delivery_session:
        fresh = await delivery_session.get(Ticket, ticket.id)
        if fresh is not None:
            await queue_topic_state(delivery_session, fresh)
        await delivery_session.commit()


async def delete_topic(ticket: Ticket, *, session: AsyncSession | None = None) -> None:
    """Планирует удаление темы с сохранением её исходного адреса."""
    from .queue import queue_topic_delete

    if session is not None:
        await queue_topic_delete(session, ticket)
        return
    from database import async_session_maker

    async with async_session_maker() as delivery_session:
        await queue_topic_delete(delivery_session, ticket)
        await delivery_session.commit()

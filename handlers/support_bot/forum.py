from aiogram import F, Router
from aiogram.filters import BaseFilter, Command, CommandObject
from aiogram.types import CallbackQuery, Message

from database import async_session_maker
from database.models import Identity
from handlers.support_bot.media import collect_attachment
from services import tickets as svc
from services.tickets.forum import forum_chat_id


router = Router()


class InSupportForum(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        fid = forum_chat_id()
        return fid is not None and message.chat is not None and message.chat.id == fid


router.message.filter(InSupportForum())


async def _ticket_for(message: Message):
    topic_id = message.message_thread_id
    if not topic_id:
        return None
    async with async_session_maker() as session:
        ticket = await svc.get_ticket_by_topic(session, int(topic_id))
    return ticket


async def _notify_assignee(message: Message, ticket, target: int) -> None:
    fid = forum_chat_id()
    link = ""
    if fid and ticket.topic_id:
        link = f"\nhttps://telegram.me/c/{str(fid).replace('-100', '', 1)}/{ticket.topic_id}"
    try:
        await message.bot.send_message(
            target, f"🎫 Вам назначено обращение <code>{ticket.id.split('-')[0][:8]}</code>.{link}"
        )
    except Exception:
        pass


@router.message(F.forum_topic_closed)
async def on_topic_closed(message: Message) -> None:
    async with async_session_maker() as session:
        ticket = await svc.get_ticket_by_topic(session, int(message.message_thread_id or 0))
        if ticket is not None and ticket.status != svc.CLOSED:
            await svc.set_status(session, ticket_id=ticket.id, status=svc.CLOSED)
            await svc.notify_client_ticket_closed(session, ticket=ticket)
            await session.commit()


@router.message(F.forum_topic_reopened)
async def on_topic_reopened(message: Message) -> None:
    async with async_session_maker() as session:
        ticket = await svc.get_ticket_by_topic(session, int(message.message_thread_id or 0))
        if ticket is not None and ticket.status == svc.CLOSED:
            await svc.set_status(session, ticket_id=ticket.id, status=svc.OPEN)
            await session.commit()


@router.message(
    Command("close", "open", "pending", "priority", "note", "assign", "unassign", "tag", "untag"), F.message_thread_id
)
async def on_command(message: Message, command: CommandObject) -> None:
    ticket = await _ticket_for(message)
    if ticket is None:
        return
    cmd = command.command
    arg = (command.args or "").strip()
    async with async_session_maker() as session:
        if cmd == "note":
            if not arg:
                await message.reply("Формат: /note текст заметки")
                return
            await svc.add_message(
                session, ticket_id=ticket.id, author="note", body=arg[:4000], agent_tg_id=message.from_user.id
            )
            await session.commit()
            await message.reply("🔒 Заметка сохранена (клиент не увидит).")
            return
        if cmd == "priority":
            val = arg.lower() if arg.lower() in ("low", "normal", "high") else "high"
            fresh = await svc.get_ticket(session, ticket.id)
            if fresh is not None:
                fresh.priority = val
                await session.commit()
            await message.reply(f"Приоритет: {val}")
            return
        if cmd in ("assign", "unassign"):
            target = None
            if cmd == "assign":
                raw = arg.lstrip("@").strip()
                target = int(raw) if raw.isdigit() else message.from_user.id
            await svc.assign_ticket(session, ticket_id=ticket.id, agent_tg_id=target)
            await session.commit()
            if target and target != message.from_user.id:
                await _notify_assignee(message, ticket, target)
            await message.reply("Снято назначение." if target is None else f"Назначено на <code>{target}</code>.")
            return
        if cmd in ("tag", "untag"):
            if not arg:
                await message.reply("Формат: /tag метка")
                return
            fresh = await svc.get_ticket(session, ticket.id)
            tags = list(fresh.tags or []) if fresh is not None else []
            label = arg.strip()[:32]
            if cmd == "tag":
                tags.append(label)
            else:
                tags = [t for t in tags if t != label]
            await svc.set_tags(session, ticket_id=ticket.id, tags=tags)
            await session.commit()
            fresh2 = await svc.get_ticket(session, ticket.id)
            await message.reply("Метки: " + (", ".join(f"#{t}" for t in (fresh2.tags or [])) or "нет"))
            return
        status = {"close": svc.CLOSED, "open": svc.OPEN, "pending": svc.PENDING}[cmd]
        fresh = await svc.get_ticket(session, ticket.id)
        was_closed = fresh.status == svc.CLOSED if fresh is not None else False
        await svc.set_status(session, ticket_id=ticket.id, status=status)
        if status == svc.CLOSED and not was_closed and fresh is not None:
            await svc.notify_client_ticket_closed(session, ticket=fresh)
        await session.commit()
    if cmd == "close":
        await svc.set_topic_state(ticket, closed=True)
    elif cmd == "open":
        await svc.set_topic_state(ticket, closed=False)
    await message.reply(f"Статус: {status}")


@router.callback_query(F.data.startswith("sf:"))
async def on_agent_button(callback: CallbackQuery) -> None:
    fid = forum_chat_id()
    if fid is None or callback.message is None or callback.message.chat.id != fid:
        await callback.answer()
        return
    _, action, ticket_id = callback.data.split(":", 2)
    fresh = None
    admin_ref = None
    async with async_session_maker() as session:
        ticket = await svc.get_ticket(session, ticket_id)
        if ticket is None:
            await callback.answer("Обращение не найдено", show_alert=True)
            return
        if action == "close":
            was_closed = ticket.status == svc.CLOSED
            await svc.set_status(session, ticket_id=ticket_id, status=svc.CLOSED)
            if not was_closed:
                await svc.notify_client_ticket_closed(session, ticket=ticket)
        elif action == "open":
            await svc.set_status(session, ticket_id=ticket_id, status=svc.OPEN)
        elif action == "pending":
            await svc.set_status(session, ticket_id=ticket_id, status=svc.PENDING)
        elif action == "mine":
            await svc.assign_ticket(session, ticket_id=ticket_id, agent_tg_id=callback.from_user.id)
        elif action == "prio":
            ticket.priority = "normal" if (ticket.priority or "normal") == "high" else "high"
        else:
            await callback.answer()
            return
        fresh = await svc.get_ticket(session, ticket_id)
        try:
            client = await session.get(Identity, fresh.identity_id) if fresh is not None else None
            if client is not None:
                admin_ref = await svc.resolve_billing_user_ref(session, client)
        except Exception:
            admin_ref = None
        await session.commit()
    if fresh is None:
        await callback.answer()
        return
    if action == "close":
        await svc.set_topic_state(fresh, closed=True)
    elif action == "open":
        await svc.set_topic_state(fresh, closed=False)
    try:
        await callback.message.edit_reply_markup(reply_markup=svc.agent_controls_kb(fresh, admin_ref))
    except Exception:
        pass
    labels = {
        "close": "Обращение закрыто",
        "open": "Обращение открыто",
        "pending": "Переведено в ожидание",
        "mine": "Назначено на вас",
        "prio": "Приоритет обновлён",
    }
    await callback.answer(labels.get(action, "Готово"))


@router.message(F.message_thread_id, F.text | F.caption | F.photo | F.document)
async def on_agent_reply(message: Message) -> None:
    if (message.text or "").startswith("/"):
        return
    ticket = await _ticket_for(message)
    if ticket is None:
        return
    body, attachments = await collect_attachment(message)
    if not body and not attachments:
        return
    async with async_session_maker() as session:
        msg = await svc.add_message(
            session,
            ticket_id=ticket.id,
            author="agent",
            body=body[:4000],
            attachments=attachments,
            agent_tg_id=message.from_user.id,
        )
        if msg is None:
            return
        fresh = await svc.get_ticket(session, ticket.id)
        if fresh is not None:
            await svc.notify_client_of_reply(session, ticket=fresh, msg=msg)
        await session.commit()

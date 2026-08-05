from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Admin, Identity, Ticket, TicketMessage
from database.web_notifications import create_notification
from settings import config

from .events import publish_client_ticket_changed


def support_persona() -> str:
    name = (getattr(config, "PROJECT_NAME", "") or "").strip()
    return f"Поддержка {name}" if name else "Поддержка"


_UPLOAD_PREFIX = "/api/web/uploads/"
_UPLOAD_DIR = Path("static/web_uploads")
_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def _local_upload(url: str) -> Path | None:
    if not isinstance(url, str) or not url.startswith(_UPLOAD_PREFIX):
        return None
    return _UPLOAD_DIR / url[len(_UPLOAD_PREFIX) :]


def _support_bot():
    try:
        from support_bot import support_bot

        return support_bot
    except Exception:
        return None


async def notify_agents_new_ticket(session: AsyncSession, *, ticket: Ticket) -> None:
    short = ticket.id.split("-")[0][:8]
    cat = f" · {ticket.category}" if ticket.category else ""
    rows = [int(t) for t in (await session.execute(select(Admin.tg_id))).scalars().all() if t]

    from .forum import ensure_topic, forum_enabled

    if forum_enabled():
        from .service import build_client_context, get_thread

        client = await session.get(Identity, ticket.identity_id)
        context = await build_client_context(session, client) if client is not None else None
        thread = await get_thread(session, ticket.id)
        first = thread[0] if thread else None
        await ensure_topic(
            session,
            ticket,
            client=client,
            context=context,
            first_body=(first.body if first is not None else None),
            first_attachments=(first.attachments if first is not None else None),
        )
        await _webpush_agents(
            session, [t for t in rows if t > 0], f"Новое обращение {short}", f"Категория{cat or ': —'}"
        )
        return

    bot = _support_bot()
    if bot is not None:
        text = f"🆕 <b>Новое обращение</b> <code>{short}</code>{cat}\nОткройте бота поддержки, чтобы ответить."
        for tg_id in rows:
            if tg_id <= 0:
                continue
            try:
                await bot.send_message(tg_id, text)
            except Exception:
                pass
    await _webpush_agents(session, [t for t in rows if t > 0], f"Новое обращение {short}", f"Категория{cat or ': —'}")


async def notify_client_ticket_closed(session: AsyncSession, *, ticket: Ticket) -> None:
    if ticket.rating is not None:
        return
    client = await session.get(Identity, ticket.identity_id)
    if client is None:
        return
    tg_id = client.tg_id or 0
    if tg_id <= 0:
        return
    bot = _support_bot()
    if bot is None:
        return
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"{n}⭐", callback_data=f"sc:rate:{ticket.id}:{n}") for n in range(1, 6)]
        ]
    )
    try:
        await bot.send_message(
            tg_id,
            "<b>Обращение закрыто</b>\n━━━━━━━━━━━━━━━\n\nПомогите нам стать лучше — оцените работу поддержки:",
            reply_markup=kb,
        )
    except Exception:
        pass


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
    client = await session.get(Identity, ticket.identity_id)
    if client is None:
        return
    body = (msg.body or "").strip()
    preview = body[:120] or "Новый ответ по вашему обращению"
    await create_notification(
        session,
        user_id=client.tg_id or 0,
        identity_id=client.id,
        type="ticket",
        title=f"{support_persona()} ответила",
        message=preview,
        data={"ticket_id": ticket.id},
    )
    await publish_client_ticket_changed(client.id)
    tg_id = client.tg_id or 0
    if tg_id <= 0:
        await _email_client_reply(client, ticket, body)
        return
    support_bot = _support_bot()
    if support_bot is None:
        return
    try:
        await support_bot.send_message(tg_id, body)
    except Exception:
        pass
    from aiogram.types import FSInputFile

    for att in msg.attachments or []:
        local = _local_upload(att)
        if local is None or not local.exists():
            continue
        try:
            if str(local).lower().endswith(_IMAGE_EXT):
                await support_bot.send_photo(tg_id, FSInputFile(str(local)))
            else:
                await support_bot.send_document(tg_id, FSInputFile(str(local)))
        except Exception:
            pass

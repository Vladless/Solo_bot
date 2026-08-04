from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Identity, Ticket
from logger import logger
from settings import config


_UPLOAD_PREFIX = "/api/web/uploads/"
_UPLOAD_DIR = Path("static/web_uploads")
_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def forum_chat_id() -> int | None:
    raw = str(getattr(config, "SUPPORT_FORUM_CHAT_ID", "") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except Exception:
        return None


def _bot():
    try:
        from support_bot import support_bot

        return support_bot
    except Exception:
        return None


def forum_enabled() -> bool:
    return forum_chat_id() is not None and _bot() is not None


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
                    text="👤 Открыть клиента в боте", url=f"https://telegram.me/{username}?start=suser_{int(admin_ref)}"
                )
            ]
        ]
    )


async def _post(topic_id: int, text: str | None = None, attachments: list | None = None, reply_markup=None) -> None:
    bot = _bot()
    chat_id = forum_chat_id()
    if bot is None or chat_id is None or not topic_id:
        return
    from aiogram.types import FSInputFile

    if text:
        try:
            await bot.send_message(chat_id, text[:4000], message_thread_id=topic_id, reply_markup=reply_markup)
        except Exception:
            pass
    for att in attachments or []:
        if not isinstance(att, str) or not att.startswith(_UPLOAD_PREFIX):
            continue
        local = _UPLOAD_DIR / att[len(_UPLOAD_PREFIX) :]
        if not local.exists():
            continue
        try:
            if str(local).lower().endswith(_IMAGE_EXT):
                await bot.send_photo(chat_id, FSInputFile(str(local)), message_thread_id=topic_id)
            else:
                await bot.send_document(chat_id, FSInputFile(str(local)), message_thread_id=topic_id)
        except Exception:
            pass


def _context_lines(client: Identity, context: dict | None) -> str:
    parts = []
    who = []
    if getattr(client, "email", None):
        who.append(client.email)
    if getattr(client, "tg_id", None) and int(client.tg_id) > 0:
        who.append(f"tg <code>{client.tg_id}</code>")
    if who:
        parts.append("👤 " + " · ".join(who))
    if context:
        parts.append(
            f"🔑 ключи {context.get('keys_active', 0)}/{context.get('keys_total', 0)}"
            + (f" · до {context['nearest_expiry'][:10]}" if context.get("nearest_expiry") else "")
        )
        lp = context.get("last_payment")
        bal = context.get("balance")
        line = []
        if bal is not None:
            line.append(f"баланс {bal}₽")
        if lp:
            line.append(f"платёж {lp.get('amount')}₽/{lp.get('status')}")
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
) -> int | None:
    if ticket.topic_id:
        return ticket.topic_id
    chat_id = forum_chat_id()
    bot = _bot()
    if chat_id is None:
        logger.info("[Forum] SUPPORT_FORUM_CHAT_ID не задан — тема не создаётся")
        return None
    if bot is None:
        logger.warning("[Forum] support_bot недоступен — тема не создаётся")
        return None
    short = ticket.id.split("-")[0][:8]
    name = f"#{short} · {(ticket.category or 'обращение')}"[:120]
    try:
        topic = await bot.create_forum_topic(chat_id, name=name)
    except Exception as e:
        logger.error(
            "[Forum] create_forum_topic упал (chat_id={}): {} — проверьте: бот админ группы с правом «управление темами», верный ID, темы включены",
            chat_id,
            e,
        )
        return None
    ticket.topic_id = topic.message_thread_id
    await session.flush()
    logger.info("[Forum] тема создана: ticket={} topic_id={}", short, ticket.topic_id)
    header = f"🎫 <b>Обращение</b> <code>{short}</code>"
    if ticket.subject:
        header += f"\n{ticket.subject}"
    if client is not None:
        ctx_text = _context_lines(client, context)
        if ctx_text:
            header += "\n" + ctx_text
    header += (
        "\n\nОтвечайте в этой теме — сообщение уйдёт клиенту."
        "\nКоманды: /note /close /open /pending /priority /assign [id] /tag метка"
    )
    admin_ref = None
    if client is not None:
        from .service import resolve_billing_user_ref

        admin_ref = await resolve_billing_user_ref(session, client)
    await _post(ticket.topic_id, text=header, reply_markup=_client_bot_link(admin_ref))
    if first_body or first_attachments:
        await _post(
            ticket.topic_id, text=f"👤 <b>Клиент:</b>\n{(first_body or '').strip()}", attachments=first_attachments
        )
    return ticket.topic_id


async def post_client_message(ticket: Ticket, body: str, attachments: list | None = None) -> None:
    if not forum_enabled() or not ticket.topic_id:
        return
    await _post(ticket.topic_id, text=f"👤 <b>Клиент:</b>\n{(body or '').strip()}", attachments=attachments)


async def post_system(ticket: Ticket, text: str) -> None:
    if not forum_enabled() or not ticket.topic_id:
        return
    await _post(ticket.topic_id, text=text)


async def set_topic_state(ticket: Ticket, *, closed: bool) -> None:
    if not forum_enabled() or not ticket.topic_id:
        return
    bot = _bot()
    chat_id = forum_chat_id()
    try:
        if closed:
            await bot.close_forum_topic(chat_id, ticket.topic_id)
        else:
            await bot.reopen_forum_topic(chat_id, ticket.topic_id)
    except Exception:
        pass


async def delete_topic(ticket: Ticket) -> None:
    if not forum_enabled() or not ticket.topic_id:
        return
    bot = _bot()
    chat_id = forum_chat_id()
    try:
        await bot.delete_forum_topic(chat_id, ticket.topic_id)
    except Exception:
        pass

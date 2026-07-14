from datetime import UTC, datetime, timedelta

from sqlalchemy import Text, and_, case, cast, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Identity, Ticket, TicketMessage

from .events import publish_tickets_changed

OPEN = "open"
ANSWERED = "answered"
CLOSED = "closed"
PENDING = "pending"
STATUSES = {OPEN, ANSWERED, CLOSED, PENDING}

MAX_OPEN_PER_CLIENT = 5
CREATE_COOLDOWN_SEC = 30


class TicketRateLimited(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _now() -> datetime:
    return datetime.now(UTC)


async def _enforce_create_limits(session: AsyncSession, identity_id: str) -> None:
    cutoff = _now() - timedelta(seconds=CREATE_COOLDOWN_SEC)
    recent = await session.scalar(
        select(func.count())
        .select_from(Ticket)
        .where(Ticket.identity_id == identity_id, Ticket.created_at >= cutoff)
    )
    if recent and int(recent) > 0:
        raise TicketRateLimited("cooldown")
    open_count = await session.scalar(
        select(func.count())
        .select_from(Ticket)
        .where(Ticket.identity_id == identity_id, Ticket.status != CLOSED)
    )
    if open_count and int(open_count) >= MAX_OPEN_PER_CLIENT:
        raise TicketRateLimited("too_many_open")


async def create_ticket(
    session: AsyncSession,
    *,
    identity_id: str,
    body: str,
    category: str | None = None,
    subject: str | None = None,
    source: str = "web",
    attachments: list | None = None,
    ref_type: str | None = None,
    ref_id: str | None = None,
) -> Ticket:
    await _enforce_create_limits(session, identity_id)
    now = _now()
    ticket = Ticket(
        identity_id=identity_id,
        subject=(subject or "").strip()[:255] or None,
        category=category,
        ref_type=(ref_type or None),
        ref_id=(str(ref_id)[:64] if ref_id else None),
        source=source,
        status=OPEN,
        created_at=now,
        updated_at=now,
        last_message_at=now,
    )
    session.add(ticket)
    await session.flush()
    session.add(
        TicketMessage(
            ticket_id=ticket.id,
            author="client",
            body=(body or "").strip(),
            attachments=attachments or None,
            read_by_client=True,
            read_by_agent=False,
            created_at=now,
        )
    )
    await session.flush()
    await publish_tickets_changed()
    return ticket


async def get_ticket(session: AsyncSession, ticket_id: str) -> Ticket | None:
    return await session.get(Ticket, ticket_id)


async def get_ticket_by_topic(session: AsyncSession, topic_id: int) -> Ticket | None:
    return (await session.execute(select(Ticket).where(Ticket.topic_id == topic_id))).scalar_one_or_none()


async def add_message(
    session: AsyncSession,
    *,
    ticket_id: str,
    author: str,
    body: str,
    attachments: list | None = None,
    agent_tg_id: int | None = None,
) -> TicketMessage | None:
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        return None
    now = _now()
    is_note = author == "note"
    is_agent = author == "agent"
    msg = TicketMessage(
        ticket_id=ticket_id,
        author="note" if is_note else ("agent" if is_agent else "client"),
        agent_tg_id=agent_tg_id if (is_agent or is_note) else None,
        body=(body or "").strip(),
        attachments=attachments or None,
        read_by_client=True,
        read_by_agent=is_agent or is_note,
        created_at=now,
    )
    session.add(msg)
    if is_note:
        ticket.updated_at = now
        await session.flush()
        return msg
    msg.read_by_client = is_agent is False
    if ticket.status != CLOSED:
        ticket.status = ANSWERED if is_agent else OPEN
    ticket.last_message_at = now
    ticket.updated_at = now
    await session.flush()
    await publish_tickets_changed()
    return msg


async def set_status(session: AsyncSession, *, ticket_id: str, status: str) -> Ticket | None:
    if status not in STATUSES:
        return None
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        return None
    ticket.status = status
    ticket.updated_at = _now()
    await session.flush()
    await publish_tickets_changed()
    return ticket


async def set_rating(session: AsyncSession, *, ticket_id: str, rating: int) -> Ticket | None:
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None or ticket.status != CLOSED:
        return None
    ticket.rating = max(1, min(5, int(rating)))
    ticket.updated_at = _now()
    await session.flush()
    return ticket


async def set_tags(session: AsyncSession, *, ticket_id: str, tags: list[str]) -> Ticket | None:
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        return None
    clean = []
    for t in tags or []:
        s = str(t).strip()[:32]
        if s and s not in clean:
            clean.append(s)
        if len(clean) >= 12:
            break
    ticket.tags = clean or None
    ticket.updated_at = _now()
    await session.flush()
    return ticket


async def assign_ticket(session: AsyncSession, *, ticket_id: str, agent_tg_id: int | None) -> Ticket | None:
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        return None
    ticket.assigned_agent_tg_id = agent_tg_id
    ticket.updated_at = _now()
    await session.flush()
    return ticket


async def get_thread(session: AsyncSession, ticket_id: str) -> list[TicketMessage]:
    result = await session.execute(
        select(TicketMessage).where(TicketMessage.ticket_id == ticket_id).order_by(TicketMessage.created_at.asc())
    )
    return list(result.scalars().all())


async def list_for_identity(session: AsyncSession, identity_id: str) -> list[Ticket]:
    result = await session.execute(
        select(Ticket).where(Ticket.identity_id == identity_id).order_by(Ticket.last_message_at.desc())
    )
    return list(result.scalars().all())


async def list_for_agent(
    session: AsyncSession,
    *,
    status: str | None = None,
    search: str | None = None,
    assigned_to: int | None = None,
    tag: str | None = None,
    limit: int = 30,
    offset: int = 0,
) -> tuple[int, list[Ticket]]:
    conds = []
    if status in STATUSES:
        conds.append(Ticket.status == status)
    if assigned_to is not None:
        conds.append(Ticket.assigned_agent_tg_id == assigned_to)
    if tag:
        conds.append(Ticket.tags.contains([tag]))
    term = (search or "").strip()
    joined = bool(term)
    if term:
        like = f"%{term}%"
        body_match = select(TicketMessage.ticket_id).where(TicketMessage.body.ilike(like))
        conds.append(
            or_(
                Ticket.subject.ilike(like),
                Ticket.category.ilike(like),
                Ticket.id.ilike(like),
                Identity.email.ilike(like),
                cast(Identity.tg_id, Text).ilike(like),
                Ticket.id.in_(body_match),
            )
        )
    base = select(Ticket)
    count_base = select(func.count()).select_from(Ticket)
    if joined:
        base = base.join(Identity, Identity.id == Ticket.identity_id)
        count_base = count_base.join(Identity, Identity.id == Ticket.identity_id)
    if conds:
        base = base.where(*conds)
        count_base = count_base.where(*conds)
    total = int((await session.scalar(count_base)) or 0)
    priority_rank = case((Ticket.priority == "high", 2), (Ticket.priority == "low", 0), else_=1)
    result = await session.execute(
        base.order_by(priority_rank.desc(), Ticket.last_message_at.desc()).limit(limit).offset(offset)
    )
    return total, list(result.scalars().all())


async def delete_ticket(session: AsyncSession, ticket_id: str) -> bool:
    """Удаляет тикет (каскад messages) вместе с его форум-темой."""
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        return False
    from .forum import delete_topic

    await delete_topic(ticket)
    await session.delete(ticket)
    await session.flush()
    return True


async def delete_stale(session: AsyncSession, *, answered_days: int = 2, closed_days: int = 1) -> int:
    """Гигиена: удаляет протухшие тикеты вместе с их форум-темами.

    - «Отвечен» без ответа клиента дольше answered_days → удалить.
    - «Закрыт» без реакции клиента дольше closed_days → удалить.
    Сообщения удаляются каскадно (FK ondelete=CASCADE)."""
    now = _now()
    answered_cut = now - timedelta(days=answered_days)
    closed_cut = now - timedelta(days=closed_days)
    tickets = list(
        (
            await session.execute(
                select(Ticket).where(
                    or_(
                        and_(Ticket.status == ANSWERED, Ticket.last_message_at < answered_cut),
                        and_(Ticket.status == CLOSED, Ticket.updated_at < closed_cut),
                    )
                )
            )
        ).scalars().all()
    )
    if not tickets:
        return 0
    from .forum import delete_topic

    for t in tickets:
        await delete_topic(t)
        await session.delete(t)
    await session.flush()
    return len(tickets)


async def resolve_billing_user_ref(session: AsyncSession, identity) -> int | None:
    """Ref для админ-карточки клиента: реальный tg_id, иначе внутренний User.id.

    Работает и для веб-клиентов без Telegram (по identity_id → User)."""
    from database.models import User

    user = (await session.execute(select(User).where(User.identity_id == identity.id))).scalar_one_or_none()
    if user is None:
        tg = getattr(identity, "tg_id", None)
        if tg and int(tg) != 0:
            user = (await session.execute(select(User).where(User.tg_id == int(tg)))).scalar_one_or_none()
    if user is None:
        return None
    if user.tg_id and int(user.tg_id) > 0:
        return int(user.tg_id)
    return int(user.id)


async def build_client_context(session: AsyncSession, identity) -> dict | None:
    from datetime import datetime as _dt

    from database.models import Key, Payment, User

    tg_id = getattr(identity, "tg_id", None)
    if not tg_id or int(tg_id) <= 0:
        return None
    now_ms = int(_dt.now(UTC).timestamp() * 1000)
    user = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    keys = list((await session.execute(select(Key).where(Key.tg_id == tg_id))).scalars().all())
    active = [k for k in keys if (k.expiry_time or 0) > now_ms and not getattr(k, "is_frozen", False)]
    nearest = max((k.expiry_time for k in keys if k.expiry_time), default=None)
    last_pay = (
        await session.execute(
            select(Payment).where(Payment.tg_id == tg_id).order_by(Payment.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    return {
        "username": getattr(user, "username", None),
        "balance": getattr(user, "balance", None),
        "trial_used": bool(getattr(user, "trial", 0)) if user is not None else None,
        "keys_total": len(keys),
        "keys_active": len(active),
        "nearest_expiry": _dt.fromtimestamp(nearest / 1000, UTC).isoformat() if nearest else None,
        "last_payment": (
            {
                "amount": last_pay.amount,
                "status": last_pay.status,
                "created_at": (
                    (last_pay.created_at.replace(tzinfo=UTC) if last_pay.created_at.tzinfo is None else last_pay.created_at).isoformat()
                    if last_pay.created_at
                    else None
                ),
            }
            if last_pay is not None
            else None
        ),
    }


DEFAULT_CANNED_REPLIES = [
    {"title": "Приветствие", "body": "Здравствуйте! Уже смотрю ваше обращение, отвечу в ближайшее время."},
    {"title": "Смена локации", "body": "Попробуйте сменить локацию и переподключиться. Если не поможет — пришлите скриншот ошибки."},
    {"title": "Переустановка", "body": "Удалите профиль в приложении и добавьте подписку заново по актуальной ссылке из кабинета."},
    {"title": "Решено?", "body": "Проблема решена? Если да — закрою обращение. Если нет — опишите, что происходит сейчас."},
]


async def support_metrics(session: AsyncSession) -> dict:
    rows = (await session.execute(select(Ticket.status, func.count()).group_by(Ticket.status))).all()
    by_status = {str(s): int(c) for s, c in rows}
    total = sum(by_status.values())
    avg_rating = await session.scalar(select(func.avg(Ticket.rating)).where(Ticket.rating.isnot(None)))
    first_agent = (
        select(TicketMessage.ticket_id, func.min(TicketMessage.created_at).label("t"))
        .where(TicketMessage.author == "agent")
        .group_by(TicketMessage.ticket_id)
        .subquery()
    )
    frt = await session.scalar(
        select(func.avg(func.extract("epoch", first_agent.c.t - Ticket.created_at)))
        .select_from(Ticket)
        .join(first_agent, first_agent.c.ticket_id == Ticket.id)
    )
    resolution = await session.scalar(
        select(func.avg(func.extract("epoch", Ticket.updated_at - Ticket.created_at))).where(Ticket.status == CLOSED)
    )
    return {
        "by_status": by_status,
        "total": total,
        "open": by_status.get(OPEN, 0),
        "avg_rating": round(float(avg_rating), 2) if avg_rating is not None else None,
        "avg_first_response_sec": int(frt) if frt else None,
        "avg_resolution_sec": int(resolution) if resolution else None,
    }


async def mark_read(session: AsyncSession, *, ticket_id: str, by: str) -> None:
    if by not in ("client", "agent"):
        return
    col = TicketMessage.read_by_client if by == "client" else TicketMessage.read_by_agent
    await session.execute(
        update(TicketMessage).where(TicketMessage.ticket_id == ticket_id, col.is_(False)).values({col: True})
    )
    await session.flush()

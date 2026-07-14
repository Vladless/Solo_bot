import asyncio

from datetime import UTC

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_session, verify_identity_admin, verify_identity_agent, verify_identity_token
from config import REDIS_URL
from core.settings.modes_config import MODES_CONFIG
from database import async_session_maker
from database.models import Admin, Identity, Ticket, TicketMessage, User
from services import tickets as svc
from services.tickets.events import TICKETS_EVENTS_CHANNEL, tickets_client_channel


async def require_tickets_enabled() -> None:
    if not MODES_CONFIG.get("SUPPORT_TICKETS_ENABLED", False):
        raise HTTPException(status_code=404, detail="Система тикетов отключена")


router = APIRouter()
admin_router = APIRouter()

MAX_BODY = 4000
MAX_ATTACHMENTS = 10


class CreateTicketBody(BaseModel):
    body: str
    category: str | None = None
    subject: str | None = None
    attachments: list[str] | None = None
    ref_type: str | None = None
    ref_id: str | None = None


class MessageBody(BaseModel):
    body: str = ""
    attachments: list[str] | None = None
    internal: bool = False


class AgentPatchBody(BaseModel):
    status: str | None = None
    assigned_agent_tg_id: int | None = None
    unassign: bool = False
    priority: str | None = None
    tags: list[str] | None = None


class RatingBody(BaseModel):
    rating: int


def _clean_attachments(items: list[str] | None) -> list[str] | None:
    if not items:
        return None
    out = [str(x).strip() for x in items if isinstance(x, str) and str(x).strip()]
    return out[:MAX_ATTACHMENTS] or None


def _iso(dt) -> str | None:
    """ISO-время всегда с UTC-смещением: naive-значения (колонки без tz) считаем UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


def _msg_dict(m: TicketMessage) -> dict:
    return {
        "id": m.id,
        "author": m.author,
        "agent_tg_id": m.agent_tg_id,
        "body": m.body,
        "attachments": m.attachments or [],
        "created_at": _iso(m.created_at),
        "read_by_client": m.read_by_client,
        "read_by_agent": m.read_by_agent,
    }


def _ticket_dict(t: Ticket) -> dict:
    return {
        "id": t.id,
        "identity_id": t.identity_id,
        "subject": t.subject,
        "category": t.category,
        "priority": t.priority,
        "status": t.status,
        "source": t.source,
        "assigned_agent_tg_id": t.assigned_agent_tg_id,
        "rating": t.rating,
        "tags": t.tags or [],
        "ref_type": t.ref_type,
        "ref_id": t.ref_id,
        "created_at": _iso(t.created_at),
        "updated_at": _iso(t.updated_at),
        "last_message_at": _iso(t.last_message_at),
    }


@router.post("/tickets")
async def create_ticket(
    payload: CreateTicketBody,
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(verify_identity_token),
) -> dict:
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="Опишите проблему")
    try:
        ticket = await svc.create_ticket(
            session,
            identity_id=identity.id,
            body=body[:MAX_BODY],
            category=payload.category,
            subject=payload.subject,
            source="web",
            attachments=_clean_attachments(payload.attachments),
            ref_type=payload.ref_type,
            ref_id=payload.ref_id,
        )
    except svc.TicketRateLimited as e:
        detail = (
            "Слишком много открытых обращений — дождитесь ответа по текущим"
            if e.reason == "too_many_open"
            else "Вы только что создали обращение — подождите немного"
        )
        raise HTTPException(status_code=429, detail=detail)
    await svc.notify_agents_new_ticket(session, ticket=ticket)
    return _ticket_dict(ticket)


@router.get("/tickets")
async def my_tickets(
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(verify_identity_token),
) -> dict:
    items = await svc.list_for_identity(session, identity.id)
    return {"items": [_ticket_dict(t) for t in items]}


@router.get("/tickets/stream")
async def client_stream(request: Request):
    from api.depends import _identity_from_cookie

    async with async_session_maker() as session:
        identity = await _identity_from_cookie(session, request)
        await session.commit()
    if identity is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return _sse_response(request, tickets_client_channel(identity.id))


async def _owned(session: AsyncSession, ticket_id: str, identity: Identity) -> Ticket:
    ticket = await svc.get_ticket(session, ticket_id)
    if ticket is None or ticket.identity_id != identity.id:
        raise HTTPException(status_code=404, detail="Тикет не найден")
    return ticket


@router.get("/tickets/{ticket_id}")
async def my_ticket_thread(
    ticket_id: str = Path(...),
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(verify_identity_token),
) -> dict:
    ticket = await _owned(session, ticket_id, identity)
    thread = await svc.get_thread(session, ticket_id)
    await svc.mark_read(session, ticket_id=ticket_id, by="client")
    visible = [m for m in thread if m.author != "note"]
    return {"ticket": _ticket_dict(ticket), "messages": [_msg_dict(m) for m in visible]}


@router.post("/tickets/{ticket_id}/rate")
async def rate_ticket(
    payload: RatingBody,
    ticket_id: str = Path(...),
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(verify_identity_token),
) -> dict:
    await _owned(session, ticket_id, identity)
    ticket = await svc.set_rating(session, ticket_id=ticket_id, rating=payload.rating)
    if ticket is None:
        raise HTTPException(status_code=400, detail="Оценить можно только закрытое обращение")
    return _ticket_dict(ticket)


@router.post("/tickets/{ticket_id}/messages")
async def my_ticket_reply(
    payload: MessageBody,
    ticket_id: str = Path(...),
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(verify_identity_token),
) -> dict:
    await _owned(session, ticket_id, identity)
    body = (payload.body or "").strip()
    attachments = _clean_attachments(payload.attachments)
    if not body and not attachments:
        raise HTTPException(status_code=400, detail="Пустое сообщение")
    msg = await svc.add_message(
        session, ticket_id=ticket_id, author="client", body=body[:MAX_BODY], attachments=attachments
    )
    if svc.forum_enabled():
        ticket = await svc.get_ticket(session, ticket_id)
        if ticket is not None:
            await svc.post_client_message(ticket, body[:MAX_BODY], attachments)
    return _msg_dict(msg)


@admin_router.get("/tickets")
async def agent_inbox(
    status: str | None = Query(None),
    search: str | None = Query(None),
    assigned_to: int | None = Query(None),
    tag: str | None = Query(None),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    identity=Depends(verify_identity_agent),
    session: AsyncSession = Depends(get_session),
) -> dict:
    total, items = await svc.list_for_agent(
        session, status=status, search=search, assigned_to=assigned_to, tag=tag, limit=limit, offset=offset
    )
    return {"total": total, "items": [_ticket_dict(t) for t in items]}


@admin_router.get("/tickets/unanswered-count")
async def agent_unanswered_count(
    identity=Depends(verify_identity_agent),
    session: AsyncSession = Depends(get_session),
) -> dict:
    total, _ = await svc.list_for_agent(session, status=svc.OPEN, limit=1, offset=0)
    return {"count": total}


@admin_router.get("/tickets-metrics")
async def agent_metrics(
    identity=Depends(verify_identity_agent),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await svc.support_metrics(session)


@admin_router.get("/agents")
async def list_agents(
    identity=Depends(verify_identity_agent),
    session: AsyncSession = Depends(get_session),
) -> dict:
    from sqlalchemy import select as _select

    rows = (await session.execute(_select(Admin.tg_id, Admin.role))).all()
    tg_ids = [int(t) for t, _ in rows if t]
    names: dict[int, str] = {}
    if tg_ids:
        for tg, username, first in (
            await session.execute(_select(User.tg_id, User.username, User.first_name).where(User.tg_id.in_(tg_ids)))
        ).all():
            label = (username and f"@{username}") or first or None
            if tg is not None and label:
                names[int(tg)] = label
    items = []
    for tg, role in rows:
        if not tg:
            continue
        items.append({"tg_id": int(tg), "name": names.get(int(tg)) or str(tg), "role": role or "admin"})
    items.sort(key=lambda x: x["name"].lower())
    return {"items": items}


@admin_router.get("/canned-replies")
async def agent_canned_replies(identity=Depends(verify_identity_agent)) -> dict:
    from core.settings.modes_config import MODES_CONFIG

    raw = MODES_CONFIG.get("SUPPORT_CANNED_REPLIES")
    if not isinstance(raw, list) or not raw:
        raw = svc.DEFAULT_CANNED_REPLIES
    items = []
    for r in raw:
        if isinstance(r, dict) and r.get("body"):
            items.append({"title": str(r.get("title") or "")[:80], "body": str(r.get("body"))[:2000]})
    return {"items": items}


def _sse_response(request: Request, channel: str) -> StreamingResponse:
    async def event_generator():
        redis_client = None
        pubsub = None
        try:
            from redis.asyncio import from_url

            redis_client = from_url(REDIS_URL, encoding="utf-8", decode_responses=True, max_connections=4)
            pubsub = redis_client.pubsub(ignore_subscribe_messages=True)
            await pubsub.subscribe(channel)
            yield "retry: 3000\n\n"
            yield "data: changed\n\n"
            while True:
                if await request.is_disconnected():
                    break
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15.0)
                if message and message.get("type") == "message":
                    yield "data: changed\n\n"
                    continue
                yield ": keepalive\n\n"
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        finally:
            if pubsub is not None:
                try:
                    await pubsub.unsubscribe(channel)
                    await pubsub.close()
                except Exception:
                    pass
            if redis_client is not None:
                try:
                    await redis_client.aclose()
                except Exception:
                    pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@admin_router.get("/tickets/stream")
async def agent_stream(request: Request):
    from api.depends import _identity_from_cookie, resolve_identity_role

    async with async_session_maker() as session:
        identity = await _identity_from_cookie(session, request)
        role = await resolve_identity_role(session, identity) if identity is not None else "user"
        await session.commit()
    if identity is None or role not in ("admin", "moderator"):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return _sse_response(request, TICKETS_EVENTS_CHANNEL)


@admin_router.get("/tickets/{ticket_id}")
async def agent_thread(
    ticket_id: str = Path(...),
    identity=Depends(verify_identity_agent),
    session: AsyncSession = Depends(get_session),
) -> dict:
    ticket = await svc.get_ticket(session, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Тикет не найден")
    thread = await svc.get_thread(session, ticket_id)
    await svc.mark_read(session, ticket_id=ticket_id, by="agent")
    client = await session.get(Identity, ticket.identity_id)
    client_card = None
    context = None
    if client is not None:
        admin_ref = await svc.resolve_billing_user_ref(session, client)
        client_card = {
            "id": client.id,
            "tg_id": client.tg_id,
            "email": client.email,
            "admin_ref": admin_ref,
        }
        context = await svc.build_client_context(session, client)
    return {
        "ticket": _ticket_dict(ticket),
        "messages": [_msg_dict(m) for m in thread],
        "client": client_card,
        "context": context,
    }


@admin_router.post("/tickets/{ticket_id}/messages")
async def agent_reply(
    payload: MessageBody,
    ticket_id: str = Path(...),
    identity=Depends(verify_identity_agent),
    session: AsyncSession = Depends(get_session),
) -> dict:
    body = (payload.body or "").strip()
    attachments = _clean_attachments(payload.attachments)
    if not body and not attachments:
        raise HTTPException(status_code=400, detail="Пустое сообщение")
    is_note = bool(payload.internal)
    msg = await svc.add_message(
        session,
        ticket_id=ticket_id,
        author="note" if is_note else "agent",
        body=body[:MAX_BODY],
        attachments=None if is_note else attachments,
        agent_tg_id=getattr(identity, "tg_id", None),
    )
    if msg is None:
        raise HTTPException(status_code=404, detail="Тикет не найден")
    ticket = await svc.get_ticket(session, ticket_id)
    if not is_note and ticket is not None:
        await svc.notify_client_of_reply(session, ticket=ticket, msg=msg)
    if svc.forum_enabled() and ticket is not None:
        prefix = "🔒 Заметка (веб)" if is_note else "🛟 Оператор (веб)"
        await svc.post_system(ticket, f"{prefix}:\n{body[:MAX_BODY]}")
    return _msg_dict(msg)


@admin_router.patch("/tickets/{ticket_id}")
async def agent_patch(
    payload: AgentPatchBody,
    ticket_id: str = Path(...),
    identity=Depends(verify_identity_agent),
    session: AsyncSession = Depends(get_session),
) -> dict:
    ticket = await svc.get_ticket(session, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Тикет не найден")
    if payload.status is not None:
        was_closed = ticket.status == svc.CLOSED
        if await svc.set_status(session, ticket_id=ticket_id, status=payload.status) is None:
            raise HTTPException(status_code=400, detail="Недопустимый статус")
        if svc.forum_enabled() and payload.status in (svc.CLOSED, svc.OPEN):
            await svc.set_topic_state(ticket, closed=payload.status == svc.CLOSED)
        if payload.status == svc.CLOSED and not was_closed:
            await svc.notify_client_ticket_closed(session, ticket=ticket)
    if payload.unassign:
        await svc.assign_ticket(session, ticket_id=ticket_id, agent_tg_id=None)
    elif payload.assigned_agent_tg_id is not None:
        await svc.assign_ticket(session, ticket_id=ticket_id, agent_tg_id=payload.assigned_agent_tg_id)
    if payload.priority is not None:
        ticket.priority = payload.priority[:16]
    if payload.tags is not None:
        await svc.set_tags(session, ticket_id=ticket_id, tags=payload.tags)
    fresh = await svc.get_ticket(session, ticket_id)
    return _ticket_dict(fresh)


@admin_router.delete("/tickets/{ticket_id}")
async def agent_delete(
    ticket_id: str = Path(...),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    ok = await svc.delete_ticket(session, ticket_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Тикет не найден")
    return {"ok": True}

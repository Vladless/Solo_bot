from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Poll, PollMessage, PollVote


POLL_STATUS_OPEN = "open"
POLL_STATUS_CLOSED = "closed"


async def create_poll(
    session: AsyncSession,
    *,
    question: str,
    options: list[str],
    allows_multiple: bool = False,
    is_anonymous: bool = False,
    created_by_tg_id: int | None = None,
) -> Poll:
    poll = Poll(
        question=question,
        options=options,
        allows_multiple=allows_multiple,
        is_anonymous=is_anonymous,
        created_by_tg_id=created_by_tg_id,
    )
    session.add(poll)
    await session.flush()
    return poll


async def record_poll_message(
    session: AsyncSession,
    *,
    telegram_poll_id: str,
    poll_id: str,
    tg_id: int | None,
) -> None:
    stmt = (
        pg_insert(PollMessage)
        .values(telegram_poll_id=telegram_poll_id, poll_id=poll_id, tg_id=tg_id)
        .on_conflict_do_nothing(index_elements=[PollMessage.telegram_poll_id])
    )
    await session.execute(stmt)


async def set_sent_count(session: AsyncSession, poll_id: str, sent_count: int) -> None:
    await session.execute(update(Poll).where(Poll.id == poll_id).values(sent_count=sent_count))


async def resolve_poll_id(session: AsyncSession, telegram_poll_id: str) -> str | None:
    return await session.scalar(select(PollMessage.poll_id).where(PollMessage.telegram_poll_id == telegram_poll_id))


async def record_vote(
    session: AsyncSession,
    *,
    telegram_poll_id: str,
    tg_id: int,
    option_ids: list[int],
) -> str | None:
    poll_id = await resolve_poll_id(session, telegram_poll_id)
    if poll_id is None:
        return None

    poll = await session.get(Poll, poll_id)
    if poll is None or poll.status != POLL_STATUS_OPEN:
        return poll_id

    if not option_ids:
        await session.execute(delete(PollVote).where(PollVote.poll_id == poll_id, PollVote.tg_id == tg_id))
        return poll_id

    stmt = (
        pg_insert(PollVote)
        .values(poll_id=poll_id, tg_id=tg_id, option_ids=option_ids, voted_at=datetime.now(UTC))
        .on_conflict_do_update(
            index_elements=[PollVote.poll_id, PollVote.tg_id],
            set_={"option_ids": option_ids, "voted_at": datetime.now(UTC)},
        )
    )
    await session.execute(stmt)
    return poll_id


async def get_poll(session: AsyncSession, poll_id: str) -> Poll | None:
    return await session.get(Poll, poll_id)


async def list_polls(session: AsyncSession, *, limit: int = 10, offset: int = 0) -> list[Poll]:
    result = await session.execute(select(Poll).order_by(Poll.created_at.desc()).limit(limit).offset(offset))
    return list(result.scalars().all())


async def delete_poll(session: AsyncSession, poll_id: str) -> None:
    await session.execute(delete(Poll).where(Poll.id == poll_id))


async def close_poll(session: AsyncSession, poll_id: str) -> None:
    await session.execute(
        update(Poll).where(Poll.id == poll_id).values(status=POLL_STATUS_CLOSED, closed_at=datetime.now(UTC))
    )


async def get_poll_stats(session: AsyncSession, poll_id: str) -> dict:
    poll = await get_poll(session, poll_id)
    if poll is None:
        return {"total_voters": 0, "total_votes": 0, "counts": []}

    options = poll.options or []
    counts = [0] * len(options)
    total_voters = 0
    total_votes = 0

    result = await session.execute(select(PollVote.option_ids).where(PollVote.poll_id == poll_id))
    for (option_ids,) in result.all():
        ids = option_ids or []
        if not ids:
            continue
        total_voters += 1
        for oid in ids:
            if isinstance(oid, int) and 0 <= oid < len(counts):
                counts[oid] += 1
                total_votes += 1

    return {"total_voters": total_voters, "total_votes": total_votes, "counts": counts}

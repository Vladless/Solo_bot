import hashlib

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Poll, PollAnswerState, PollMessage, PollVote, User


POLL_STATUS_OPEN = "open"
POLL_STATUS_CLOSED = "closed"
PENDING_ANSWER_TTL = timedelta(days=7)
UPDATE_ORDER_WINDOW = timedelta(days=1)


async def _lock_telegram_poll(session: AsyncSession, telegram_poll_id: str) -> None:
    """Согласует регистрацию сообщения и ранние ответы."""
    lock_id = int.from_bytes(hashlib.blake2b(telegram_poll_id.encode(), digest_size=8).digest(), signed=True)
    await session.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id})


async def _lock_poll(session: AsyncSession, poll_id: str) -> Poll | None:
    """Удерживает состояние опроса до записи ответа или доставки."""
    result = await session.execute(
        select(Poll).where(Poll.id == poll_id).with_for_update().execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


def _utc(value: datetime) -> datetime:
    """Приводит сохранённое время к UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def cleanup_pending_answers(session: AsyncSession) -> int:
    """Удаляет просроченные ответы без зарегистрированного сообщения."""
    result = await session.execute(
        delete(PollAnswerState).where(
            PollAnswerState.poll_id.is_(None),
            PollAnswerState.received_at < datetime.now(UTC) - PENDING_ANSWER_TTL,
        )
    )
    return int(result.rowcount or 0)


async def _store_answer(
    session: AsyncSession, telegram_poll_id: str, tg_id: int, option_ids: list[int], update_id: int | None
) -> PollAnswerState | None:
    """Сохраняет последний ответ, включая отзыв голоса."""
    now = datetime.now(UTC)
    result = await session.execute(
        select(PollAnswerState)
        .where(PollAnswerState.telegram_poll_id == telegram_poll_id, PollAnswerState.tg_id == tg_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    answer = result.scalar_one_or_none()
    if answer is None:
        answer = PollAnswerState(telegram_poll_id=telegram_poll_id, tg_id=tg_id)
        session.add(answer)
    elif (
        update_id is not None
        and answer.update_id is not None
        and update_id <= answer.update_id
        and _utc(answer.received_at) >= now - UPDATE_ORDER_WINDOW
    ):
        return None
    answer.option_ids = list(option_ids)
    answer.update_id = update_id if update_id is not None else answer.update_id
    answer.received_at = now
    answer.applied = False
    await session.flush()
    return answer


async def _apply_answer(session: AsyncSession, poll: Poll, answer: PollAnswerState) -> None:
    """Применяет очередь без возврата к устаревшему выбору."""
    answer.poll_id = poll.id
    await session.flush()
    newer = None
    if answer.update_id is not None:
        received_at = _utc(answer.received_at)
        newer = await session.scalar(
            select(PollAnswerState.telegram_poll_id)
            .where(
                PollAnswerState.poll_id == poll.id,
                PollAnswerState.tg_id == answer.tg_id,
                PollAnswerState.telegram_poll_id != answer.telegram_poll_id,
                or_(
                    PollAnswerState.received_at > received_at + UPDATE_ORDER_WINDOW,
                    and_(
                        PollAnswerState.received_at >= received_at - UPDATE_ORDER_WINDOW,
                        PollAnswerState.update_id > answer.update_id,
                    ),
                ),
            )
            .limit(1)
        )
    if poll.status == POLL_STATUS_OPEN and not poll.is_anonymous and newer is None:
        if not answer.option_ids:
            await session.execute(delete(PollVote).where(PollVote.poll_id == poll.id, PollVote.tg_id == answer.tg_id))
        else:
            await session.execute(
                pg_insert(PollVote)
                .values(
                    poll_id=poll.id,
                    tg_id=answer.tg_id,
                    option_ids=answer.option_ids,
                    voted_at=answer.received_at,
                )
                .on_conflict_do_update(
                    index_elements=[PollVote.poll_id, PollVote.tg_id],
                    set_={"option_ids": answer.option_ids, "voted_at": answer.received_at},
                )
            )
    answer.applied = True
    await session.flush()


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
) -> bool:
    await _lock_telegram_poll(session, telegram_poll_id)
    poll = await _lock_poll(session, poll_id)
    if poll is None:
        await session.execute(delete(PollAnswerState).where(PollAnswerState.telegram_poll_id == telegram_poll_id))
        return False
    stmt = (
        pg_insert(PollMessage)
        .values(telegram_poll_id=telegram_poll_id, poll_id=poll_id, tg_id=tg_id)
        .on_conflict_do_nothing(index_elements=[PollMessage.telegram_poll_id])
        .returning(PollMessage.telegram_poll_id)
    )
    inserted = (await session.execute(stmt)).scalar_one_or_none()
    if inserted is not None:
        await session.execute(
            update(Poll).where(Poll.id == poll_id).values(sent_count=func.coalesce(Poll.sent_count, 0) + 1)
        )
    pending = await session.execute(
        select(PollAnswerState)
        .where(PollAnswerState.telegram_poll_id == telegram_poll_id, PollAnswerState.applied.is_(False))
        .order_by(PollAnswerState.update_id, PollAnswerState.tg_id)
    )
    for answer in pending.scalars():
        await _apply_answer(session, poll, answer)
    return True


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
    update_id: int | None = None,
) -> str | None:
    await _lock_telegram_poll(session, telegram_poll_id)
    poll_id = await resolve_poll_id(session, telegram_poll_id)
    poll = await _lock_poll(session, poll_id) if poll_id is not None else None
    if poll_id is not None and (poll is None or poll.status != POLL_STATUS_OPEN):
        return poll_id
    answer = await _store_answer(session, telegram_poll_id, tg_id, option_ids, update_id)
    if poll is not None and answer is not None:
        await _apply_answer(session, poll, answer)
    return poll_id


async def get_poll(session: AsyncSession, poll_id: str) -> Poll | None:
    return await session.get(Poll, poll_id)


async def get_poll_voters(
    session: AsyncSession, poll_id: str, *, limit: int | None = None, offset: int = 0
) -> list[dict]:
    """Возвращает голоса неанонимного опроса с профилями клиентов."""
    result = await session.execute(
        select(PollVote.tg_id, PollVote.option_ids, PollVote.voted_at, User.username, User.first_name, User.last_name)
        .join(Poll, Poll.id == PollVote.poll_id)
        .outerjoin(User, User.tg_id == PollVote.tg_id)
        .where(PollVote.poll_id == poll_id, Poll.is_anonymous.is_(False))
        .order_by(PollVote.voted_at, PollVote.tg_id)
        .limit(limit)
        .offset(offset)
    )
    return [dict(row._mapping) for row in result.all()]


async def list_polls(session: AsyncSession, *, limit: int = 10, offset: int = 0) -> list[Poll]:
    result = await session.execute(
        select(Poll).order_by(Poll.created_at.desc(), Poll.id.desc()).limit(limit).offset(offset)
    )
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

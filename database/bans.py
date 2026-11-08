from datetime import UTC, datetime

from sqlalchemy import and_, delete, exists, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.access.resolution import UserId
from database.models import BlockedUser, TelegramChatStatus, User


def telegram_chat_blocked(user_id, tg_id):
    """Проверяет недоступность текущего Telegram-чата клиента."""
    known = exists().where(TelegramChatStatus.tg_id == tg_id)
    blocked = exists().where(TelegramChatStatus.tg_id == tg_id, TelegramChatStatus.is_blocked.is_(True))
    legacy = exists().where(BlockedUser.user_id == user_id, BlockedUser.tg_id == tg_id)
    return and_(tg_id > 0, or_(blocked, and_(~known, legacy)))


def _observation_time(value: datetime) -> datetime:
    """Приводит время к точности событий Telegram."""
    if not isinstance(value, datetime):
        raise ValueError("Не получено время наблюдения Telegram-чата")
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).replace(microsecond=0)


async def apply_telegram_chat_observations(
    session: AsyncSession,
    observations: list[tuple[int | None, int, bool, datetime]],
) -> tuple[int, int]:
    """Применяет свежие наблюдения доступности чатов в одной транзакции."""
    normalized = [
        (int(uid) if uid is not None else None, int(tg), blocked, _observation_time(observed))
        for uid, tg, blocked, observed in observations
        if tg is not None and int(tg) > 0 and isinstance(blocked, bool)
    ]
    blocked_chats = sorted({tg for _, tg, blocked, _ in normalized if blocked})
    expected_owners = sorted({uid for uid, _, blocked, _ in normalized if blocked and uid is not None})
    owners = []
    if blocked_chats:
        owners = (
            await session.execute(
                select(User.id, User.tg_id)
                .where(or_(User.id.in_(expected_owners), User.tg_id.in_(blocked_chats)))
                .order_by(User.id)
                .with_for_update()
            )
        ).all()
    chat_by_owner = {int(uid): tg for uid, tg in owners}
    owner_by_chat = {int(tg): int(uid) for uid, tg in owners if tg is not None and tg > 0}
    latest: dict[int, tuple[bool, datetime]] = {}
    for uid, tg, blocked, observed in normalized:
        if blocked and uid is not None and chat_by_owner.get(uid) != tg:
            continue
        previous = latest.get(tg)
        if previous is None or (observed, not blocked) > (previous[1], not previous[0]):
            latest[tg] = (blocked, observed)

    added = removed = 0
    for tg in sorted(latest):
        blocked, observed = latest[tg]
        created = (
            await session.execute(
                insert(TelegramChatStatus)
                .values(tg_id=tg, is_blocked=blocked, observed_at=observed)
                .on_conflict_do_nothing(index_elements=[TelegramChatStatus.tg_id])
                .returning(TelegramChatStatus.tg_id)
            )
        ).scalar_one_or_none()
        if created is None:
            current = (
                await session.execute(
                    select(TelegramChatStatus)
                    .where(TelegramChatStatus.tg_id == tg)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one()
            current_rank = (_observation_time(current.observed_at), not current.is_blocked)
            rank = (observed, not blocked)
            if rank < current_rank:
                continue
            if rank > current_rank:
                await session.execute(
                    update(TelegramChatStatus)
                    .where(TelegramChatStatus.tg_id == tg)
                    .values(is_blocked=blocked, observed_at=observed)
                )

        if blocked:
            owner = owner_by_chat.get(tg)
            if owner is None:
                continue
            await session.execute(
                delete(BlockedUser).where(BlockedUser.user_id == owner, BlockedUser.tg_id.is_distinct_from(tg))
            )
            result = await session.execute(
                insert(BlockedUser)
                .values(user_id=owner, tg_id=tg)
                .on_conflict_do_nothing(index_elements=[BlockedUser.user_id])
                .returning(BlockedUser.user_id)
            )
            added += len(result.scalars().all())
        else:
            result = await session.execute(
                delete(BlockedUser).where(BlockedUser.tg_id == tg).returning(BlockedUser.user_id)
            )
            removed += len(result.scalars().all())
    return added, removed


async def save_blocked_recipients(
    session: AsyncSession,
    tg_ids: list[int],
    recipients: list[tuple[UserId, int]],
    *,
    observed_at: dict[int, datetime] | None = None,
    reachable_at: dict[int, datetime] | None = None,
) -> int:
    """Сохраняет наблюдения доставки с общей блокировкой владельцев."""
    now = datetime.now(UTC)
    pairs = {(int(uid), int(tg)) for uid, tg in recipients if tg is not None and int(tg) > 0}
    paired_chats = {tg for _, tg in pairs}
    legacy_chats = {int(tg) for tg in tg_ids if tg is not None and int(tg) > 0} - paired_chats
    observations = []
    for uid, tg in [*sorted(pairs), *((None, tg) for tg in sorted(legacy_chats))]:
        observed = observed_at.get(tg) if observed_at is not None else now
        if observed is not None:
            observations.append((uid, tg, True, observed))
    for tg, observed in (reachable_at or {}).items():
        if int(tg) > 0 and observed is not None:
            observations.append((None, int(tg), False, observed))
    added, _ = await apply_telegram_chat_observations(session, observations)
    return added


async def save_blocked_user_ids(
    session: AsyncSession, tg_ids: list[int], *, observed_at: dict[int, datetime] | None = None
) -> int:
    """Отмечает недоступные чаты и возвращает число новых записей."""
    return await save_blocked_recipients(session, tg_ids, [], observed_at=observed_at)


async def save_blocked_user_pairs(
    session: AsyncSession,
    recipients: list[tuple[UserId, int]],
    *,
    observed_at: dict[int, datetime] | None = None,
) -> int:
    """Отмечает недоступность чата при сохранении его владельца."""
    return await save_blocked_recipients(session, [], recipients, observed_at=observed_at)


async def remove_blocked_user_ids(
    session: AsyncSession, tg_ids: list[int], *, observed_at: dict[int, datetime] | None = None
) -> int:
    """Снимает отметки только с подтверждённо доступных чатов."""
    now = datetime.now(UTC)
    observations = []
    for tg in sorted({int(tg) for tg in tg_ids if tg is not None and int(tg) > 0}):
        observed = observed_at.get(tg) if observed_at is not None else now
        if observed is not None:
            observations.append((None, tg, False, observed))
    _, removed = await apply_telegram_chat_observations(session, observations)
    return removed


async def clear_blocked_chat_statuses(session: AsyncSession) -> int:
    """Очищает список недоступных чатов с защитой от старых ошибок."""
    statuses = (
        await session.execute(
            select(TelegramChatStatus.tg_id, TelegramChatStatus.observed_at).where(
                TelegramChatStatus.is_blocked.is_(True)
            )
        )
    ).all()
    legacy = (await session.execute(select(BlockedUser.user_id, BlockedUser.tg_id))).all()
    now = datetime.now(UTC)
    observed_at = {int(tg): max(now, _observation_time(observed)) for tg, observed in statuses}
    legacy_chats = {int(tg) for _, tg in legacy if tg is not None and tg > 0}
    for tg in legacy_chats:
        observed_at.setdefault(tg, now)
    await apply_telegram_chat_observations(
        session, [(None, tg, False, observed) for tg, observed in sorted(observed_at.items())]
    )
    await session.execute(delete(BlockedUser))
    return len(legacy) + len({int(tg) for tg, _ in statuses} - legacy_chats)

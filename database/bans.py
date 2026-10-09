from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.access.resolution import UserId
from database.models import BlockedUser, User
from logger import logger


async def save_blocked_user_ids(session: AsyncSession, tg_ids: list[int]) -> None:
    """Вставка списка telegram id в таблицу blocked_users батчами по 500."""
    if not tg_ids:
        return
    batch_size = 500
    total = 0
    for i in range(0, len(tg_ids), batch_size):
        batch = tg_ids[i : i + batch_size]
        res = await session.execute(select(User.id, User.tg_id).where(User.tg_id.in_(batch)))
        rows = res.all()
        uid_by_tg = {int(tgid): int(uid) for uid, tgid in rows if tgid is not None}
        values = [{"user_id": uid_by_tg[int(tg)], "tg_id": int(tg)} for tg in batch if int(tg) in uid_by_tg]
        if not values:
            continue
        stmt = insert(BlockedUser).values(values).on_conflict_do_nothing(index_elements=[BlockedUser.user_id])
        await session.execute(stmt)
        total += len(values)
    logger.info(f"📝 Добавлено до {total} пользователей в blocked_users")


async def remove_blocked_user_ids(session: AsyncSession, tg_ids: list[int]) -> None:
    """Удаление списка telegram id из таблицы blocked_users батчами по 500."""
    if not tg_ids:
        return
    batch_size = 500
    total = 0
    for i in range(0, len(tg_ids), batch_size):
        batch = tg_ids[i : i + batch_size]
        owner_ids = select(User.id).where(User.tg_id.in_(batch))
        stmt = delete(BlockedUser).where(BlockedUser.user_id.in_(owner_ids))
        result = await session.execute(stmt)
        total += result.rowcount
    logger.info(f"🗑 Удалено {total} пользователей из blocked_users")


async def save_blocked_user_pairs(session: AsyncSession, recipients: list[tuple[UserId, int]]) -> None:
    """Отмечает недоступность чата при сохранении его владельца."""
    pairs = {(int(uid), int(tg)) for uid, tg in recipients if tg is not None and int(tg) > 0}
    if not pairs:
        return
    user_ids = sorted({uid for uid, _ in pairs})
    for i in range(0, len(user_ids), 500):
        rows = (
            await session.execute(
                select(User.id, User.tg_id)
                .where(User.id.in_(user_ids[i : i + 500]))
                .order_by(User.id)
                .with_for_update()
            )
        ).all()
        values = [
            {"user_id": int(uid), "tg_id": int(tg)}
            for uid, tg in rows
            if tg is not None and (int(uid), int(tg)) in pairs
        ]
        if values:
            await session.execute(
                insert(BlockedUser).values(values).on_conflict_do_nothing(index_elements=[BlockedUser.user_id])
            )

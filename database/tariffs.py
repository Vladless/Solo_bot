import hashlib

from collections import defaultdict
from datetime import datetime

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Server, Tariff
from logger import logger


def create_subgroup_hash(subgroup_title: str, group_code: str) -> str:
    if not subgroup_title:
        return ""

    unique_key = f"{subgroup_title}:{group_code}"
    hash_object = hashlib.md5(unique_key.encode("utf-8"))
    return hash_object.hexdigest()[:8]


async def find_subgroup_by_hash(session: AsyncSession, subgroup_hash: str, group_code: str) -> str | None:
    result = await session.execute(
        select(Tariff.subgroup_title)
        .where(Tariff.group_code == group_code, Tariff.subgroup_title.isnot(None))
        .distinct()
    )
    subgroups = [row[0] for row in result.fetchall()]

    for subgroup_title in subgroups:
        if create_subgroup_hash(subgroup_title, group_code) == subgroup_hash:
            return subgroup_title

    return None


async def get_tariffs(
    session: AsyncSession, tariff_id: int = None, group_code: str = None, with_subgroup_weights: bool = False
):
    try:
        if tariff_id:
            result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
        elif group_code:
            result = await session.execute(
                select(Tariff).where(Tariff.group_code == group_code).order_by(Tariff.sort_order, Tariff.id)
            )
        else:
            result = await session.execute(select(Tariff).order_by(Tariff.sort_order, Tariff.id))

        tariffs = [dict(r.__dict__) for r in result.scalars().all()]

        if with_subgroup_weights and group_code:
            tariffs_without_order = [t for t in tariffs if t.get("sort_order") is None]
            if tariffs_without_order:
                for tariff in tariffs_without_order:
                    tariff["sort_order"] = 1
                    await session.execute(update(Tariff).where(Tariff.id == tariff["id"]).values(sort_order=1))
                await session.commit()

            grouped = defaultdict(list)
            for t in tariffs:
                grouped[t.get("subgroup_title")].append(t)

            subgroup_weights = {}
            for subgroup, tariffs_list in grouped.items():
                if subgroup:
                    total_weight = sum(t.get("sort_order", 1) for t in tariffs_list)
                    subgroup_weights[subgroup] = total_weight

            return {"tariffs": tariffs, "subgroup_weights": subgroup_weights}

        return tariffs
    except SQLAlchemyError as e:
        logger.error(f"[TARIFF] Ошибка при получении тарифов: {e}")
        return []


async def get_tariff_by_id(session: AsyncSession, tariff_id: int):
    try:
        result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
        tariff = result.scalar_one_or_none()
        return dict(tariff.__dict__) if tariff else None
    except SQLAlchemyError as e:
        logger.error(f"[TARIFF] Ошибка при получении тарифа по ID {tariff_id}: {e}")
        return None


async def get_tariffs_for_cluster(session: AsyncSession, cluster_name: str):
    try:
        server_row = await session.execute(
            select(Server.tariff_group).where(Server.cluster_name == cluster_name).limit(1)
        )
        row = server_row.first()

        if not row:
            server_row = await session.execute(
                select(Server.tariff_group).where(Server.server_name == cluster_name).limit(1)
            )
            row = server_row.first()

        if not row or not row[0]:
            return []

        group_code = row[0]
        result = await session.execute(
            select(Tariff)
            .where(Tariff.group_code == group_code, Tariff.is_active.is_(True))
            .order_by(Tariff.sort_order, Tariff.id)
        )
        return [dict(r.__dict__) for r in result.scalars().all()]
    except SQLAlchemyError as e:
        logger.error(f"[TARIFF] Ошибка при получении тарифов для кластера {cluster_name}: {e}")
        return []


async def create_tariff(session: AsyncSession, data: dict):
    try:
        data["created_at"] = datetime.utcnow()
        data["updated_at"] = datetime.utcnow()

        if "sort_order" not in data:
            group_code = data.get("group_code")
            if group_code:
                result = await session.execute(
                    select(func.max(Tariff.sort_order)).where(
                        Tariff.group_code == group_code, Tariff.sort_order.isnot(None)
                    )
                )
                max_order = result.scalar() or 0
            else:
                result = await session.execute(select(func.max(Tariff.sort_order)).where(Tariff.sort_order.isnot(None)))
                max_order = result.scalar() or 0

            data["sort_order"] = max_order + 1

        stmt = insert(Tariff).values(**data).returning(Tariff)
        result = await session.execute(stmt)
        await session.commit()
        return result.scalar_one()
    except SQLAlchemyError as e:
        logger.error(f"[TARIFF] Ошибка при создании тарифа: {e}")
        await session.rollback()
        return None


async def update_tariff(session: AsyncSession, tariff_id: int, updates: dict):
    if not updates:
        return False
    try:
        updates["updated_at"] = datetime.utcnow()
        await session.execute(update(Tariff).where(Tariff.id == tariff_id).values(**updates))
        await session.commit()
        return True
    except SQLAlchemyError as e:
        logger.error(f"[TARIFF] Ошибка при обновлении тарифа ID={tariff_id}: {e}")
        await session.rollback()
        return False


async def delete_tariff(session: AsyncSession, tariff_id: int):
    try:
        await session.execute(delete(Tariff).where(Tariff.id == tariff_id))
        await session.commit()
        return True
    except SQLAlchemyError as e:
        logger.error(f"[TARIFF] Ошибка при удалении тарифа ID={tariff_id}: {e}")
        await session.rollback()
        return False


async def check_tariff_exists(session: AsyncSession, tariff_id: int):
    try:
        result = await session.execute(select(Tariff).where(Tariff.id == tariff_id, Tariff.is_active.is_(True)))
        tariff = result.scalar_one_or_none()
        if tariff:
            return True
        logger.warning(f"[TARIFF] Тариф {tariff_id} не найден в БД")
        return False
    except SQLAlchemyError as e:
        logger.error(f"[TARIFF] Ошибка при проверке тарифа {tariff_id}: {e}")
        return False


async def get_tariff_sort_order(session: AsyncSession, tariff_id: int) -> int:
    try:
        result = await session.execute(select(Tariff.sort_order).where(Tariff.id == tariff_id))
        sort_order = result.scalar_one_or_none()

        if sort_order is None:
            await session.execute(update(Tariff).where(Tariff.id == tariff_id).values(sort_order=1))
            await session.commit()
            return 1

        return sort_order
    except SQLAlchemyError as e:
        logger.error(f"[TARIFF] Ошибка при получении sort_order для тарифа {tariff_id}: {e}")
        return None


async def move_tariff_up(session: AsyncSession, tariff_id: int) -> bool:
    try:
        current_order = await get_tariff_sort_order(session, tariff_id)
        new_order = max(1, current_order - 1)

        await session.execute(update(Tariff).where(Tariff.id == tariff_id).values(sort_order=new_order))
        await session.commit()
        return True
    except SQLAlchemyError as e:
        logger.error(f"[TARIFF] Ошибка при перемещении тарифа {tariff_id} вверх: {e}")
        await session.rollback()
        return False


async def move_tariff_down(session: AsyncSession, tariff_id: int) -> bool:
    try:
        current_order = await get_tariff_sort_order(session, tariff_id)
        new_order = current_order + 1

        await session.execute(update(Tariff).where(Tariff.id == tariff_id).values(sort_order=new_order))
        await session.commit()
        return True
    except SQLAlchemyError as e:
        logger.error(f"[TARIFF] Ошибка при перемещении тарифа {tariff_id} вниз: {e}")
        await session.rollback()
        return False


async def initialize_tariff_sort_orders(session: AsyncSession, group_code: str) -> bool:
    try:
        result = await session.execute(select(Tariff).where(Tariff.group_code == group_code).order_by(Tariff.id))
        tariffs = result.scalars().all()

        if not tariffs:
            return True

        for i, tariff in enumerate(tariffs):
            new_sort_order = 1 + i
            await session.execute(update(Tariff).where(Tariff.id == tariff.id).values(sort_order=new_sort_order))

        await session.commit()
        return True
    except SQLAlchemyError as e:
        logger.error(f"[TARIFF] Ошибка при инициализации sort_order для группы {group_code}: {e}")
        await session.rollback()
        return False


async def initialize_all_tariff_weights(session: AsyncSession) -> bool:
    try:
        result = await session.execute(select(Tariff).where(Tariff.sort_order.is_(None)))
        tariffs_without_weight = result.scalars().all()

        if not tariffs_without_weight:
            return True

        for tariff in tariffs_without_weight:
            await session.execute(update(Tariff).where(Tariff.id == tariff.id).values(sort_order=1))

        await session.commit()
        return True

    except SQLAlchemyError as e:
        logger.error(f"[TARIFF] Ошибка при инициализации весов тарифов: {e}")
        await session.rollback()
        return False

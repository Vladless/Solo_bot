from datetime import datetime

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Server, Tariff
from logger import logger


async def get_tariffs(
    session: AsyncSession, tariff_id: int = None, group_code: str = None
):
    try:
        if tariff_id:
            result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
        elif group_code:
            result = await session.execute(
                select(Tariff).where(Tariff.group_code == group_code)
            )
        else:
            result = await session.execute(select(Tariff))

        return [dict(r.__dict__) for r in result.scalars().all()]
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
            select(Server.tariff_group)
            .where(Server.cluster_name == cluster_name)
            .limit(1)
        )
        row = server_row.first()
        
        if not row:
            server_row = await session.execute(
                select(Server.tariff_group)
                .where(Server.server_name == cluster_name)
                .limit(1)
            )
            row = server_row.first()
            
        if not row or not row[0]:
            return []

        group_code = row[0]
        result = await session.execute(
            select(Tariff)
            .where(Tariff.group_code == group_code, Tariff.is_active.is_(True))
            .order_by(Tariff.id)
        )
        return [dict(r.__dict__) for r in result.scalars().all()]
    except SQLAlchemyError as e:
        logger.error(
            f"[TARIFF] Ошибка при получении тарифов для кластера {cluster_name}: {e}"
        )
        return []


async def create_tariff(session: AsyncSession, data: dict):
    try:
        data["created_at"] = datetime.utcnow()
        data["updated_at"] = datetime.utcnow()

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
        await session.execute(
            update(Tariff).where(Tariff.id == tariff_id).values(**updates)
        )
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
        result = await session.execute(
            select(Tariff)
            .where(Tariff.id == tariff_id, Tariff.is_active.is_(True))
        )
        tariff = result.scalar_one_or_none()
        if tariff:
            logger.info(f"[TARIFF] Тариф {tariff_id} найден в БД: {tariff.group_code}")
            return True
        logger.warning(f"[TARIFF] Тариф {tariff_id} не найден в БД")
        return False
    except SQLAlchemyError as e:
        logger.error(f"[TARIFF] Ошибка при проверке тарифа {tariff_id}: {e}")
        return False

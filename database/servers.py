from sqlalchemy import delete, insert, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Server
from logger import logger


async def create_server(
    session: AsyncSession,
    cluster_name: str,
    server_name: str,
    api_url: str,
    subscription_url: str,
    inbound_id: str,
):
    try:
        stmt = insert(Server).values(
            cluster_name=cluster_name,
            server_name=server_name,
            api_url=api_url,
            subscription_url=subscription_url,
            inbound_id=inbound_id,
        )
        await session.execute(stmt)
        await session.commit()
        logger.info(f"✅ Сервер {server_name} добавлен в кластер {cluster_name}")
    except SQLAlchemyError as e:
        logger.error(f"❌ Ошибка при добавлении сервера {server_name}: {e}")
        await session.rollback()
        raise


async def delete_server(session: AsyncSession, server_name: str):
    try:
        stmt = delete(Server).where(Server.server_name == server_name)
        await session.execute(stmt)
        await session.commit()
        logger.info(f"🗑 Сервер {server_name} удалён")
    except SQLAlchemyError as e:
        logger.error(f"❌ Ошибка при удалении сервера {server_name}: {e}")
        await session.rollback()
        raise


async def get_servers(session: AsyncSession, include_enabled: bool = False) -> dict:
    try:
        stmt = select(Server)
        result = await session.execute(stmt)
        servers = result.scalars().all()

        grouped = {}
        for s in servers:
            if not include_enabled and not s.enabled:
                continue
            cluster = s.cluster_name
            grouped.setdefault(cluster, []).append(
                {
                    "server_name": s.server_name,
                    "api_url": s.api_url,
                    "subscription_url": s.subscription_url,
                    "inbound_id": s.inbound_id,
                    "panel_type": s.panel_type,
                    "enabled": s.enabled,
                    "max_keys": s.max_keys,
                    "tariff_group": s.tariff_group,
                    "cluster_name": cluster,
                }
            )

        return grouped
    except SQLAlchemyError as e:
        logger.error(f"Ошибка при получении серверов: {e}")
        return {}


async def get_clusters(session: AsyncSession) -> list[str]:
    stmt = select(Server.cluster_name).distinct().order_by(Server.cluster_name)
    result = await session.execute(stmt)
    return [r[0] for r in result.all()]


async def check_unique_server_name(
    session: AsyncSession, server_name: str, cluster_name: str | None = None
) -> bool:
    stmt = select(Server).where(Server.server_name == server_name)
    if cluster_name:
        stmt = stmt.where(Server.cluster_name == cluster_name)
    result = await session.execute(stmt.limit(1))
    return result.scalar_one_or_none() is None


async def check_server_name_by_cluster(
    session: AsyncSession, server_name: str
) -> dict | None:
    try:
        result = await session.execute(
            select(Server.cluster_name).where(Server.server_name == server_name)
        )
        row = result.first()
        return {"cluster_name": row[0]} if row else None
    except SQLAlchemyError as e:
        logger.error(f"Ошибка при поиске кластера для сервера {server_name}: {e}")
        return None


async def get_cluster_name_by_server(
    session: AsyncSession, server_id_or_name: str
) -> str | None:
    stmt = (
        select(Server.cluster_name)
        .where(
            (Server.id == server_id_or_name) | (Server.server_name == server_id_or_name)
        )
        .limit(1)
    )

    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    return row

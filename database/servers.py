from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Key, Server, ServerSpecialgroup, ServerSubgroup, Tariff
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
    from handlers.utils import ALLOWED_GROUP_CODES

    try:
        stmt = select(Server)
        result = await session.execute(stmt)
        servers = result.scalars().all()

        ids = [s.id for s in servers]
        subs_map = {}
        if ids:
            r = await session.execute(
                select(ServerSubgroup.server_id, ServerSubgroup.subgroup_title).where(ServerSubgroup.server_id.in_(ids))
            )
            for sid, sg in r.all():
                subs_map.setdefault(sid, []).append(sg)

        groups_map = {}
        if ids:
            r2 = await session.execute(
                select(ServerSpecialgroup.server_id, ServerSpecialgroup.group_code).where(
                    ServerSpecialgroup.server_id.in_(ids)
                )
            )
            for sid, gc in r2.all():
                groups_map.setdefault(sid, []).append(gc)

        allowed = set(ALLOWED_GROUP_CODES)

        grouped = {}
        for s in servers:
            if not include_enabled and not s.enabled:
                continue
            cluster = s.cluster_name
            special = sorted({g for g in groups_map.get(s.id, []) if g in allowed})
            grouped.setdefault(cluster, []).append({
                "server_name": s.server_name,
                "api_url": s.api_url,
                "subscription_url": s.subscription_url,
                "inbound_id": s.inbound_id,
                "panel_type": s.panel_type,
                "enabled": s.enabled,
                "max_keys": s.max_keys,
                "tariff_group": s.tariff_group,
                "tariff_subgroups": subs_map.get(s.id, []),
                "special_groups": special,
                "cluster_name": cluster,
            })
        return grouped
    except SQLAlchemyError as e:
        logger.error(f"Ошибка при получении серверов: {e}")
        return {}


async def get_clusters(session: AsyncSession) -> list[str]:
    stmt = select(Server.cluster_name).distinct().order_by(Server.cluster_name)
    result = await session.execute(stmt)
    return [r[0] for r in result.all()]


async def check_unique_server_name(session: AsyncSession, server_name: str, cluster_name: str | None = None) -> bool:
    stmt = select(Server).where(Server.server_name == server_name)
    if cluster_name:
        stmt = stmt.where(Server.cluster_name == cluster_name)
    result = await session.execute(stmt.limit(1))
    return result.scalar_one_or_none() is None


async def check_server_name_by_cluster(session: AsyncSession, server_name: str) -> dict | None:
    try:
        result = await session.execute(select(Server.cluster_name).where(Server.server_name == server_name))
        row = result.first()
        return {"cluster_name": row[0]} if row else None
    except SQLAlchemyError as e:
        logger.error(f"Ошибка при поиске кластера для сервера {server_name}: {e}")
        return None


async def get_cluster_name_by_server(session: AsyncSession, server_id_or_name: str) -> str | None:
    stmt = (
        select(Server.cluster_name)
        .where((Server.id == server_id_or_name) | (Server.server_name == server_id_or_name))
        .limit(1)
    )

    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    return row


async def get_server_by_name(session: AsyncSession, server_name: str) -> dict | None:
    try:
        stmt = select(Server).where(Server.server_name == server_name)
        result = await session.execute(stmt)
        server = result.scalar_one_or_none()

        if server:
            return {
                "id": server.id,
                "cluster_name": server.cluster_name,
                "server_name": server.server_name,
                "api_url": server.api_url,
                "subscription_url": server.subscription_url,
                "inbound_id": server.inbound_id,
                "panel_type": server.panel_type,
                "enabled": server.enabled,
                "max_keys": server.max_keys,
                "tariff_group": server.tariff_group,
            }
        return None
    except SQLAlchemyError as e:
        logger.error(f"Ошибка при получении сервера {server_name}: {e}")
        return None


async def update_server_field(session: AsyncSession, server_name: str, field: str, value: any) -> bool:
    try:
        stmt = update(Server).where(Server.server_name == server_name).values(**{field: value})
        await session.execute(stmt)
        await session.commit()
        logger.info(f"✅ Поле {field} сервера {server_name} обновлено на {value}")
        return True
    except SQLAlchemyError as e:
        logger.error(f"❌ Ошибка при обновлении поля {field} сервера {server_name}: {e}")
        await session.rollback()
        return False


async def update_server_name_with_keys(session: AsyncSession, old_name: str, new_name: str) -> bool:
    try:
        from sqlalchemy import update

        from database.models import Key

        if not await check_unique_server_name(session, new_name):
            logger.error(f"❌ Сервер с именем {new_name} уже существует")
            return False

        stmt_server = update(Server).where(Server.server_name == old_name).values(server_name=new_name)
        await session.execute(stmt_server)

        stmt_keys = update(Key).where(Key.server_id == old_name).values(server_id=new_name)
        await session.execute(stmt_keys)

        await session.commit()
        logger.info(f"✅ Сервер переименован с {old_name} на {new_name}")
        return True
    except SQLAlchemyError as e:
        logger.error(f"❌ Ошибка при переименовании сервера {old_name}: {e}")
        await session.rollback()
        return False


async def get_available_clusters(session: AsyncSession) -> list[str]:
    try:
        stmt = select(Server.cluster_name).distinct().order_by(Server.cluster_name)
        result = await session.execute(stmt)
        return [row[0] for row in result.all()]
    except SQLAlchemyError as e:
        logger.error(f"Ошибка при получении списка кластеров: {e}")
        return []


async def update_server_cluster(session: AsyncSession, server_name: str, new_cluster: str) -> bool:
    try:
        server_data = await get_server_by_name(session, server_name)
        if not server_data:
            return False

        old_cluster = server_data["cluster_name"]

        stmt_remaining = select(func.count()).where(
            (Server.cluster_name == old_cluster) & (Server.server_name != server_name)
        )
        result = await session.execute(stmt_remaining)
        remaining_servers = result.scalar_one()

        if remaining_servers == 0:
            stmt_update_keys = update(Key).where(Key.server_id == old_cluster).values(server_id=new_cluster)
            await session.execute(stmt_update_keys)

        stmt_new_cluster = select(Server.tariff_group).where(Server.cluster_name == new_cluster).limit(1)
        result = await session.execute(stmt_new_cluster)
        new_tariff_group = result.scalar_one_or_none()

        await session.execute(
            update(Server)
            .where(Server.server_name == server_name)
            .values(cluster_name=new_cluster, tariff_group=new_tariff_group)
        )

        if server_data.get("id") is None:
            rid = await session.execute(select(Server.id).where(Server.server_name == server_name).limit(1))
            server_id = rid.scalar_one_or_none()
        else:
            server_id = server_data["id"]

        if server_id is not None and new_tariff_group is not None:
            await session.execute(
                update(ServerSubgroup).where(ServerSubgroup.server_id == server_id).values(group_code=new_tariff_group)
            )

        await session.commit()
        logger.info(
            f"✅ Сервер {server_name} перемещен в кластер {new_cluster} с обновлением тарифной группы и привязок подгрупп"
        )
        return True
    except SQLAlchemyError as e:
        logger.error(f"❌ Ошибка при обновлении кластера сервера {server_name}: {e}")
        await session.rollback()
        return False


async def resolve_device_limit_from_group(session: AsyncSession, server_id: str) -> int | None:
    r = await session.execute(select(Server.tariff_group).where(Server.server_name == server_id))
    group = r.scalar_one_or_none()
    if not group:
        return None
    q = await session.execute(
        select(Tariff.device_limit)
        .where(Tariff.group_code == group, Tariff.is_active.is_(True))
        .order_by(Tariff.duration_days.desc())
        .limit(1)
    )
    dl = q.scalar_one_or_none()
    return int(dl) if dl is not None else None


async def filter_cluster_by_subgroup(
    session: AsyncSession, cluster: list, target_subgroup: str, cluster_id: str
) -> list:
    names = [s.get("server_name") for s in cluster if s.get("server_name")]
    if not names:
        return []

    q_allowed = await session.execute(
        select(Server.server_name)
        .join(ServerSubgroup, ServerSubgroup.server_id == Server.id)
        .where(
            Server.server_name.in_(names),
            Server.enabled.is_(True),
            ServerSubgroup.subgroup_title == target_subgroup,
        )
    )
    allowed = {n for (n,) in q_allowed.all()}
    if allowed:
        return [s for s in cluster if s.get("server_name") in allowed]

    total_for_subgroup = await session.scalar(
        select(func.count()).select_from(ServerSubgroup).where(ServerSubgroup.subgroup_title == target_subgroup)
    )
    if not total_for_subgroup:
        logger.info(f"Для подгруппы {target_subgroup} нет ни одного сервера. Используем весь кластер {cluster_id}.")
        return cluster

    q_any = await session.execute(
        select(Server.server_name)
        .join(ServerSubgroup, ServerSubgroup.server_id == Server.id)
        .where(
            Server.server_name.in_(names),
            Server.enabled.is_(True),
        )
    )
    any_bound = {n for (n,) in q_any.all()}
    if any_bound:
        logger.warning(f"Нет серверов под подгруппу {target_subgroup} в кластере {cluster_id}. Продление пропущено.")
        return []

    logger.info(f"В кластере {cluster_id} нет привязок подгрупп. Продлеваем по всему кластеру.")
    return cluster

import hashlib

from collections import defaultdict
from datetime import datetime

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.redis_cache import cache_delete, cache_delete_pattern, cache_get, cache_key, cache_set
from database.models import Server, Tariff, TariffSubgroupSetting
from logger import logger
from settings.cache_config import TARIFFS_FOR_CLUSTER_CACHE_TTL_SEC, TARIFF_BY_ID_CACHE_TTL_SEC


def _row_to_cache_dict(row_dict: dict) -> dict:
    """Делает dict строки БД пригодным для JSON/Redis (datetime → str, убирает _sa_instance_state)."""
    out = {}
    for k, v in row_dict.items():
        if k.startswith("_"):
            continue
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


async def _invalidate_tariff_cache(tariff_id: int | None = None) -> None:
    """Сброс кэша тарифов при изменении (по id и списков по кластерам)."""
    if tariff_id is not None:
        await cache_delete(cache_key("tariff", tariff_id))
    await cache_delete_pattern("tariffs_cluster:*")
    await cache_delete_pattern("tariffs_public:*")


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


async def get_tariff_names_groups_subgroups_durations(
    session: AsyncSession, tariff_ids: list[int]
) -> tuple[dict[int, str], dict[int, str], dict[int, str | None], dict[int, int]]:
    """Один запрос: id, name, group_code, subgroup_title, duration_days → четыре словаря."""
    if not tariff_ids:
        return {}, {}, {}, {}

    result = await session.execute(
        select(
            Tariff.id,
            Tariff.name,
            Tariff.group_code,
            Tariff.subgroup_title,
            Tariff.duration_days,
        ).where(Tariff.id.in_(tariff_ids))
    )
    rows = result.all()
    names = {}
    groups = {}
    subgroups = {}
    durations = {}
    for tid, name, group_code, subgroup_title, duration_days in rows:
        names[tid] = name
        groups[tid] = group_code
        subgroups[tid] = subgroup_title
        durations[tid] = duration_days
    return names, groups, subgroups, durations


async def get_tariff_by_id(session: AsyncSession, tariff_id: int):
    key = cache_key("tariff", tariff_id)
    cached = await cache_get(key)
    if isinstance(cached, dict):
        return cached
    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()
    if not tariff:
        return None
    row = _row_to_cache_dict(dict(tariff.__dict__))
    await cache_set(key, row, TARIFF_BY_ID_CACHE_TTL_SEC)
    return row


async def get_tariff_group_codes(session: AsyncSession) -> list[str]:
    result = await session.execute(select(Tariff.group_code).distinct().order_by(Tariff.group_code))
    return [row[0] for row in result.fetchall() if row[0]]


async def get_active_tariff_by_id(session: AsyncSession, tariff_id: int) -> Tariff | None:
    """Возвращает ORM-объект Tariff по id, если тариф активен (is_active=True)."""
    result = await session.execute(select(Tariff).where(Tariff.id == int(tariff_id), Tariff.is_active.is_(True)))
    return result.scalar_one_or_none()


async def get_active_tariffs_by_group_code(session: AsyncSession, group_code: str) -> list[Tariff]:
    result = await session.execute(
        select(Tariff).where(Tariff.group_code == group_code, Tariff.is_active.is_(True)).order_by(Tariff.id)
    )
    return result.scalars().all()


async def get_tariffs_for_cluster(session: AsyncSession, cluster_name: str):
    key = cache_key("tariffs_cluster", cluster_name)
    cached = await cache_get(key)
    if isinstance(cached, list):
        return cached
    server_row = await session.execute(select(Server.tariff_group).where(Server.cluster_name == cluster_name).limit(1))
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
    rows = [_row_to_cache_dict(dict(r.__dict__)) for r in result.scalars().all()]
    await cache_set(key, rows, TARIFFS_FOR_CLUSTER_CACHE_TTL_SEC)
    return rows


async def create_tariff(session: AsyncSession, data: dict):
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
    await _invalidate_tariff_cache()
    return result.scalar_one()


async def update_tariff(session: AsyncSession, tariff_id: int, updates: dict):
    if not updates:
        return False
    updates["updated_at"] = datetime.utcnow()
    await session.execute(update(Tariff).where(Tariff.id == tariff_id).values(**updates))
    await _invalidate_tariff_cache(tariff_id)
    return True


async def delete_tariff(session: AsyncSession, tariff_id: int):
    await session.execute(delete(Tariff).where(Tariff.id == tariff_id))
    await _invalidate_tariff_cache(tariff_id)
    return True


async def check_tariff_exists(session: AsyncSession, tariff_id: int):
    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id, Tariff.is_active.is_(True)))
    tariff = result.scalar_one_or_none()
    if tariff:
        return True
    logger.warning(f"[TARIFF] Тариф {tariff_id} не найден в БД")
    return False


async def get_vless_enabled(session: AsyncSession, tariff_id: int | None) -> bool:
    """Возвращает, включён ли VLESS у тарифа (по кэшированному get_tariff_by_id)."""
    if not tariff_id:
        return False
    tariff = await get_tariff_by_id(session, int(tariff_id))
    if not tariff:
        return False
    return bool(tariff.get("vless"))


async def get_vless_enabled_batch(session: AsyncSession, tariff_ids: list[int]) -> dict[int, bool]:
    """
    Один запрос: для списка tariff_id возвращает dict[tariff_id -> vless].
    Использовать в списках ключей вместо N вызовов get_vless_enabled.
    """
    if not tariff_ids:
        return {}
    unique_ids = list(dict.fromkeys(tariff_ids))
    result = await session.execute(select(Tariff.id, Tariff.vless).where(Tariff.id.in_(unique_ids)))
    return {row[0]: bool(row[1]) for row in result.all()}


async def get_tariff_sort_order(session: AsyncSession, tariff_id: int) -> int:
    result = await session.execute(select(Tariff.sort_order).where(Tariff.id == tariff_id))
    sort_order = result.scalar_one_or_none()

    if sort_order is None:
        await session.execute(update(Tariff).where(Tariff.id == tariff_id).values(sort_order=1))
        await _invalidate_tariff_cache(tariff_id)
        return 1

    return sort_order


async def move_tariff_up(session: AsyncSession, tariff_id: int) -> bool:
    current_order = await get_tariff_sort_order(session, tariff_id)
    new_order = max(1, current_order - 1)

    await session.execute(update(Tariff).where(Tariff.id == tariff_id).values(sort_order=new_order))
    await _invalidate_tariff_cache(tariff_id)
    return True


async def move_tariff_down(session: AsyncSession, tariff_id: int) -> bool:
    current_order = await get_tariff_sort_order(session, tariff_id)
    new_order = current_order + 1

    await session.execute(update(Tariff).where(Tariff.id == tariff_id).values(sort_order=new_order))
    await _invalidate_tariff_cache(tariff_id)
    return True


async def move_subgroup(session: AsyncSession, group_code: str, subgroup_title: str, direction: str) -> bool:
    """Двигает подгруппу целиком в сквозном порядке: меняет её местами с соседним
    элементом (тарифом или подгруппой) и перенумеровывает sort_order всей группы 1..N.
    direction: 'up' | 'down'."""
    rows = list(
        (
            await session.execute(
                select(Tariff).where(Tariff.group_code == group_code).order_by(Tariff.sort_order, Tariff.id)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return False

    grouped: dict = defaultdict(list)
    for t in rows:
        grouped[t.subgroup_title].append(t)

    items: list[tuple[int, str, object]] = []
    for t in grouped.get(None, []):
        items.append((int(t.sort_order or 0), "tariff", t))
    for sub in sorted(s for s in grouped if s):
        rep = min(int(t.sort_order or 0) for t in grouped[sub])
        items.append((rep, "subgroup", sub))
    items.sort(key=lambda x: x[0])
    order_list = [(kind, payload) for _, kind, payload in items]

    idx = next((i for i, (k, p) in enumerate(order_list) if k == "subgroup" and p == subgroup_title), None)
    if idx is None:
        return False
    swap = idx - 1 if direction == "up" else idx + 1
    if swap < 0 or swap >= len(order_list):
        return False
    order_list[idx], order_list[swap] = order_list[swap], order_list[idx]

    n = 1
    for kind, payload in order_list:
        if kind == "tariff":
            payload.sort_order = n
            n += 1
        else:
            for t in grouped[payload]:
                t.sort_order = n
                n += 1

    await _invalidate_tariff_cache()
    return True


async def initialize_tariff_sort_orders(session: AsyncSession, group_code: str) -> bool:
    result = await session.execute(select(Tariff).where(Tariff.group_code == group_code).order_by(Tariff.id))
    tariffs = result.scalars().all()

    if not tariffs:
        return True

    for i, tariff in enumerate(tariffs):
        new_sort_order = 1 + i
        await session.execute(update(Tariff).where(Tariff.id == tariff.id).values(sort_order=new_sort_order))

    await _invalidate_tariff_cache()
    return True


async def initialize_all_tariff_weights(session: AsyncSession) -> bool:
    result = await session.execute(select(Tariff).where(Tariff.sort_order.is_(None)))
    tariffs_without_weight = result.scalars().all()

    if not tariffs_without_weight:
        return True

    for tariff in tariffs_without_weight:
        await session.execute(update(Tariff).where(Tariff.id == tariff.id).values(sort_order=1))

    await _invalidate_tariff_cache()
    return True


async def get_subgroup_description(session: AsyncSession, group_code: str, subgroup_title: str) -> str | None:
    row = (
        await session.execute(
            select(TariffSubgroupSetting.description).where(
                TariffSubgroupSetting.group_code == group_code,
                TariffSubgroupSetting.subgroup_title == subgroup_title,
            )
        )
    ).scalar_one_or_none()
    return row or None


async def set_subgroup_description(
    session: AsyncSession, group_code: str, subgroup_title: str, description: str | None
) -> None:
    existing = (
        await session.execute(
            select(TariffSubgroupSetting).where(
                TariffSubgroupSetting.group_code == group_code,
                TariffSubgroupSetting.subgroup_title == subgroup_title,
            )
        )
    ).scalar_one_or_none()
    if existing:
        existing.description = description
        existing.updated_at = datetime.utcnow()
    else:
        session.add(
            TariffSubgroupSetting(
                group_code=group_code,
                subgroup_title=subgroup_title,
                description=description,
            )
        )

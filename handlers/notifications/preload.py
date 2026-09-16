from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_all_keys
from database.models import Key, Tariff, User
from database.models.users import BlockedUser, ManualBan
from logger import logger


async def preload_notification_data(session: AsyncSession) -> dict:
    stmt = (
        select(
            Key,
            Tariff,
            User.balance.label("user_balance"),
        )
        .outerjoin(Tariff, Key.tariff_id == Tariff.id)
        .outerjoin(User, Key.user_id == User.id)
        .where(
            Key.is_frozen.is_(False),
            ~exists().where(BlockedUser.tg_id == Key.tg_id),
            ~exists().where(
                ManualBan.user_id == Key.user_id,
                or_(ManualBan.until.is_(None), ManualBan.until > datetime.now(timezone.utc)),
            ),
        )
    )

    result = await session.execute(stmt)
    rows = result.all()

    keys_data = {}
    tariffs_cache = {}
    balances_cache = {}

    for row in rows:
        key = row[0]
        tariff = row[1]
        balance = row[2] or 0.0

        keys_data[key.client_id] = {
            "key": key,
            "tariff": dict(tariff.__dict__) if tariff else None,
            "balance": float(balance),
        }

        if tariff and tariff.id not in tariffs_cache:
            tariffs_cache[tariff.id] = dict(tariff.__dict__)

        balances_cache[key.tg_id] = float(balance)

    return {
        "keys_data": keys_data,
        "tariffs_cache": tariffs_cache,
        "balances_cache": balances_cache,
    }


async def preload_with_fallback(session: AsyncSession) -> tuple[list, dict | None]:
    try:
        preload_data = await preload_notification_data(session)
        keys = [data["key"] for data in preload_data["keys_data"].values()]
        logger.info(f"Предзагружено: {len(keys)} ключей, {len(preload_data['tariffs_cache'])} тарифов")
        return keys, preload_data
    except Exception as error:
        logger.error(f"Ошибка предзагрузки: {error}")
        try:
            keys = await get_all_keys(session=session)
            keys = [k for k in keys if not k.is_frozen]
            logger.info(f"Fallback: {len(keys)} ключей")
            return keys, None
        except Exception as fallback_error:
            logger.error(f"Ошибка fallback: {fallback_error}")
            return [], None

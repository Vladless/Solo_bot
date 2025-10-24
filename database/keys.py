from datetime import datetime

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Key, User
from logger import logger


async def store_key(
    session: AsyncSession,
    tg_id: int,
    client_id: str,
    email: str,
    expiry_time: int,
    key: str,
    server_id: str,
    remnawave_link: str = None,
    tariff_id: int = None,
    alias: str = None,
):
    try:
        exists = await session.execute(select(Key).where(Key.tg_id == tg_id, Key.client_id == client_id))
        existing_key = exists.scalar_one_or_none()
        
        if existing_key:
            await session.execute(
                update(Key)
                .where(Key.tg_id == tg_id, Key.client_id == client_id)
                .values(
                    email=email,
                    expiry_time=expiry_time,
                    key=key,
                    server_id=server_id,
                    remnawave_link=remnawave_link,
                    tariff_id=tariff_id,
                    alias=alias,
                )
            )
            logger.info(f"[Store Key] Ключ обновлён: tg_id={tg_id}, client_id={client_id}, server_id={server_id}")
        else:
            new_key = Key(
                tg_id=tg_id,
                client_id=client_id,
                email=email,
                created_at=int(datetime.utcnow().timestamp() * 1000),
                expiry_time=expiry_time,
                key=key,
                server_id=server_id,
                remnawave_link=remnawave_link,
                tariff_id=tariff_id,
                alias=alias,
            )
            session.add(new_key)
            logger.info(f"[Store Key] Ключ создан: tg_id={tg_id}, client_id={client_id}, server_id={server_id}")
        
        await session.commit()

    except SQLAlchemyError as e:
        logger.error(f"❌ Ошибка при сохранении ключа: {e}")
        await session.rollback()


async def get_keys(session: AsyncSession, tg_id: int):
    result = await session.execute(select(Key).where(Key.tg_id == tg_id))
    return result.scalars().all()


async def get_all_keys(session: AsyncSession):
    result = await session.execute(select(Key))
    return result.scalars().all()


async def get_key_by_server(session: AsyncSession, tg_id: int, client_id: str):
    stmt = select(Key).where(Key.tg_id == tg_id, Key.client_id == client_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_key_details(session: AsyncSession, email: str) -> dict | None:
    stmt = select(Key, User).join(User, Key.tg_id == User.tg_id).where(Key.email == email)
    result = await session.execute(stmt)
    row = result.first()
    if not row:
        return None

    key, user = row
    expiry_date = datetime.utcfromtimestamp(key.expiry_time / 1000)
    current_date = datetime.utcnow()
    time_left = expiry_date - current_date

    if time_left.total_seconds() <= 0:
        days_left_message = "<b>Ключ истек.</b>"
    elif time_left.days > 0:
        days_left_message = f"Осталось дней: <b>{time_left.days}</b>"
    else:
        hours_left = time_left.seconds // 3600
        days_left_message = f"Осталось часов: <b>{hours_left}</b>"

    return {
        "key": key.key,
        "remnawave_link": key.remnawave_link,
        "server_id": key.server_id,
        "created_at": key.created_at,
        "expiry_time": key.expiry_time,
        "client_id": key.client_id,
        "tg_id": user.tg_id,
        "email": key.email,
        "is_frozen": key.is_frozen,
        "balance": user.balance,
        "alias": key.alias,
        "expiry_date": expiry_date.strftime("%d %B %Y года %H:%M"),
        "days_left_message": days_left_message,
        "link": key.key or key.remnawave_link,
        "cluster_name": key.server_id,
        "location_name": key.server_id,
        "tariff_id": key.tariff_id,
    }


async def get_key_count(session: AsyncSession, tg_id: int) -> int:
    result = await session.execute(select(func.count()).select_from(Key).where(Key.tg_id == tg_id))
    return result.scalar() or 0


async def delete_key(session: AsyncSession, identifier: int | str):
    stmt = delete(Key).where(Key.tg_id == identifier if str(identifier).isdigit() else Key.client_id == identifier)
    await session.execute(stmt)
    await session.commit()
    logger.info(f"Ключ с идентификатором {identifier} удалён")


async def update_key_expiry(session: AsyncSession, client_id: str, new_expiry_time: int):
    await session.execute(update(Key).where(Key.client_id == client_id).values(expiry_time=new_expiry_time))
    await session.commit()
    logger.info(f"Срок действия ключа {client_id} обновлён до {new_expiry_time}")


async def get_client_id_by_email(session: AsyncSession, email: str):
    result = await session.execute(select(Key.client_id).where(Key.email == email))
    return result.scalar_one_or_none()


async def update_key_notified(session: AsyncSession, tg_id: int, client_id: str):
    await session.execute(update(Key).where(Key.tg_id == tg_id, Key.client_id == client_id).values(notified=True))
    await session.commit()


async def mark_key_as_frozen(session: AsyncSession, tg_id: int, client_id: str, time_left: int):
    await session.execute(
        text(
            """
            UPDATE keys
            SET expiry_time = :expiry,
                is_frozen = TRUE
            WHERE tg_id = :tg_id
              AND client_id = :client_id
        """
        ),
        {"expiry": time_left, "tg_id": tg_id, "client_id": client_id},
    )


async def mark_key_as_unfrozen(session: AsyncSession, tg_id: int, client_id: str, new_expiry_time: int):
    await session.execute(
        text(
            """
            UPDATE keys
            SET expiry_time = :expiry,
                is_frozen = FALSE
            WHERE tg_id = :tg_id
              AND client_id = :client_id
        """
        ),
        {"expiry": new_expiry_time, "tg_id": tg_id, "client_id": client_id},
    )


async def update_key_tariff(session: AsyncSession, client_id: str, tariff_id: int):
    await session.execute(update(Key).where(Key.client_id == client_id).values(tariff_id=tariff_id))
    await session.commit()
    logger.info(f"Тариф ключа {client_id} обновлён на {tariff_id}")


async def get_subscription_link(session: AsyncSession, email: str) -> str | None:
    result = await session.execute(select(func.coalesce(Key.key, Key.remnawave_link)).where(Key.email == email))
    return result.scalar_one_or_none()


async def update_key_client_id(session: AsyncSession, email: str, new_client_id: str):
    await session.execute(update(Key).where(Key.email == email).values(client_id=new_client_id))
    await session.commit()
    logger.info(f"client_id обновлён для {email} -> {new_client_id}")


async def update_key_link(session: AsyncSession, email: str, link: str) -> bool:
    q = update(Key).where(Key.email == email).values(key=link).returning(Key.client_id)
    res = await session.execute(q)
    await session.commit()
    return res.scalar_one_or_none() is not None

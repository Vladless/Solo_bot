import hashlib
from collections.abc import AsyncGenerator

from fastapi import Depends, HTTPException, Header, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session_maker
from database.models import Admin


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def verify_admin_token(
    admin_id: int = Query(..., alias="tg_id"),
    token: str = Header(..., alias="X-Token"),
    session: AsyncSession = Depends(get_session),
) -> Admin:
    hashed = hash_token(token)
    result = await session.execute(select(Admin).where(Admin.tg_id == admin_id, Admin.token == hashed))
    admin = result.scalar_one_or_none()
    if not admin:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return admin

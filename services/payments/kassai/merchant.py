import asyncio
import hashlib
import hmac
import time

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text

from database.db import async_session_maker
from database.settings import get_setting, set_setting


def sign_merchant_request(payload: dict, secret: str) -> str:
    """Подписывает отсортированные параметры merchant API."""
    values = "|".join(str(payload[key]) for key in sorted(payload) if key != "signature")
    return hmac.new(secret.encode("utf-8"), values.encode("utf-8"), hashlib.sha256).hexdigest()


@asynccontextmanager
async def _merchant_request_lock(shop: str) -> AsyncIterator[None]:
    """Удерживает межпроцессную блокировку на время запроса магазина."""
    while True:
        async with async_session_maker() as session:
            result = await session.execute(
                text("SELECT pg_try_advisory_xact_lock(hashtextextended(:merchant_key, 0))"),
                {"merchant_key": f"solo:kassai:merchant:{shop}"},
            )
            if result.scalar_one():
                try:
                    yield
                finally:
                    await session.rollback()
                return
            await session.rollback()
        await asyncio.sleep(0.05)


@asynccontextmanager
async def merchant_request_nonce(shop_id: str | int) -> AsyncIterator[int]:
    """Сохраняет возрастающий nonce и сериализует запросы магазина."""
    shop = str(shop_id).strip()
    if not shop or isinstance(shop_id, bool):
        raise ValueError("Не указан магазин KassaI")
    if shop.isdecimal():
        shop = str(int(shop))
    key = f"KASSAI_MERCHANT_NONCE:{shop}"
    async with _merchant_request_lock(shop):
        async with async_session_maker() as session:
            previous = await get_setting(session, key, 0)
            if isinstance(previous, bool) or not isinstance(previous, int) or previous < 0:
                raise ValueError("Повреждено состояние запросов KassaI")
            nonce = max(previous + 1, time.time_ns() // 1_000_000)
            await set_setting(session, key, nonce, "Последний nonce merchant API KassaI")
            await session.commit()
        yield nonce

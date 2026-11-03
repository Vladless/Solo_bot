from aiohttp import web

from core.settings.runtime_sync import maybe_sync_runtime_configs
from database import async_session_maker
from services.payments.yookassa.page_http import (
    MAX_PAGE_REQUEST_BYTES,
    page_error_response,
    payment_page_create_response,
    payment_page_response,
)
from settings import texts


async def show_yookassa_page(request: web.Request) -> web.Response:
    """Отдаёт страницу оплаты с вебхук-сервера бота."""
    await maybe_sync_runtime_configs()
    async with async_session_maker() as session:
        result = await payment_page_response(session, request.query.get("t", ""))
        await session.commit()
    return web.Response(text=result.body, status=result.status, headers=result.headers, content_type="text/html")


async def create_yookassa_page_payment(request: web.Request) -> web.Response:
    """Создаёт платёж через вебхук-сервер бота."""
    await maybe_sync_runtime_configs()
    raw = bytearray()
    async for chunk in request.content.iter_chunked(MAX_PAGE_REQUEST_BYTES):
        raw.extend(chunk)
        if len(raw) > MAX_PAGE_REQUEST_BYTES:
            result = page_error_response(413, texts.YOOKASSA_PAGE_INVALID_REQUEST, code="invalid_request")
            break
    else:
        async with async_session_maker() as session:
            result = await payment_page_create_response(session, bytes(raw))
            await session.commit()
    return web.json_response(result.body, status=result.status, headers=result.headers)

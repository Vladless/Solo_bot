from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_session
from services.payments.yookassa.page_http import (
    MAX_PAGE_REQUEST_BYTES,
    PAGE_CREATE_PATH,
    PAGE_PATH,
    page_error_response,
    payment_page_create_response,
    payment_page_response,
)
from settings import texts


router = APIRouter(tags=["YooKassaPage"])


@router.get(PAGE_PATH, include_in_schema=False)
async def show_yookassa_page(request: Request, session: Annotated[AsyncSession, Depends(get_session)]):
    """Открывает страницу по подписанной ссылке из бота."""
    result = await payment_page_response(session, request.query_params.get("t", ""))
    return HTMLResponse(result.body, status_code=result.status, headers=result.headers)


@router.post(PAGE_CREATE_PATH, include_in_schema=False)
async def create_yookassa_page_payment(request: Request, session: Annotated[AsyncSession, Depends(get_session)]):
    """Принимает выбор способа оплаты без данных банковской карты."""
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_PAGE_REQUEST_BYTES:
            result = page_error_response(413, texts.YOOKASSA_PAGE_INVALID_REQUEST, code="invalid_request")
            break
    else:
        result = await payment_page_create_response(session, bytes(raw))
    return JSONResponse(result.body, status_code=result.status, headers=result.headers)

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_session, verify_identity_admin
from database.analytics import DOMAINS, StatsCtx


router = APIRouter()


@router.get("")
async def list_domains(identity=Depends(verify_identity_admin)):
    """Доступные кросс-ресурсные отчёты."""
    return {"domains": sorted(DOMAINS.keys())}


@router.get("/{domain}")
async def get_report(
    domain: str,
    days: int = Query(30, ge=1, le=365),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Кросс-ресурсный отчёт: GET /api/analytics/revenue?days=30, /api/analytics/retention и т.д."""
    fn = DOMAINS.get(domain)
    if fn is None:
        raise HTTPException(status_code=404, detail=f"Unknown analytics report: {domain}")
    return await fn(StatsCtx(session, days))

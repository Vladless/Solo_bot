from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_request_actor, get_session, verify_identity_token
from api.v2.schemas.web_public import DailyBonusClaimResponse, DailyBonusStateResponse
from database import identities as idb
from database.web_layout import DAILY_BONUS_BLOCK_TYPES, find_block_locations
from services.daily_bonus import DailyBonusState, claim_daily_bonus, get_daily_bonus_state
from services.errors import ServiceError


router = APIRouter()


async def _resolve_bonus_user(session: AsyncSession, request: Request, identity) -> tuple[int, int | None]:
    actor = get_request_actor(request)
    billing_user_id = actor.billing_user_id if actor and actor.billing_user_id is not None else None
    if billing_user_id is None:
        billing_user_id = await idb.ensure_billing_user_for_identity(session, identity)
    tg_id = actor.telegram_chat_id if actor else None
    return int(billing_user_id), tg_id


def _state_payload(state: DailyBonusState, placement: dict[str, object] | None = None) -> DailyBonusStateResponse:
    return DailyBonusStateResponse(**state.__dict__, **(placement or {}))


async def _bonus_block_placement(session: AsyncSession) -> dict[str, object]:
    """Где админ поставил блок бонуса — кабинет подсвечивает эту вкладку точкой."""
    locations = await find_block_locations(session, list(DAILY_BONUS_BLOCK_TYPES))
    if not locations:
        return {}
    block = locations[0]
    return {
        "page_slug": block.slug,
        "tab_group": block.tab_group,
        "tab_id": block.tab_id,
        "attention_dot": block.data.get("tabAttentionDot") is not False,
        "attention_dot_color": str(block.data.get("attentionDotColor") or ""),
    }


@router.get("/daily/me", response_model=DailyBonusStateResponse)
async def get_my_daily_bonus(
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    """Состояние ежедневного бонуса текущего пользователя."""
    user_id, _ = await _resolve_bonus_user(session, request, identity)
    state = await get_daily_bonus_state(session, user_id)
    return _state_payload(state, await _bonus_block_placement(session))


@router.post("/daily/me/claim", response_model=DailyBonusClaimResponse)
async def claim_my_daily_bonus(
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    """Начисляет ежедневный бонус. Отказ по правилам — ok=false и причина, без ошибки HTTP."""
    from api.ratelimit import enforce_rate_limit

    await enforce_rate_limit(request, session, bucket="daily_bonus_claim", max_per_window=10, window_sec=60)
    user_id, tg_id = await _resolve_bonus_user(session, request, identity)
    try:
        result = await claim_daily_bonus(session, user_id, tg_id, source="web")
    except ServiceError as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=e.message) from e
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail="Не удалось начислить бонус") from e
    return DailyBonusClaimResponse(
        ok=result.ok,
        reason=result.reason,
        amount=result.amount,
        balance=result.balance,
        streak=result.streak,
        state=_state_payload(result.state, await _bonus_block_placement(session)),
    )

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.constants import PAYMENT_SYSTEMS_EXCLUDED
from database.access.resolution import resolve_user_optional
from database.models import Key, Payment


PREDICATE_LABELS = {
    "has_active": "есть активная подписка",
    "hot_lead": "горячий лид",
    "active_count": "активных подписок",
}


def _rule_of(tariff) -> dict | None:
    rule = tariff.get("visibility_rules") if isinstance(tariff, dict) else getattr(tariff, "visibility_rules", None)
    return rule if isinstance(rule, dict) and rule.get("predicate") else None


async def get_user_visibility_state(session: AsyncSession, tg_id: int) -> dict:
    user = await resolve_user_optional(session, tg_id)
    if user is None:
        return {"has_active": False, "active_count": 0, "is_hot_lead": False}

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    active_count = int(
        await session.scalar(
            select(func.count()).select_from(Key).where(Key.user_id == user.id, Key.expiry_time > now_ms)
        )
        or 0
    )
    has_active = active_count > 0

    is_hot_lead = False
    if not has_active and getattr(user, "trial", 0) == 1:
        paid = int(
            await session.scalar(
                select(func.count())
                .select_from(Payment)
                .where(
                    Payment.user_id == user.id,
                    Payment.status == "success",
                    Payment.amount > 0,
                    Payment.payment_system.notin_(PAYMENT_SYSTEMS_EXCLUDED),
                )
            )
            or 0
        )
        is_hot_lead = paid > 0

    return {"has_active": has_active, "active_count": active_count, "is_hot_lead": is_hot_lead}


def tariff_visible(tariff, state: dict) -> bool:
    rule = _rule_of(tariff)
    if rule is None:
        return True
    predicate = rule.get("predicate")
    if predicate == "has_active":
        match = state["has_active"]
    elif predicate == "hot_lead":
        match = state["is_hot_lead"]
    elif predicate == "active_count":
        match = state["active_count"] >= int(rule.get("min_count") or 1)
    else:
        return True
    return match if rule.get("mode") == "only" else not match


async def filter_visible_tariffs(session: AsyncSession, tg_id: int, tariffs: list) -> list:
    if not any(_rule_of(t) for t in tariffs):
        return tariffs
    state = await get_user_visibility_state(session, tg_id)
    return [t for t in tariffs if tariff_visible(t, state)]


async def is_tariff_visible_for(session: AsyncSession, tg_id: int, tariff) -> bool:
    if _rule_of(tariff) is None:
        return True
    state = await get_user_visibility_state(session, tg_id)
    return tariff_visible(tariff, state)


def describe_visibility(rule: dict | None) -> str:
    if not isinstance(rule, dict) or not rule.get("predicate"):
        return "всем"
    predicate = rule.get("predicate")
    if predicate == "active_count":
        label = f"активных подписок ≥ {rule.get('min_count')}"
    else:
        label = PREDICATE_LABELS.get(predicate, predicate)
    prefix = "только: " if rule.get("mode") == "only" else "кроме: "
    return prefix + label

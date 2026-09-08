import random

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from core.settings.bonus_config import DailyBonusRules, resolve_daily_bonus_rules
from database import daily_bonus as db
from database.payments import add_payment
from database.users import get_balance, update_balance
from logger import logger
from services.errors import ValidationError


BONUS_PAYMENT_SYSTEM = "daily_bonus"

REASON_DISABLED = "disabled"
REASON_COOLDOWN = "cooldown"
REASON_LIMIT = "limit"
REASON_SUBSCRIPTION = "subscription"
REASON_ACCOUNT_AGE = "account_age"
REASON_MISCONFIGURED = "misconfigured"


@dataclass(frozen=True)
class DailyBonusState:
    enabled: bool
    can_claim: bool
    reason: str
    mode: str
    amount_next: float
    amount_min: float
    amount_max: float
    ladder: list[float]
    streak: int
    streak_next: int
    cooldown_hours: int
    period_hours: int
    claims_per_period: int
    claims_left: int
    seconds_left: int
    next_available_at: str | None
    claimed_total: float
    last_claim_at: str | None


@dataclass(frozen=True)
class DailyBonusClaimResult:
    ok: bool
    reason: str
    amount: float
    balance: float
    streak: int
    state: DailyBonusState


def next_streak(last_claim_at: datetime | None, streak: int, rules: DailyBonusRules, now: datetime) -> int:
    """Номер дня серии для следующей выдачи: серия рвётся, если перерыв дольше окна удержания."""
    if last_claim_at is None:
        return 1
    if rules.streak_keep_hours <= 0:
        return max(1, streak) + 1
    if now - last_claim_at > timedelta(hours=rules.streak_keep_hours):
        return 1
    return max(1, streak) + 1


def amount_for_streak(rules: DailyBonusRules, streak_day: int) -> float:
    """Сумма бонуса по режиму: фиксированная, случайная из диапазона или по лестнице серии."""
    if rules.mode == "streak" and rules.ladder:
        index = min(max(1, streak_day), len(rules.ladder)) - 1
        return float(rules.ladder[index])
    if rules.mode == "range" and rules.max_amount > 0:
        low = min(rules.min_amount, rules.max_amount)
        high = max(rules.min_amount, rules.max_amount)
        return round(random.uniform(low, high), 2)  # noqa: S311
    return float(rules.amount)


def display_amounts(rules: DailyBonusRules, streak_day: int) -> tuple[float, float, float]:
    """Что показать до выдачи: (ожидаемая сумма, минимум, максимум)."""
    if rules.mode == "streak" and rules.ladder:
        index = min(max(1, streak_day), len(rules.ladder)) - 1
        value = float(rules.ladder[index])
        return value, value, value
    if rules.mode == "range" and rules.max_amount > 0:
        low = min(rules.min_amount, rules.max_amount)
        high = max(rules.min_amount, rules.max_amount)
        return round((low + high) / 2, 2), low, high
    return float(rules.amount), float(rules.amount), float(rules.amount)


def _iso(value: datetime | None) -> str | None:
    return value.replace(microsecond=0).isoformat() + "Z" if value is not None else None


async def get_daily_bonus_state(
    session: AsyncSession,
    user_id: int,
    *,
    now: datetime | None = None,
) -> DailyBonusState:
    """Состояние ежедневного бонуса: доступен ли, сколько дадут и когда следующий."""
    rules = resolve_daily_bonus_rules()
    moment = now or datetime.utcnow()
    last = await db.get_last_claim(session, user_id)
    last_at = last.created_at if last is not None else None
    streak = int(last.streak or 0) if last is not None else 0
    streak_day = next_streak(last_at, streak, rules, moment)
    amount_next, amount_min, amount_max = display_amounts(rules, streak_day)
    claimed_total = await db.total_claimed(session, user_id)

    period_start = moment - timedelta(hours=rules.period_hours)
    claims_in_period = await db.count_claims_since(session, user_id, period_start)
    claims_left = max(0, rules.claims_per_period - claims_in_period)

    available_at: datetime | None = None
    reason = ""
    if last_at is not None:
        available_at = last_at + timedelta(hours=rules.cooldown_hours)
        if available_at > moment:
            reason = REASON_COOLDOWN
    if claims_left <= 0:
        first_in_period = await db.first_claim_since(session, user_id, period_start)
        limit_until = (first_in_period or moment) + timedelta(hours=rules.period_hours)
        if available_at is None or limit_until > available_at:
            available_at = limit_until
        if limit_until > moment and not reason:
            reason = REASON_LIMIT

    if not reason and rules.require_subscription and not await db.has_active_subscription(session, user_id):
        reason = REASON_SUBSCRIPTION
    if not reason and rules.min_account_age_hours > 0:
        created_at = await db.get_user_created_at(session, user_id)
        if created_at is None or moment - created_at < timedelta(hours=rules.min_account_age_hours):
            reason = REASON_ACCOUNT_AGE
    if not reason and amount_max <= 0:
        reason = REASON_MISCONFIGURED
    if not rules.enabled:
        reason = REASON_DISABLED

    seconds_left = 0
    if available_at is not None and available_at > moment:
        seconds_left = int((available_at - moment).total_seconds())

    return DailyBonusState(
        enabled=rules.enabled,
        can_claim=rules.enabled and reason == "",
        reason=reason,
        mode=rules.mode,
        amount_next=amount_next,
        amount_min=amount_min,
        amount_max=amount_max,
        ladder=[float(value) for value in rules.ladder],
        streak=streak,
        streak_next=streak_day,
        cooldown_hours=rules.cooldown_hours,
        period_hours=rules.period_hours,
        claims_per_period=rules.claims_per_period,
        claims_left=claims_left,
        seconds_left=seconds_left,
        next_available_at=_iso(available_at),
        claimed_total=round(claimed_total, 2),
        last_claim_at=_iso(last_at),
    )


async def claim_daily_bonus(
    session: AsyncSession,
    user_id: int,
    tg_id: int | None = None,
    source: str = "web",
) -> DailyBonusClaimResult:
    """Начисляет ежедневный бонус на баланс. Отказ по правилам возвращается как ok=False с причиной."""
    rules = resolve_daily_bonus_rules()
    if not rules.enabled:
        state = await get_daily_bonus_state(session, user_id)
        return DailyBonusClaimResult(False, REASON_DISABLED, 0.0, await get_balance(session, user_id), 0, state)

    await db.lock_user_bonus(session, user_id)
    moment = datetime.utcnow()
    state = await get_daily_bonus_state(session, user_id, now=moment)
    if not state.can_claim:
        return DailyBonusClaimResult(False, state.reason, 0.0, await get_balance(session, user_id), state.streak, state)

    amount = round(amount_for_streak(rules, state.streak_next), 2)
    if amount <= 0:
        raise ValidationError("Сумма бонуса не настроена")

    new_balance = await update_balance(session, int(user_id), amount)
    if new_balance is None:
        raise ValidationError("Не удалось начислить бонус")
    await db.insert_claim(session, user_id, tg_id, amount, state.streak_next, source, moment)
    await add_payment(
        session=session,
        user_id=int(user_id),
        amount=amount,
        payment_system=BONUS_PAYMENT_SYSTEM,
        status="success",
        currency="RUB",
    )
    logger.info(f"[DailyBonus] user_id={user_id} получил {amount} ₽ (серия {state.streak_next}, {source})")

    fresh = await get_daily_bonus_state(session, user_id, now=moment)
    return DailyBonusClaimResult(True, "", amount, float(new_balance), state.streak_next, fresh)

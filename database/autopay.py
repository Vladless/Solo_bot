from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    KassaIAutopayAttempt,
    KassaISubscription,
    OverPayAutopayAttempt,
    OverPaySubscription,
    YooKassaAutopayAttempt,
    YooKassaSubscription,
)
from settings import texts


_PROVIDERS = (
    ("YOOKASSA_AUTOPAY", YooKassaSubscription, YooKassaAutopayAttempt),
    ("KASSAI_AUTOPAY", KassaISubscription, KassaIAutopayAttempt),
    ("OVERPAY_AUTOPAY", OverPaySubscription, OverPayAutopayAttempt),
)
_UNFINISHED = ("prepared", "unknown", "pending", "succeeded", "manual_review")


async def ensure_no_other_autopay(session: AsyncSession, user_id: int, client_id: str, provider: str) -> None:
    """Исключает конкурирующее автопродление под блокировкой владельца."""
    for provider_id, subscription, attempt in _PROVIDERS:
        if provider_id == provider:
            continue
        found = await session.scalar(
            select(subscription.id)
            .where(
                subscription.user_id == user_id,
                subscription.client_id == client_id,
                or_(
                    subscription.is_active.is_(True),
                    select(attempt.id)
                    .where(
                        attempt.subscription_id == subscription.id,
                        attempt.status.in_(_UNFINISHED),
                    )
                    .exists(),
                ),
            )
            .limit(1)
        )
        if found is not None:
            raise ValueError(texts.AUTOPAY_OTHER_PROVIDER_ACTIVE)

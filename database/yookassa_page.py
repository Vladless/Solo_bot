from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Payment, User, YooKassaPageIntent
from database.payments import require_payment_provider


async def delete_expired_page_intents(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Удаляет до пятисот страниц через семь дней после истечения ссылки."""
    cutoff = (now or datetime.utcnow()) - timedelta(days=7)
    obsolete = (
        select(YooKassaPageIntent.id)
        .where(YooKassaPageIntent.expires_at < cutoff)
        .order_by(YooKassaPageIntent.expires_at, YooKassaPageIntent.id)
        .limit(500)
        .with_for_update(skip_locked=True)
    )
    result = await session.execute(
        delete(YooKassaPageIntent)
        .where(YooKassaPageIntent.id.in_(obsolete))
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


async def add_page_intent(
    session: AsyncSession,
    *,
    intent_id: str,
    user_id: int,
    invoice: dict,
    methods: list[str],
    expires_at: datetime,
) -> YooKassaPageIntent:
    """Сохраняет неизменяемые условия страницы оплаты."""
    intent = YooKassaPageIntent(
        id=intent_id,
        user_id=int(user_id),
        invoice=invoice,
        methods=list(methods),
        attempts={},
        expires_at=expires_at,
    )
    session.add(intent)
    await session.flush()
    return intent


async def get_page_intent(
    session: AsyncSession, intent_id: str, *, lock: bool = False
) -> tuple[YooKassaPageIntent, User] | None:
    """Читает счёт с действующим владельцем и необязательной блокировкой."""
    statement = (
        select(YooKassaPageIntent, User)
        .join(User, User.id == YooKassaPageIntent.user_id)
        .where(YooKassaPageIntent.id == intent_id)
        .execution_options(populate_existing=True)
    )
    if lock:
        statement = statement.with_for_update(of=YooKassaPageIntent)
    row = (await session.execute(statement)).first()
    return (row[0], row[1]) if row is not None else None


async def get_page_payment_statuses(session: AsyncSession, user_id: int, payment_ids: list[str]) -> dict[str, str]:
    """Читает окончательные статусы не более четырёх способов оплаты."""
    if not payment_ids:
        return {}
    if len(payment_ids) > 4:
        raise ValueError("Too many payment page attempts")
    rows = (await session.execute(select(Payment).where(Payment.payment_id.in_(payment_ids)))).scalars()
    statuses = {}
    for payment in rows:
        if payment.user_id != int(user_id):
            raise ValueError("Payment page owner differs from invoice owner")
        require_payment_provider(payment, "yookassa")
        statuses[payment.payment_id] = str(payment.status or "")
    return statuses

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from database.db import async_session_maker
from database.keys import get_key_for_checkout
from database.payments import (
    PAYMENT_CHECKOUT_OPERATION,
    PAYMENT_CHECKOUT_SNAPSHOT,
    capture_payment_checkout as capture_checkout_snapshot,
    resolve_payment_creation_owner,
)
from services.clusters import select_cluster
from services.errors import ValidationError
from services.payments.checkout_intent import checkout_key_snapshot


async def capture_payment_checkout(session: AsyncSession | None, owner: int, metadata: dict | None = None) -> dict:
    """Фиксирует корзину и постоянные реквизиты выдачи до создания счёта."""
    if session is None:
        async with async_session_maker() as own_session:
            return await capture_payment_checkout(own_session, owner, metadata)
    owner = await resolve_payment_creation_owner(session, owner)
    captured = await capture_checkout_snapshot(session, owner, metadata)
    captured.pop(PAYMENT_CHECKOUT_OPERATION, None)
    snapshot = captured.get(PAYMENT_CHECKOUT_SNAPSHOT) or {}
    data = snapshot.get("data") or {}
    if snapshot.get("state") in {"waiting_for_renewal_payment", "waiting_for_addons_payment"}:
        key = await get_key_for_checkout(
            session,
            owner,
            client_id=data.get("client_id"),
            email=data.get("email"),
            lock=False,
        )
        if key is None or key.is_frozen:
            raise ValidationError("Подписка для оплаты недоступна")
        captured[PAYMENT_CHECKOUT_OPERATION] = {"key_snapshot": checkout_key_snapshot(key)}
        return captured
    if snapshot.get("state") != "waiting_for_payment" or data.get("payment_flow") == "gift_create":
        return captured
    cluster = await select_cluster(session)
    operation_id = uuid.uuid4()
    captured[PAYMENT_CHECKOUT_OPERATION] = {
        "client_id": str(operation_id),
        "email": f"pay{operation_id.hex[:16]}",
        "cluster": cluster.cluster_name,
    }
    return captured

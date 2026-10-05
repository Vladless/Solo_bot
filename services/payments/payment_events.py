from core.redis_cache import cache_publish
from logger import logger


def payment_events_channel(legacy_user_ref: int) -> str:
    return f"payment_events:user:{int(legacy_user_ref)}"


async def publish_payment_event(
    *,
    legacy_user_ref: int,
    status: str,
    flow: str | None = None,
    amount: float | int | None = None,
) -> None:
    try:
        payload: dict[str, str | float | int] = {"status": str(status)}
        if flow:
            payload["flow"] = str(flow)
        if amount is not None:
            payload["amount"] = float(amount)

        subscribers = await cache_publish(
            payment_events_channel(int(legacy_user_ref)),
            payload,
        )
        logger.info(
            f"[Payments] Event published: user_ref={legacy_user_ref}, status={status}, "
            f"flow={flow}, subscribers={subscribers}"
        )
    except Exception as e:
        logger.warning(f"[Payments] publish_payment_event failed: {e}")

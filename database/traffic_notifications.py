from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.access.resolution import UserId
from database.keys import lock_owned_key_for_operation
from database.models import TrafficNotificationState


@dataclass(frozen=True)
class TrafficMeter:
    resource_id: str
    server_name: str
    used_bytes: int
    limit_bytes: int

    @property
    def remaining_bytes(self) -> int:
        return max(0, self.limit_bytes - self.used_bytes)


@dataclass(frozen=True)
class TrafficNotificationClaim:
    user_id: UserId
    client_id: str
    email: str
    token: str
    level: int
    meter: TrafficMeter
    expected_key: tuple


def key_traffic_snapshot(key) -> tuple:
    """Фиксирует параметры ключа перед запросом к панели."""
    return (
        key.server_id,
        key.tariff_id,
        key.expiry_time,
        key.current_traffic_limit,
        key.selected_traffic_limit,
    )


async def claim_traffic_notification(
    session: AsyncSession,
    *,
    user_id: UserId,
    client_id: str,
    email: str,
    expected_key: tuple,
    meters: list[TrafficMeter],
    levels: tuple[int, ...],
    now_ms: int,
) -> TrafficNotificationClaim | None:
    """Резервирует самое срочное уведомление актуального ключа."""
    key = await lock_owned_key_for_operation(session, user_id, client_id, email)
    if (
        key is None
        or key.is_frozen
        or (key.expiry_time and key.expiry_time <= now_ms)
        or key_traffic_snapshot(key) != expected_key
    ):
        return None
    await session.execute(
        insert(TrafficNotificationState)
        .values(user_id=int(user_id), client_id=client_id, meters={}, claim_until=0, claim_targets={})
        .on_conflict_do_nothing(index_elements=["user_id", "client_id"])
    )
    state = await session.scalar(
        select(TrafficNotificationState)
        .where(TrafficNotificationState.user_id == int(user_id), TrafficNotificationState.client_id == client_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if int(state.claim_until or 0) > now_ms:
        return None

    stored = dict(state.meters or {})
    candidates = []
    targets = {}
    for meter in meters:
        if (
            isinstance(meter.used_bytes, bool)
            or not isinstance(meter.used_bytes, int)
            or meter.used_bytes < 0
            or isinstance(meter.limit_bytes, bool)
            or not isinstance(meter.limit_bytes, int)
            or meter.limit_bytes <= 0
        ):
            continue
        previous = dict(stored.get(meter.resource_id) or {})
        generation = int(previous.get("generation", 0))
        sent = set(previous.get("sent", []))
        if previous and (
            meter.used_bytes < int(previous.get("used", 0)) or meter.limit_bytes > int(previous.get("limit", 0))
        ):
            generation += 1
            sent.clear()
        crossed = [
            level for level in levels if level not in sent and meter.remaining_bytes * 100 <= meter.limit_bytes * level
        ]
        stored[meter.resource_id] = {
            "used": meter.used_bytes,
            "limit": meter.limit_bytes,
            "generation": generation,
            "sent": sorted(sent),
        }
        if crossed:
            candidates.append((min(crossed), meter))
            targets[meter.resource_id] = {"generation": generation, "levels": crossed}

    state.meters = stored
    state.claim_token = None
    state.claim_targets = {}
    state.claim_until = 0
    if not candidates:
        await session.flush()
        return None
    level, meter = min(
        candidates,
        key=lambda candidate: (
            candidate[0],
            candidate[1].remaining_bytes / candidate[1].limit_bytes,
            candidate[1].resource_id,
        ),
    )
    token = str(uuid4())
    state.claim_token = token
    state.claim_targets = targets
    state.claim_until = now_ms + 300_000
    await session.flush()
    return TrafficNotificationClaim(user_id, client_id, email, token, level, meter, expected_key)


async def lock_traffic_notification_claim(session: AsyncSession, claim: TrafficNotificationClaim) -> bool:
    """Проверяет ключ и резервацию непосредственно перед доставкой."""
    key = await lock_owned_key_for_operation(session, claim.user_id, claim.client_id, claim.email)
    if key is None or key.is_frozen or key_traffic_snapshot(key) != claim.expected_key:
        return False
    return (
        await session.scalar(
            select(TrafficNotificationState.claim_token)
            .where(
                TrafficNotificationState.user_id == int(claim.user_id),
                TrafficNotificationState.client_id == claim.client_id,
                TrafficNotificationState.claim_token == claim.token,
            )
            .with_for_update()
        )
        == claim.token
    )


async def complete_traffic_notification(
    session: AsyncSession,
    claim: TrafficNotificationClaim,
    *,
    delivered: bool,
    now_ms: int,
) -> bool:
    """Завершает отправку только собственной действующей резервации."""
    key = await lock_owned_key_for_operation(session, claim.user_id, claim.client_id, claim.email)
    if key is None or key.is_frozen:
        return False
    state = await session.scalar(
        select(TrafficNotificationState)
        .where(
            TrafficNotificationState.user_id == int(claim.user_id),
            TrafficNotificationState.client_id == claim.client_id,
            TrafficNotificationState.claim_token == claim.token,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if state is None:
        return False
    stored = dict(state.meters or {})
    if delivered:
        for resource_id, target in (state.claim_targets or {}).items():
            meter = dict(stored.get(resource_id) or {})
            if meter.get("generation") == target.get("generation"):
                meter["sent"] = sorted(set(meter.get("sent", [])) | set(target.get("levels", [])))
                stored[resource_id] = meter
        state.meters = stored
    state.claim_token = None
    state.claim_targets = {}
    state.claim_until = 0 if delivered else now_ms + 60_000
    await session.flush()
    return True


async def reset_traffic_notification_state(
    session: AsyncSession,
    user_id: UserId,
    client_id: str,
    resource_ids: set[str] | None = None,
) -> None:
    """Сбрасывает пороги после подтверждённого сброса счётчика."""
    if resource_ids is not None and not resource_ids:
        return
    stmt = select(TrafficNotificationState).where(
        TrafficNotificationState.user_id == int(user_id), TrafficNotificationState.client_id == client_id
    )
    state = await session.scalar(stmt.with_for_update().execution_options(populate_existing=True))
    if state is None:
        return
    if resource_ids is None:
        await session.execute(
            delete(TrafficNotificationState).where(
                TrafficNotificationState.user_id == int(user_id), TrafficNotificationState.client_id == client_id
            )
        )
        return
    state.meters = {key: value for key, value in (state.meters or {}).items() if key not in resource_ids}
    state.claim_token = None
    state.claim_until = 0
    state.claim_targets = {}
    await session.flush()

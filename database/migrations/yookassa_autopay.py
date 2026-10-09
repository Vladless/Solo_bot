import uuid

from collections import defaultdict
from datetime import datetime

from sqlalchemy import and_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from database.models import (
    Key,
    Payment,
    Setting,
    Tariff,
    User,
    YooKassaAutopayAttempt,
    YooKassaLog,
    YooKassaPayment,
    YooKassaSavedCard,
    YooKassaSubscription,
)


_TG_BACKFILL_MARKER = "YOOKASSA_AUTOPAY_LEGACY_UID_BACKFILL_DONE"


async def migrate_yookassa_autopay(conn: AsyncConnection) -> dict[str, int]:
    """Привязывает старые автоплатежи к подтверждённым владельцам."""
    if conn.dialect.name != "postgresql":
        return {}
    await conn.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('solo:yookassa-autopay:migration',0))"))
    first_backfill = await conn.scalar(select(Setting.key).where(Setting.key == _TG_BACKFILL_MARKER)) is None
    users = list((await conn.execute(select(User.id, User.tg_id, User.created_at))).all())
    valid_uids = {row.id for row in users}
    tg_owners = {row.tg_id: row.id for row in users if row.tg_id is not None and row.tg_id != 0}
    user_created = {row.id: row.created_at for row in users}
    cards = list(
        (
            await conn.execute(
                select(
                    YooKassaSavedCard.id,
                    YooKassaSavedCard.user_id,
                    YooKassaSavedCard.tg_id,
                    YooKassaSavedCard.created_at,
                )
            )
        ).all()
    )
    subscriptions = list(
        (
            await conn.execute(
                select(
                    YooKassaSubscription.id,
                    YooKassaSubscription.user_id,
                    YooKassaSubscription.tg_id,
                    YooKassaSubscription.client_id,
                    YooKassaSubscription.card_id,
                    YooKassaSubscription.created_at,
                )
            )
        ).all()
    )
    payments = list(
        (
            await conn.execute(
                select(
                    YooKassaPayment.id,
                    YooKassaPayment.user_id,
                    YooKassaPayment.tg_id,
                    YooKassaPayment.payment_id,
                    YooKassaPayment.subscription_id,
                    YooKassaPayment.payment_method_id,
                    YooKassaPayment.created_at,
                )
            )
        ).all()
    )
    graph, proof, rows = defaultdict(set), defaultdict(set), {}
    for kind, model, records in (
        ("card", YooKassaSavedCard, cards),
        ("subscription", YooKassaSubscription, subscriptions),
        ("payment", YooKassaPayment, payments),
    ):
        for row in records:
            node = (kind, row.id)
            graph[node]
            rows[node] = (model, row)
            if row.user_id in valid_uids:
                proof[node].add(row.user_id)

    key_proof = defaultdict(set)
    for client_id, uid in (
        await conn.execute(
            select(Key.client_id, Key.user_id).join(
                YooKassaSubscription, YooKassaSubscription.client_id == Key.client_id
            )
        )
    ).all():
        if uid in valid_uids:
            key_proof[client_id].add(uid)
    payment_proof = defaultdict(set)
    for pid, uid in (
        await conn.execute(
            select(Payment.payment_id, Payment.user_id).join(
                YooKassaPayment, YooKassaPayment.payment_id == Payment.payment_id
            )
        )
    ).all():
        if uid in valid_uids:
            payment_proof[pid].add(uid)

    def connect(left, right):
        if right in rows:
            graph[left].add(right)
            graph[right].add(left)

    for row in subscriptions:
        node = ("subscription", row.id)
        proof[node].update(key_proof[row.client_id])
        connect(node, ("card", row.card_id))
    for row in payments:
        node = ("payment", row.id)
        proof[node].update(payment_proof[row.payment_id])
        connect(node, ("subscription", row.subscription_id))
        connect(node, ("card", row.payment_method_id))

    report = {"resolved": 0, "unresolved": 0, "conflicts": 0}
    visited = set()
    for start in graph:
        if start in visited:
            continue
        component, todo = set(), [start]
        while todo:
            node = todo.pop()
            if node in component:
                continue
            component.add(node)
            todo.extend(graph[node] - component)
        visited.update(component)
        candidates = set().union(*(proof[node] for node in component))
        if not candidates and first_backfill and any(node[0] == "card" for node in component):
            candidates = {
                tg_owners[row.tg_id] for node in component for _model, row in (rows[node],) if row.tg_id in tg_owners
            }
            if any(
                row.tg_id in tg_owners
                and (
                    row.created_at is None
                    or user_created[tg_owners[row.tg_id]] is None
                    or user_created[tg_owners[row.tg_id]] > row.created_at
                )
                for node in component
                for _model, row in (rows[node],)
            ):
                candidates = set()
        if len(candidates) != 1:
            report["conflicts" if len(candidates) > 1 else "unresolved"] += sum(
                rows[node][1].user_id is None for node in component
            )
            continue
        uid = candidates.pop()
        for node in component:
            model, row = rows[node]
            if row.user_id is None:
                result = await conn.execute(
                    update(model).where(model.id == row.id, model.user_id.is_(None)).values(user_id=uid)
                )
                report["resolved"] += result.rowcount or 0
    if first_backfill:
        for row in (
            await conn.execute(
                select(YooKassaLog.id, YooKassaLog.tg_id, YooKassaLog.created_at).where(YooKassaLog.user_id.is_(None))
            )
        ).all():
            if (
                row.tg_id in tg_owners
                and row.created_at is not None
                and user_created[tg_owners[row.tg_id]] is not None
                and user_created[tg_owners[row.tg_id]] <= row.created_at
            ):
                await conn.execute(
                    update(YooKassaLog).where(YooKassaLog.id == row.id).values(user_id=tg_owners[row.tg_id])
                )
        await conn.execute(
            insert(Setting)
            .values(
                key=_TG_BACKFILL_MARKER, value=True, description="Legacy recurring payment TG ownership considered once"
            )
            .on_conflict_do_nothing(index_elements=["key"])
        )

    report["period_backfilled"] = 0
    for sub_id, days in (
        await conn.execute(
            select(YooKassaSubscription.id, Tariff.duration_days)
            .join(
                Key,
                and_(
                    Key.user_id == YooKassaSubscription.user_id,
                    Key.client_id == YooKassaSubscription.client_id,
                    Key.tariff_id == YooKassaSubscription.tariff_id,
                ),
            )
            .join(Tariff, Tariff.id == YooKassaSubscription.tariff_id)
            .where(YooKassaSubscription.period_days.is_(None), Tariff.duration_days > 0)
        )
    ).all():
        await conn.execute(
            update(YooKassaSubscription).where(YooKassaSubscription.id == sub_id).values(period_days=days)
        )
        report["period_backfilled"] += 1

    known_pids = set(
        (
            await conn.execute(
                select(YooKassaPayment.user_id, YooKassaPayment.payment_id).where(YooKassaPayment.user_id.is_not(None))
            )
        ).all()
    )
    known_pids.update(
        (
            await conn.execute(
                select(YooKassaAutopayAttempt.user_id, YooKassaAutopayAttempt.payment_id).where(
                    YooKassaAutopayAttempt.payment_id.is_not(None), YooKassaAutopayAttempt.user_id.is_not(None)
                )
            )
        ).all()
    )
    known_pids.update(
        (
            await conn.execute(
                select(Payment.user_id, Payment.payment_id).where(
                    Payment.user_id.is_not(None),
                    Payment.payment_id.is_not(None),
                    Payment.status.in_(("success", "refunded", "chargebacked", "canceled", "cancelled", "failed")),
                )
            )
        ).all()
    )
    attempted_subs = set(
        (
            await conn.execute(
                select(YooKassaAutopayAttempt.subscription_id).where(
                    YooKassaAutopayAttempt.subscription_id.is_not(None)
                )
            )
        ).scalars()
    )
    report["legacy_uncertain"] = 0
    for row in (
        await conn.execute(select(YooKassaSubscription.__table__).where(YooKassaSubscription.user_id.is_not(None)))
    ).all():
        if row.id in attempted_subs:
            continue
        old_pid = row.last_payment_id
        if not ((old_pid and (row.user_id, old_pid) not in known_pids) or (not old_pid and row.is_processing)):
            continue
        idem = f"autopay-{row.id}-{row.retry_count or 0}"
        aid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"solo:yookassa-autopay:legacy:{row.id}:{old_pid}:{idem}"))
        provider_pid = old_pid if old_pid and not old_pid.startswith("autopay_") else None
        intent = {
            "kind": "legacy_unknown",
            "legacy": True,
            "legacy_unknown": True,
            "legacy_subscription_id": row.id,
            "legacy_last_payment_id": old_pid,
            "legacy_idempotency_key": idem,
            "original_user_id": row.user_id,
            "legacy_tg_id": row.tg_id,
            "client_id": row.client_id,
            "card_id": row.card_id,
            "legacy_amount": row.amount,
            "reserved_balance": 0,
            "legacy_last_attempt_at": row.last_attempt_at.isoformat() if row.last_attempt_at else None,
        }
        result = await conn.execute(
            insert(YooKassaAutopayAttempt)
            .values(
                id=aid,
                subscription_id=row.id,
                user_id=row.user_id,
                idempotency_key=idem,
                payload={"legacy_request_unavailable": True},
                intent=intent,
                status="manual_review",
                payment_id=provider_pid,
                consent_canceled=not bool(row.is_active),
            )
            .on_conflict_do_nothing()
        )
        report["legacy_uncertain"] += result.rowcount or 0

    active_groups = defaultdict(list)
    for row in (
        await conn.execute(
            select(
                YooKassaSubscription.id,
                YooKassaSubscription.user_id,
                YooKassaSubscription.client_id,
                YooKassaSubscription.card_id,
                YooKassaSubscription.amount,
                YooKassaSubscription.tariff_id,
                YooKassaSubscription.created_at,
                YooKassaSubscription.period_days,
                YooKassaSubscription.accepted_gross_amount,
            ).where(YooKassaSubscription.user_id.is_not(None), YooKassaSubscription.is_active.is_(True))
        )
    ).all():
        active_groups[(row.user_id, row.client_id)].append(row)
    report["duplicate_disabled"] = 0
    for records in active_groups.values():
        if len(records) < 2:
            continue
        same_terms = (
            len({
                (row.card_id, row.amount, row.tariff_id, row.period_days, row.accepted_gross_amount) for row in records
            })
            == 1
        )
        keep = (
            max(records, key=lambda row: (row.created_at is not None, row.created_at, row.id)).id
            if same_terms
            else None
        )
        ids = [row.id for row in records if row.id != keep]
        await conn.execute(
            update(YooKassaSubscription)
            .where(YooKassaSubscription.id.in_(ids))
            .values(is_active=False, canceled_at=datetime.utcnow())
        )
        report["duplicate_disabled"] += len(ids)
    await conn.execute(
        text("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_yookassa_autopay_active_owner_client
        ON yookassa_autopay_subscriptions(user_id,client_id)
        WHERE user_id IS NOT NULL AND is_active IS TRUE
    """)
    )

    await conn.execute(
        text("""
        CREATE OR REPLACE FUNCTION yookassa_autopay_frozen_attempt() RETURNS trigger AS $$
        BEGIN
            IF NEW.payload IS DISTINCT FROM OLD.payload OR NEW.intent IS DISTINCT FROM OLD.intent
               OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
               OR (OLD.payment_id IS NOT NULL AND NEW.payment_id IS DISTINCT FROM OLD.payment_id) THEN
                RAISE EXCEPTION 'Recurring payment request and provider identity are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    )
    await conn.execute(
        text("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_trigger
                WHERE tgrelid='yookassa_autopay_attempts'::regclass AND tgname='yookassa_autopay_frozen_attempt') THEN
                CREATE TRIGGER yookassa_autopay_frozen_attempt BEFORE UPDATE ON yookassa_autopay_attempts
                FOR EACH ROW EXECUTE FUNCTION yookassa_autopay_frozen_attempt();
            END IF;
        END $$
    """)
    )
    return report

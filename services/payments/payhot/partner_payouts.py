import asyncio
import hashlib
import json

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from sqlalchemy import text

from core.settings import payhot_config as config
from database import partners as pdb
from logger import logger
from services.payments.payhot.payout_api import (
    PayoutError,
    create_payout,
    get_payout,
    normalize_recipient,
    validate_payout,
)
from settings import texts


def payouts_enabled() -> bool:
    """Возвращает переключатель новых партнёрских выплат."""
    return config.PARTNER_PAYHOT_PAYOUTS_ENABLED


def _now() -> datetime:
    """Возвращает UTC для полей очереди без часового пояса."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _scope() -> str:
    """Привязывает очередь к неизменному подключению PayHOT."""
    return hashlib.sha256((config.PAYHOT_API_BASE_URL + "\0" + config.PAYHOT_API_KEY).encode()).hexdigest()


def _minor(value) -> str:
    """Переводит точную сумму получателю в копейки."""
    try:
        amount = Decimal(str(value))
        if (
            not amount.is_finite()
            or amount <= 0
            or abs(amount - amount.quantize(Decimal("0.01"))) > Decimal("0.00000001")
        ):
            raise ValueError
        minor = int(amount.quantize(Decimal("0.01")) * 100)
        if not 0 < minor <= 9223372036854775807:
            raise ValueError
        return str(minor)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(texts.PARTNER_PAYHOT_AMOUNT_INVALID) from exc


async def approve_payout(session, payout_id: int) -> dict | None:
    """Одобряет вручную либо сохраняет выплату в устойчивой очереди."""
    row = await pdb.get_partner_payout(session, payout_id)
    if not row or row["status"] != "pending" or row["owner_id"] is None:
        return None
    method = row["payout_method"]
    if not payouts_enabled() or method not in {"card", "sbp"}:
        if await pdb.decide_partner_payout(session, payout_id, True):
            return await pdb.get_partner_payout(session, payout_id)
        return None
    from modules.partner_program import settings as partner_settings

    if not getattr(partner_settings, "ENABLE_PAYOUT_" + method.upper(), False):
        raise ValueError(texts.PARTNER_PAYHOT_METHOD_DISABLED)
    if not config.PAYHOT_API_KEY or config.PAYHOT_API_KEY.startswith("phk_test_"):
        raise ValueError(texts.PARTNER_PAYHOT_CONFIG_REQUIRED)
    if not await pdb.partner_payout_schema_available(session):
        raise ValueError(texts.PARTNER_PAYHOT_SCHEMA_REQUIRED)
    owner = await pdb.get_partner_profile(session, row["owner_id"], lock=True)
    row = await pdb.get_partner_payout(session, payout_id)
    if not owner or not row or row["status"] != "pending" or row["owner_id"] != owner["id"]:
        return None
    try:
        saved = json.loads(row.get("recipient_data") or "null")
        recipient = normalize_recipient(method, saved)
    except (ValueError, TypeError) as exc:
        raise ValueError(texts.PARTNER_PAYHOT_REQUISITES_REQUIRED) from exc
    recipient["payeer_identifier"] = f"partner:{owner['id']}"
    reference = "partner_" + uuid4().hex
    payload = {
        "merchant_payout_reference": reference,
        "amount_minor": _minor(row["amount"]),
        "currency": "RUB",
        "recipient": recipient,
    }
    result = await session.execute(
        text(
            "UPDATE payout_requests SET status='processing', provider='payhot', provider_reference=:reference, "
            "provider_key=:key, provider_payload=:payload, provider_scope=:scope, provider_status='queued', "
            "provider_attempts=0, provider_next_attempt_at=:now WHERE id=:id AND status='pending' "
            "AND (user_id=:owner OR (user_id IS NULL AND tg_id=:tg)) RETURNING id"
        ),
        {
            "id": payout_id,
            "owner": owner["id"],
            "tg": owner["tg_id"],
            "reference": reference,
            "key": "partner-payout-" + uuid4().hex,
            "payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            "scope": _scope(),
            "now": _now(),
        },
    )
    if result.scalar() is None:
        return None
    return await pdb.get_partner_payout(session, payout_id)


async def _finish(session, row: dict, payout: dict) -> bool:
    """Применяет подтверждённый итог с однократным возвратом резерва."""
    payload = json.loads(row["provider_payload"])
    validate_payout(payout, payload, row.get("provider_id"))
    if row["provider_scope"] != _scope():
        raise PayoutError(texts.PARTNER_PAYHOT_PROVIDER_ERROR, code="merchant_scope_changed", unknown=True)
    owner = await pdb.get_partner_profile(session, row["owner_id"], lock=True) if row["owner_id"] else None
    current = await pdb.get_partner_payout(session, row["id"])
    if not current or current["status"] != "processing":
        return False
    if current["owner_id"] != row["owner_id"]:
        raise PayoutError(texts.PARTNER_PAYHOT_PROVIDER_ERROR, code="owner_changed", unknown=True)
    status = payout["status"]
    terminal = status in {"succeeded", "failed", "cancelled"}
    if status in {"failed", "cancelled"} and not owner:
        raise PayoutError(texts.PARTNER_PAYHOT_PROVIDER_ERROR, code="refund_owner_missing", unknown=True)
    result = await session.execute(
        text(
            "UPDATE payout_requests SET provider_id=:provider_id, provider_status=:provider_status, "
            "status=:status, provider_lease_until=NULL, provider_next_attempt_at=:next, provider_error=NULL "
            "WHERE id=:id AND status='processing' AND provider='payhot' "
            "AND (provider_id IS NULL OR provider_id=:provider_id) RETURNING COALESCE(reserved_amount,amount)"
        ),
        {
            "id": row["id"],
            "provider_id": payout["id"],
            "provider_status": status,
            "status": "approved" if status == "succeeded" else "rejected" if terminal else "processing",
            "next": _now() + timedelta(seconds=60),
        },
    )
    reserve = result.scalar()
    if reserve is None:
        return False
    if status in {"failed", "cancelled"}:
        await session.execute(
            text("UPDATE users SET partner_balance=COALESCE(partner_balance,0)+:amount WHERE id=:id"),
            {"id": owner["id"], "amount": float(reserve)},
        )
    return terminal


async def verify_payout(session, payout_id: int, provider_payout_id: str | None = None) -> dict | None:
    """Сверяет операцию из кабинета без создания повторной выплаты."""
    if not await pdb.partner_payout_schema_available(session):
        raise ValueError(texts.PARTNER_PAYHOT_SCHEMA_REQUIRED)
    row = await pdb.get_partner_payout(session, payout_id)
    if not row or row.get("provider") != "payhot":
        return None
    if row["status"] != "processing":
        return row
    provider_id = provider_payout_id or row.get("provider_id")
    if not provider_id:
        raise ValueError(texts.PARTNER_PAYHOT_ID_REQUIRED)
    payout = await get_payout(str(provider_id))
    await _finish(session, row, payout)
    return await pdb.get_partner_payout(session, payout_id)


async def reconcile_webhook(payout_id: str, sessionmaker) -> bool:
    """Сверяет выплату по API и восстанавливает потерянный ответ создания."""
    payout = await get_payout(payout_id)
    async with sessionmaker() as session:
        if not await pdb.partner_payout_schema_available(session):
            return True
        found = (
            await session.execute(
                text("SELECT id FROM payout_requests WHERE provider='payhot' AND provider_reference=:reference"),
                {"reference": payout.get("merchant_payout_reference")},
            )
        ).scalar()
        if found is None:
            return True
        row = await pdb.get_partner_payout(session, found)
        if row["status"] != "processing":
            return True
        await _finish(session, row, payout)
        await session.commit()
    return True


async def process_payout(payout_id: int, sessionmaker) -> None:
    """Отправляет сохранённый запрос или проверяет ранее принятую выплату."""
    now = _now()
    async with sessionmaker() as session:
        row = await pdb.get_partner_payout(session, payout_id)
        if not row or row["status"] != "processing" or row.get("provider") != "payhot":
            return
        if row["provider_scope"] != _scope() or not config.PAYHOT_API_KEY:
            await session.execute(
                text(
                    "UPDATE payout_requests SET provider_error='merchant_scope_changed', provider_next_attempt_at=:next WHERE id=:id AND status='processing'"
                ),
                {"id": payout_id, "next": now + timedelta(hours=1)},
            )
            await session.commit()
            return
        attempts = row.get("provider_attempts") or 0
        if not row.get("provider_id"):
            if not payouts_enabled() and attempts == 0:
                return
            started = row.get("provider_started_at")
            if isinstance(started, str):
                started = datetime.fromisoformat(started)
            if attempts >= 3 or started and now - started > timedelta(minutes=5):
                await session.execute(
                    text(
                        "UPDATE payout_requests SET provider_error='manual_review', provider_next_attempt_at=:next WHERE id=:id AND status='processing'"
                    ),
                    {"id": payout_id, "next": now + timedelta(hours=1)},
                )
                await session.commit()
                return
        claimed = (
            await session.execute(
                text(
                    "UPDATE payout_requests SET provider_lease_until=:lease, provider_next_attempt_at=:next, "
                    "provider_attempts=COALESCE(provider_attempts,0)+:increment, "
                    "provider_started_at=COALESCE(provider_started_at,:now) WHERE id=:id AND status='processing' "
                    "AND (provider_lease_until IS NULL OR provider_lease_until<:now) "
                    "AND (provider_next_attempt_at IS NULL OR provider_next_attempt_at<=:now) RETURNING id"
                ),
                {
                    "id": payout_id,
                    "now": now,
                    "lease": now + timedelta(seconds=max(config.PAYHOT_REQUEST_TIMEOUT * 2, 90)),
                    "next": now + timedelta(seconds=60),
                    "increment": 0 if row.get("provider_id") else 1,
                },
            )
        ).scalar()
        if claimed is None:
            return
        await session.commit()
    try:
        payload = json.loads(row["provider_payload"])
        payout = (
            await get_payout(row["provider_id"])
            if row.get("provider_id")
            else await create_payout(payload, row["provider_key"])
        )
        validate_payout(payout, payload, row.get("provider_id"))
        if not row.get("provider_id"):
            async with sessionmaker() as session:
                await session.execute(
                    text(
                        "UPDATE payout_requests SET provider_id=:provider_id WHERE id=:id AND status='processing' AND provider='payhot' AND (provider_id IS NULL OR provider_id=:provider_id)"
                    ),
                    {"id": payout_id, "provider_id": payout["id"]},
                )
                await session.commit()
            payout = await get_payout(payout["id"])
        async with sessionmaker() as session:
            await _finish(session, await pdb.get_partner_payout(session, payout_id), payout)
            await session.commit()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("[PayHOT Payout] Заявка {} ожидает сверки: {}", payout_id, type(exc).__name__)
        async with sessionmaker() as session:
            code = getattr(exc, "code", "verification_pending")
            if code not in {"refund_owner_missing", "merchant_scope_changed"}:
                code = (
                    "manual_review"
                    if isinstance(exc, PayoutError) and not exc.retryable and row.get("provider_id")
                    else "verification_pending"
                )
            await session.execute(
                text(
                    "UPDATE payout_requests SET provider_error=:error, provider_lease_until=NULL, provider_next_attempt_at=:next WHERE id=:id AND status='processing'"
                ),
                {"id": payout_id, "error": code, "next": _now() + timedelta(seconds=60)},
            )
            await session.commit()


async def _notify_payout(sessionmaker, bot, payout_id: int) -> None:
    """Сообщает владельцу подтверждённый итог выплаты."""
    from html import escape

    async with sessionmaker() as session:
        row = await pdb.get_partner_payout(session, payout_id)
        if not row or row.get("provider_notified_at") or row["status"] not in {"approved", "rejected"}:
            return
        owner_id = row.get("owner_id")
        if owner_id:
            await pdb.get_partner_profile(session, owner_id, lock=True)
        dialect = await session.run_sync(lambda sync: sync.get_bind().dialect.name)
        statement = (
            text(
                "SELECT id FROM payout_requests WHERE id=:id AND provider_notified_at IS NULL AND status IN ('approved','rejected') FOR UPDATE SKIP LOCKED"
            )
            if dialect == "postgresql"
            else text(
                "SELECT id FROM payout_requests WHERE id=:id AND provider_notified_at IS NULL AND status IN ('approved','rejected')"
            )
        )
        claimed = (await session.execute(statement, {"id": payout_id})).scalar()
        if claimed is None:
            return
        row = await pdb.get_partner_payout(session, payout_id)
        if row.get("owner_id") != owner_id:
            return
        tg_id = row.get("owner_tg_id")
        from database.access.resolution import UserId
        from database.web_notifications import notify_web
        from handlers.admin.sender.sender_utils import is_telegram_chat_id

        message = texts.PARTNER_PAYHOT_SUCCEEDED if row["status"] == "approved" else texts.PARTNER_PAYHOT_FAILED
        amount = row["amount"] if row["status"] == "approved" else row.get("reserved_amount") or row["amount"]
        message = message.format(payout_id=row["id"], amount=escape(f"{amount:.2f}"))
        if is_telegram_chat_id(tg_id):
            try:
                await bot.send_message(tg_id, message)
            except Exception as exc:
                logger.warning("[PayHOT Payout] Telegram уведомление {}: {}", payout_id, type(exc).__name__)
        if row.get("owner_id"):
            await notify_web(
                session,
                user_ref=UserId(row["owner_id"]),
                type="system",
                title=texts.PARTNER_PAYHOT_NOTIFY_TITLE,
                message=message,
                data={"payout_id": row["id"]},
            )
        await session.execute(
            text("UPDATE payout_requests SET provider_notified_at=:now WHERE id=:id AND provider_notified_at IS NULL"),
            {"id": payout_id, "now": _now()},
        )
        await session.commit()
    try:
        from modules.partner_program.cache import invalidate_admin_stats, invalidate_user

        await invalidate_admin_stats()
        if tg_id:
            await invalidate_user(tg_id)
    except Exception as exc:
        logger.warning("[PayHOT Payout] Сброс кеша {}: {}", payout_id, type(exc).__name__)


async def _alert_review(sessionmaker, payout_id: int) -> None:
    """Однократно сообщает администраторам о необходимой сверке."""
    from services.admin_alert import send_admin_alert

    async with sessionmaker() as session:
        row = await pdb.get_partner_payout(session, payout_id)
        if not row or row.get("provider_alerted_at") or not row.get("provider_error"):
            return
        reason = row["provider_error"]
        if reason not in {"manual_review", "merchant_scope_changed", "refund_owner_missing"}:
            return
        sent = await send_admin_alert(
            texts.PARTNER_PAYHOT_REVIEW_ALERT.format(
                payout_id=payout_id, reason=reason, reference=row["provider_reference"]
            )
        )
        if sent:
            await session.execute(
                text("UPDATE payout_requests SET provider_alerted_at=:now WHERE id=:id"),
                {"id": payout_id, "now": _now()},
            )
            await session.commit()


async def payout_loop(bot, sessionmaker) -> None:
    """Продолжает сверку принятых выплат независимо от переключателя."""
    while True:
        try:
            async with sessionmaker() as session:
                if await pdb.partner_payout_schema_available(session):
                    rows = (
                        (
                            await session.execute(
                                text(
                                    "SELECT id FROM payout_requests WHERE provider='payhot' AND status='processing' AND (:enabled OR provider_id IS NOT NULL OR COALESCE(provider_attempts,0)>0) AND (provider_next_attempt_at IS NULL OR provider_next_attempt_at<=:now) ORDER BY provider_next_attempt_at NULLS FIRST, id LIMIT 50"
                                ),
                                {"now": _now(), "enabled": payouts_enabled()},
                            )
                        )
                        .scalars()
                        .all()
                    )
                    notifications = (
                        (
                            await session.execute(
                                text(
                                    "SELECT id FROM payout_requests WHERE provider='payhot' AND status IN ('approved','rejected') AND provider_notified_at IS NULL ORDER BY id LIMIT 50"
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                else:
                    rows, notifications = [], []
            for payout_id in rows:
                await process_payout(payout_id, sessionmaker)
                await _alert_review(sessionmaker, payout_id)
            for payout_id in notifications:
                try:
                    await _notify_payout(sessionmaker, bot, payout_id)
                except Exception as exc:
                    logger.warning("[PayHOT Payout] Уведомление {}: {}", payout_id, type(exc).__name__)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("[PayHOT Payout] Ошибка очереди: {}", type(exc).__name__)
        await asyncio.sleep(15)

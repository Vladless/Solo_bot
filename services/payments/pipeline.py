from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from math import isfinite
from typing import TYPE_CHECKING

from core.client_origin import normalize_origin, set_client_origin
from database import (
    add_payment,
    async_session_maker,
    get_payment_by_payment_id,
    invalidate_payment_cache,
    lock_payment_for_processing,
    update_balance,
    update_payment_status,
)
from database.access.resolution import TelegramId, UserId, resolve_user_optional
from database.cache_purge import flush_purges
from database.keys import invalidate_keys_list
from database.payments import (
    PAYMENT_CHECKOUT_OPERATION,
    canonical_payment_provider,
    payment_checkout_matches,
    require_payment_provider,
    resolve_unrecorded_payment_owner,
    yookassa_refunded_net,
)
from logger import logger
from services.payments.yookassa.amounts import saved_yookassa_amounts


if TYPE_CHECKING:
    pass


async def send_payment_success_notification(*args, **kwargs) -> None:
    from handlers.payments.utils import send_payment_success_notification as notify

    await notify(*args, **kwargs)


@dataclass
class ParsedPayment:
    """Данные платежа из уведомления провайдера."""

    payment_id: str
    tg_id: int | None
    amount: float
    currency: str = "RUB"
    metadata: dict | None = field(default=None)


@dataclass
class PipelineResult:
    """Результат обработки платежа для ответа провайдеру."""

    ok: bool
    already_processed: bool = False
    error: str | None = None


def _adopt_payment_origin(*sources: dict | None) -> None:
    """Восстанавливает канал оплаты из сохранённых метаданных."""
    for source in sources:
        origin = normalize_origin((source or {}).get("origin"))
        if origin:
            set_client_origin(origin)
            return


def _resolve_client(parsed: ParsedPayment, *, user_id: int | None = None, fallback: int | None = None) -> int | None:
    """Определяет владельца оплаты по сохранённой записи."""
    if user_id is not None:
        return UserId(user_id)
    if fallback is not None:
        return fallback if isinstance(fallback, UserId | TelegramId) else TelegramId(fallback)
    return parsed.tg_id


async def process_success_payment(
    provider: str,
    parsed: ParsedPayment,
    *,
    metadata_patch: dict | None = None,
    credit_amount_override: float | None = None,
    update_currency: str | None = None,
    update_original_amount: float | None = None,
    completion: Callable[..., Awaitable[None]] | None = None,
    use_temporary_checkout: bool = True,
) -> PipelineResult:
    """Подтверждает платёж, зачисляет баланс и завершает покупку."""
    try:
        credit_amount = float(credit_amount_override if credit_amount_override is not None else parsed.amount)
        if not isfinite(credit_amount) or credit_amount <= 0:
            raise ValueError("Paid payment amount must be finite and positive")
        async with async_session_maker() as session:
            row = await lock_payment_for_processing(session, parsed.payment_id)
            if row is not None:
                require_payment_provider(row, provider)
                _adopt_payment_origin(getattr(row, "metadata_", None), parsed.metadata, metadata_patch)

            if row is not None and str(row.status or "") in _REVERSAL_STATUSES:
                return PipelineResult(ok=True, already_processed=True)
            if row is not None and str(row.status or "") == "success":
                if completion is not None:
                    if row.user_id is None:
                        raise ValueError("Paid payment has no canonical owner")
                    await completion(session, UserId(row.user_id), float(row.amount), row)
                    await session.commit()
                    await flush_purges(session)
                    await invalidate_payment_cache(parsed.payment_id)
                logger.info(f"[{provider}] Повторный webhook, платёж уже обработан: payment_id={parsed.payment_id}")
                return PipelineResult(ok=True, already_processed=True)

            if row is not None:
                if canonical_payment_provider(provider) in {"yookassa", "yookassa_autopay"}:
                    quote = saved_yookassa_amounts(row.metadata_)
                    if quote is not None:
                        if Decimal(str(parsed.amount)) != quote.gross:
                            raise ValueError("Paid YooKassa amount differs from saved invoice")
                        credit_amount = float(quote.net)
                if Decimal(str(row.amount)) != Decimal(str(credit_amount)):
                    raise ValueError("Paid payment amount differs from saved invoice")
                if canonical_payment_provider(provider) in {
                    "yookassa",
                    "yoomoney",
                    "robokassa",
                    "kassai",
                    "paritypay",
                    "overpay",
                }:
                    currency = str(parsed.currency).upper()
                    if currency not in {"RUB", "643"}:
                        raise ValueError("Paid payment currency differs from saved invoice")
                if (getattr(row, "metadata_", None) or {}).get("owner_deleted") is True:
                    raise ValueError("Payment owner has been deleted")
                updated = await update_payment_status(
                    session=session,
                    internal_id=int(row.id),
                    new_status="success",
                    metadata_patch=metadata_patch,
                )
                if not updated:
                    logger.error(f"[{provider}] Не удалось перевести платёж id={row.id} в success")
                    return PipelineResult(ok=False, error="update_payment_status failed")
                tg_id = _resolve_client(
                    parsed,
                    user_id=row.user_id,
                    fallback=TelegramId(row.tg_id) if row.tg_id is not None else None,
                )

                if update_currency is not None:
                    row.currency = update_currency
                if update_original_amount is not None:
                    row.original_amount = update_original_amount
            else:
                if (
                    canonical_payment_provider(provider) in {"yookassa", "yookassa_autopay"}
                    and (parsed.metadata or {}).get("yookassa_invoice") == "frozen"
                ):
                    raise ValueError("Frozen YooKassa invoice has not been saved yet")
                use_temporary_checkout = False
                cached = await get_payment_by_payment_id(session, parsed.payment_id)
                if cached and canonical_payment_provider(cached.get("payment_system")) != canonical_payment_provider(
                    provider
                ):
                    raise ValueError("Payment belongs to another provider")
                if cached and cached.get("status") == "success":
                    logger.info(
                        f"[{provider}] Повторный webhook, платёж уже обработан (кэш): payment_id={parsed.payment_id}"
                    )
                    return PipelineResult(ok=True, already_processed=True)
                _adopt_payment_origin(parsed.metadata, metadata_patch, cached.get("metadata") if cached else None)
                tg_id = _resolve_client(
                    parsed,
                    user_id=cached.get("user_id") if cached else None,
                    fallback=cached.get("tg_id") if cached else None,
                )
                tg_id = await resolve_unrecorded_payment_owner(session, tg_id)
                await add_payment(
                    session=session,
                    legacy_user_ref=tg_id,
                    amount=parsed.amount,
                    payment_system=provider,
                    status="success",
                    currency=parsed.currency,
                    payment_id=parsed.payment_id,
                    metadata=parsed.metadata or metadata_patch or (cached.get("metadata") if cached else None),
                )

            if credit_amount > 0:
                if tg_id is None:
                    raise ValueError("Paid payment has no owner")
                credited_balance = await update_balance(session, tg_id, credit_amount)
                if credited_balance is None:
                    raise ValueError("Paid payment owner disappeared")
            if completion is not None:
                if tg_id is None:
                    raise ValueError("Paid payment has no owner")
                await completion(session, tg_id, credit_amount, row)
            if credit_amount > 0:
                if use_temporary_checkout and row is not None:
                    use_temporary_checkout = await payment_checkout_matches(session, tg_id, row.metadata_)
                await send_payment_success_notification(
                    tg_id,
                    credit_amount,
                    session,
                    use_temporary_checkout=use_temporary_checkout,
                    checkout_operation=(row.metadata_ or {}).get(PAYMENT_CHECKOUT_OPERATION)
                    if row is not None
                    else None,
                )

            await session.commit()
            await flush_purges(session)
            await invalidate_payment_cache(parsed.payment_id)
            if tg_id is not None:
                try:
                    async with async_session_maker() as cache_session:
                        await invalidate_keys_list(cache_session, tg_id)
                        await flush_purges(cache_session)
                except Exception as cache_err:
                    logger.warning(f"[{provider}] Не удалось сбросить кэш ключей после платежа: {cache_err}")

        logger.info(
            f"[{provider}] Платёж обработан: payment_id={parsed.payment_id}, "
            f"tg_id={tg_id}, amount={credit_amount} (parsed={parsed.amount} {parsed.currency})"
        )
        return PipelineResult(ok=True)
    except Exception as e:
        logger.error(f"[{provider}] Ошибка обработки успешного платежа: {e}")
        return PipelineResult(ok=False, error=str(e))


_REVERSAL_STATUSES = {"refunded", "chargebacked"}


async def _invalidate_keys_cache(provider: str, tg_id: int | None) -> None:
    if tg_id is None:
        return
    try:
        async with async_session_maker() as cache_session:
            await invalidate_keys_list(cache_session, tg_id)
            await flush_purges(cache_session)
    except Exception as cache_err:
        logger.warning(f"[{provider}] Не удалось сбросить кэш ключей после платежа: {cache_err}")


async def process_cancelled_payment(
    provider: str,
    parsed: ParsedPayment,
    *,
    new_status: str = "cancelled",
) -> PipelineResult:
    """Отменяет платёж или возвращает ранее зачисленные средства."""
    try:
        async with async_session_maker() as session:
            row = await lock_payment_for_processing(session, parsed.payment_id)
            if row is not None:
                require_payment_provider(row, provider)
            payment = (
                {
                    "id": row.id,
                    "status": row.status,
                    "user_id": row.user_id,
                    "tg_id": row.tg_id,
                    "amount": row.amount,
                    "currency": row.currency,
                    "metadata": row.metadata_,
                }
                if row is not None
                else await get_payment_by_payment_id(session, parsed.payment_id)
            )
            if (
                row is None
                and payment
                and canonical_payment_provider(payment.get("payment_system")) != canonical_payment_provider(provider)
            ):
                raise ValueError("Payment belongs to another provider")
            cur = payment.get("status") if payment else None
            tg_id = _resolve_client(
                parsed,
                user_id=payment.get("user_id") if payment else None,
                fallback=payment.get("tg_id") if payment else None,
            )
            reversal = new_status in _REVERSAL_STATUSES

            if cur == new_status or cur in _REVERSAL_STATUSES:
                return PipelineResult(ok=True, already_processed=True)

            if cur == "success" and not reversal:
                return PipelineResult(ok=True, already_processed=True)

            if row is not None and (getattr(row, "metadata_", None) or {}).get("owner_deleted") is True:
                raise ValueError("Payment owner has been deleted")

            if row is None:
                tg_id = await resolve_unrecorded_payment_owner(session, tg_id)

            if cur == "success":
                amount = float(payment.get("amount") or 0)
                if row is not None and canonical_payment_provider(provider) in {"yookassa", "yookassa_autopay"}:
                    amount -= float(yookassa_refunded_net(row))
                if payment.get("id") is not None:
                    await update_payment_status(session=session, internal_id=int(payment["id"]), new_status=new_status)
                else:
                    await add_payment(
                        session=session,
                        legacy_user_ref=tg_id,
                        amount=amount,
                        payment_system=provider,
                        status=new_status,
                        currency=payment.get("currency") or parsed.currency,
                        payment_id=parsed.payment_id,
                        metadata=payment.get("metadata"),
                    )
                if amount > 0:
                    if tg_id is None:
                        raise ValueError("Refund payment has no owner")
                    updated_balance = await update_balance(session, tg_id, -amount, allow_negative=True)
                    if updated_balance is None:
                        raise ValueError("Refund owner disappeared")
                await session.commit()
                await flush_purges(session)
                await invalidate_payment_cache(parsed.payment_id)
                try:
                    from services.admin_alert import send_admin_alert

                    await send_admin_alert(
                        f"↩️ Возврат платежа ({new_status})\n"
                        f"Провайдер: {provider}\n"
                        f"tg_id: {tg_id} · сумма {amount} ₽ списана с баланса (может уйти в минус — проверьте подписку клиента).\n"
                        f"payment_id: {parsed.payment_id}"
                    )
                except Exception:
                    pass
                await _invalidate_keys_cache(provider, tg_id)
                logger.info(
                    f"[{provider}] Возврат {parsed.payment_id}: статус {new_status}, баланс откачен на {amount}"
                )
                return PipelineResult(ok=True)

            if cur in ("cancelled", "failed") and not reversal:
                return PipelineResult(ok=True, already_processed=True)

            if payment and payment.get("id") is not None:
                updated = await update_payment_status(
                    session=session,
                    internal_id=int(payment["id"]),
                    new_status=new_status,
                )
                if not updated:
                    return PipelineResult(ok=False, error="update_payment_status failed")
            elif tg_id is None or await resolve_user_optional(session, tg_id) is None:
                logger.error(
                    f"[{provider}] Отмена {parsed.payment_id}: платёж не зарегистрирован и клиент "
                    f"неизвестен (ref={tg_id}) — отменять нечего"
                )
                return PipelineResult(ok=True)
            else:
                await add_payment(
                    session=session,
                    legacy_user_ref=tg_id,
                    amount=parsed.amount,
                    payment_system=provider,
                    status=new_status,
                    currency=parsed.currency,
                    payment_id=parsed.payment_id,
                    metadata=parsed.metadata,
                )

            await session.commit()
            await flush_purges(session)
            await invalidate_payment_cache(parsed.payment_id)
            await _invalidate_keys_cache(provider, tg_id)

        logger.info(f"[{provider}] Платёж {parsed.payment_id} помечен как {new_status}")
        return PipelineResult(ok=True)
    except Exception as e:
        logger.error(f"[{provider}] Ошибка при обработке отмены/возврата платежа: {e}")
        return PipelineResult(ok=False, error=str(e))

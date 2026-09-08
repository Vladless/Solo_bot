from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from core.client_origin import normalize_origin, set_client_origin
from database import (
    add_payment,
    async_session_maker,
    get_payment_by_payment_id,
    invalidate_payment_cache,
    update_balance,
    update_payment_status,
)
from database.access.resolution import resolve_user_optional
from database.models import Payment
from handlers.payments.utils import send_payment_success_notification
from logger import logger


if TYPE_CHECKING:
    pass


@dataclass
class ParsedPayment:
    """Нормализованный результат парсинга webhook-payload'а провайдера."""

    payment_id: str
    tg_id: int | None
    amount: float
    currency: str = "RUB"
    metadata: dict | None = field(default=None)


@dataclass
class PipelineResult:
    """Что pipeline вернул адаптеру — для корректного HTTP-ответа.

    ``ok=False`` означает «повторите доставку»: адаптер отвечает 500. Поэтому ситуации,
    которые повтор не исправит, возвращают ``ok=True`` — иначе провайдер шлёт вебхук по кругу.
    """

    ok: bool
    already_processed: bool = False
    error: str | None = None


def _adopt_payment_origin(*sources: dict | None) -> None:
    """Вебхук приходит без метки клиента, но работает от имени того, кто создал счёт.

    Канал лежит в метаданных платежа с момента создания счёта — берём его, иначе покупка,
    подтверждённая вебхуком, попадёт в журнал подписок как безымянный запрос к API.
    """
    for source in sources:
        origin = normalize_origin((source or {}).get("origin"))
        if origin:
            set_client_origin(origin)
            return


def _resolve_client(parsed: ParsedPayment, *, fallback: int | None) -> int | None:
    """Клиент платежа: из вебхука, иначе из записи о платеже (в БД или в pending-кэше)."""
    raw = parsed.tg_id if parsed.tg_id is not None else fallback
    return int(raw) if raw is not None else None


async def process_success_payment(
    provider: str,
    parsed: ParsedPayment,
    *,
    metadata_patch: dict | None = None,
    credit_amount_override: float | None = None,
    update_currency: str | None = None,
    update_original_amount: float | None = None,
) -> PipelineResult:
    """Идемпотентно переводит платёж в success, зачисляет баланс, уведомляет.

    Открывает одну транзакцию на всю операцию — если что-то упадёт, всё
    откатывается атомарно.

    ``provider`` — строка для колонки ``payments.payment_system`` (регистр
    важен, некоторые провайдеры исторически писали как "YOOMONEY"/"HELEKET",
    см. комментарии в конкретных адаптерах).

    ``metadata_patch`` — опциональный dict, который ПАТЧИТ (merge) существующий
    ``payments.metadata_`` для провайдера у которых метадата приходит только
    в webhook'е (cryptobot: FX rate, invoice_id, paid amount).

    ``credit_amount_override`` — зачислить на баланс сумму, отличную от
    ``parsed.amount``. Нужно для cryptobot: провайдер возвращает paid_amount
    в USDT, но баланс пополняется исходной RUB-суммой из pending-записи.

    ``update_currency`` / ``update_original_amount`` — дополняют ``Payment``
    row для crypto-платежей (зафиксировать реально списанную валюту).
    """
    try:
        async with async_session_maker() as session:
            await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(parsed.payment_id, 0))))

            row = (
                await session.execute(
                    select(Payment).where(Payment.payment_id == parsed.payment_id).limit(1).with_for_update()
                )
            ).scalar_one_or_none()

            if row is not None and str(row.status or "") == "success":
                logger.info(f"[{provider}] Повторный webhook, платёж уже обработан: payment_id={parsed.payment_id}")
                return PipelineResult(ok=True, already_processed=True)

            if row is not None:
                _adopt_payment_origin(getattr(row, "metadata_", None), parsed.metadata, metadata_patch)
                updated = await update_payment_status(
                    session=session,
                    internal_id=int(row.id),
                    new_status="success",
                    metadata_patch=metadata_patch,
                )
                if not updated:
                    logger.error(f"[{provider}] Не удалось перевести платёж id={row.id} в success")
                    return PipelineResult(ok=False, error="update_payment_status failed")
                tg_id = _resolve_client(parsed, fallback=row.tg_id if row.tg_id is not None else row.user_id)

                if update_currency is not None:
                    row.currency = update_currency
                if update_original_amount is not None:
                    row.original_amount = update_original_amount
            else:
                cached = await get_payment_by_payment_id(session, parsed.payment_id)
                if cached and cached.get("status") == "success":
                    logger.info(
                        f"[{provider}] Повторный webhook, платёж уже обработан (кэш): payment_id={parsed.payment_id}"
                    )
                    return PipelineResult(ok=True, already_processed=True)
                _adopt_payment_origin(parsed.metadata, metadata_patch, cached.get("metadata") if cached else None)
                tg_id = _resolve_client(parsed, fallback=cached.get("tg_id") if cached else None)
                await add_payment(
                    session=session,
                    tg_id=tg_id,
                    amount=parsed.amount,
                    payment_system=provider,
                    status="success",
                    currency=parsed.currency,
                    payment_id=parsed.payment_id,
                    metadata=parsed.metadata or metadata_patch or (cached.get("metadata") if cached else None),
                )

            credit_amount = float(credit_amount_override) if credit_amount_override is not None else parsed.amount
            if tg_id is not None and credit_amount > 0:
                await update_balance(session, tg_id, credit_amount)
                await send_payment_success_notification(tg_id, credit_amount, session)

            await session.commit()
            await invalidate_payment_cache(parsed.payment_id)
            if tg_id is not None:
                try:
                    from database.keys import invalidate_keys_list

                    async with async_session_maker() as cache_session:
                        await invalidate_keys_list(cache_session, int(tg_id))
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
        from database.keys import invalidate_keys_list

        async with async_session_maker() as cache_session:
            await invalidate_keys_list(cache_session, int(tg_id))
    except Exception as cache_err:
        logger.warning(f"[{provider}] Не удалось сбросить кэш ключей после платежа: {cache_err}")


async def process_cancelled_payment(
    provider: str,
    parsed: ParsedPayment,
    *,
    new_status: str = "cancelled",
) -> PipelineResult:
    """Переводит платёж в cancelled/failed/refunded/chargebacked.

    ``new_status`` — "cancelled"/"failed" (платёж не состоялся), либо
    "refunded"/"chargebacked" (возврат уже зачисленного платежа — тогда
    автоматически откатываем баланс и уведомляем админов).
    """
    try:
        async with async_session_maker() as session:
            payment = await get_payment_by_payment_id(session, parsed.payment_id)
            cur = payment.get("status") if payment else None
            tg_id = _resolve_client(parsed, fallback=payment.get("tg_id") if payment else None)
            reversal = new_status in _REVERSAL_STATUSES

            if cur == new_status:
                return PipelineResult(ok=True, already_processed=True)

            if cur == "success":
                if not reversal:
                    return PipelineResult(ok=True, already_processed=True)
                amount = float(payment.get("amount") or 0)
                if payment.get("id") is not None:
                    await update_payment_status(session=session, internal_id=int(payment["id"]), new_status=new_status)
                if tg_id is not None and amount > 0:
                    await update_balance(session, tg_id, -amount)
                await session.commit()
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

            if cur in ("cancelled", "failed"):
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
                    tg_id=tg_id,
                    amount=parsed.amount,
                    payment_system=provider,
                    status=new_status,
                    currency=parsed.currency,
                    payment_id=parsed.payment_id,
                    metadata=parsed.metadata,
                )

            await session.commit()
            await invalidate_payment_cache(parsed.payment_id)
            await _invalidate_keys_cache(provider, tg_id)

        logger.info(f"[{provider}] Платёж {parsed.payment_id} помечен как {new_status}")
        return PipelineResult(ok=True)
    except Exception as e:
        logger.error(f"[{provider}] Ошибка при обработке отмены/возврата платежа: {e}")
        return PipelineResult(ok=False, error=str(e))

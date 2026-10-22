from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from math import isfinite
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from database.payments import PAYMENT_CHECKOUT_OPERATION, PAYMENT_CHECKOUT_SNAPSHOT, get_payment_from_db_by_payment_id
from database.temporary_data import create_temporary_data
from hooks.hooks import run_hooks
from logger import logger
from services.payments.checkout_snapshot import capture_payment_checkout
from services.payments.yookassa.amounts import (
    YOOKASSA_AMOUNTS,
    YOOKASSA_PROVIDERS,
    quote_yookassa_amount,
    saved_yookassa_amounts,
)
from settings import texts


@dataclass(frozen=True)
class PaymentLinkRequest:
    legacy_user_ref: int
    amount: int | float
    currency: str
    provider_id: str
    success_url: str | None = None
    failure_url: str | None = None
    metadata: dict[str, Any] | None = None
    accepted_gross_amount: float | None = None


@dataclass
class PaymentLinkResult:
    success: bool
    payment_id: str | None = None
    payment_url: str | None = None
    error: str | None = None
    amount_quote: dict[str, str] | None = None


def quote_payment_amount(provider_id: str, amount, currency: str = "RUB") -> dict[str, str]:
    """Возвращает зачисление, наценку и итог до оплаты."""
    provider = str(provider_id or "").upper()
    if provider in YOOKASSA_PROVIDERS and currency.upper() != "RUB":
        raise ValueError(texts.YOOKASSA_RUB_REQUIRED)
    quote = quote_yookassa_amount(
        amount,
        method="sbp" if provider == "YOOKASSA_SBP" else "ordinary",
        percent=None if provider in YOOKASSA_PROVIDERS else 0,
    )
    return quote.public(provider, currency.upper())


PaymentLinkCreator = Callable[
    [AsyncSession, int, float, str, str | None, str | None, dict[str, Any] | None],
    Awaitable[tuple[str, str | None]],
]

_registry: dict[str, PaymentLinkCreator] = {}


async def store_provider_checkout(
    session: AsyncSession, user_ref: int, state: str, data: dict, *, provider_id: str
) -> None:
    """Сохраняет оформление оплаты для обычных платёжных касс."""
    if str(provider_id).strip().upper() not in {"YOOKASSA_AUTOPAY", "YOOKASSA_AUTOPAY_WEB", "KASSA2328"}:
        await create_temporary_data(session, user_ref, state, data)
    await session.commit()


def register_payment_creator(provider_id: str, creator: PaymentLinkCreator) -> None:
    """Регистрирует создателя платёжной ссылки для кассы."""
    key = provider_id.strip().upper()
    _registry[key] = creator


def log_registered_creators() -> None:
    """Печатает одной строкой список касс, умеющих платёжные ссылки."""
    if not _registry:
        logger.warning("[Payments] Ни одна касса не зарегистрировала создателя ссылки")
        return
    keys = sorted(_registry)
    logger.info("[Payments] Кассы со ссылками ({}): {}", len(keys), ", ".join(keys))


async def merge_creators_from_hooks() -> None:
    """Подтягивает создателей из хука payment_register_creators в реестр."""
    results = await run_hooks("payment_register_creators")
    for item in results:
        if isinstance(item, dict):
            for pid, creator in item.items():
                if pid and callable(creator):
                    key = str(pid).strip().upper()
                    _registry[key] = creator


async def create_payment_link(
    session: AsyncSession,
    request: PaymentLinkRequest,
) -> PaymentLinkResult:
    """Формирует платёжную ссылку через зарегистрированную кассу."""
    await merge_creators_from_hooks()
    provider_key = request.provider_id.strip().upper()
    if provider_key in {"YOOKASSA_AUTOPAY", "YOOKASSA_AUTOPAY_WEB"}:
        from services.payments.yookassa_autopay.service import create_link

        register_payment_creator("YOOKASSA_AUTOPAY", create_link)
        register_payment_creator("YOOKASSA_AUTOPAY_WEB", create_link)
    creator = _registry.get(provider_key)
    if not creator:
        return PaymentLinkResult(
            success=False,
            error=f"Провайдер не найден или не поддерживает ссылку: {provider_key}",
        )
    try:
        amount = float(request.amount)
    except (TypeError, ValueError):
        return PaymentLinkResult(success=False, error="Некорректная сумма")
    if not isfinite(amount) or amount <= 0:
        return PaymentLinkResult(success=False, error="Сумма должна быть больше нуля")
    currency = (request.currency or "RUB").strip().upper()
    try:
        metadata = dict(request.metadata or {})
        metadata.pop(YOOKASSA_AMOUNTS, None)
        if provider_key in YOOKASSA_PROVIDERS:
            from core.settings.payments_config import PAYMENTS_CONFIG

            enabled_id = "YOOKASSA_AUTOPAY" if provider_key == "YOOKASSA_AUTOPAY_WEB" else provider_key
            if not PAYMENTS_CONFIG.get(enabled_id, False):
                return PaymentLinkResult(success=False, error=texts.YOOKASSA_PROVIDER_DISABLED)
            if request.accepted_gross_amount is not None:
                metadata["accepted_gross_amount"] = request.accepted_gross_amount
        metadata.pop(PAYMENT_CHECKOUT_SNAPSHOT, None)
        metadata.pop(PAYMENT_CHECKOUT_OPERATION, None)
        if provider_key not in {"YOOKASSA_AUTOPAY", "YOOKASSA_AUTOPAY_WEB", "KASSA2328"}:
            metadata = await capture_payment_checkout(session, request.legacy_user_ref, metadata)
        url, payment_id = await creator(
            session,
            request.legacy_user_ref,
            amount,
            currency,
            request.success_url,
            request.failure_url,
            metadata,
        )
        amount_quote = None
        if provider_key in YOOKASSA_PROVIDERS and payment_id:
            saved = await get_payment_from_db_by_payment_id(session, payment_id)
            frozen = saved_yookassa_amounts((saved or {}).get("metadata"))
            if frozen:
                amount_quote = frozen.public(provider_key)
        return PaymentLinkResult(success=True, payment_url=url, payment_id=payment_id, amount_quote=amount_quote)
    except ValueError as e:
        return PaymentLinkResult(success=False, error=str(e))
    except Exception as e:
        logger.exception(f"[Payments] Ошибка создания ссылки для {provider_key}: {e}")
        return PaymentLinkResult(success=False, error="Ошибка при создании платёжной ссылки")

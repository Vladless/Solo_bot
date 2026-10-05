from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from hooks.hooks import run_hooks
from logger import logger


@dataclass(frozen=True)
class PaymentLinkRequest:
    legacy_user_ref: int
    amount: int | float
    currency: str
    provider_id: str
    success_url: str | None = None
    failure_url: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class PaymentLinkResult:
    success: bool
    payment_id: str | None = None
    payment_url: str | None = None
    error: str | None = None


PaymentLinkCreator = Callable[
    [AsyncSession, int, float, str, str | None, str | None, dict[str, Any] | None],
    Awaitable[tuple[str, str | None]],
]

_registry: dict[str, PaymentLinkCreator] = {}


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
    if amount <= 0:
        return PaymentLinkResult(success=False, error="Сумма должна быть больше нуля")
    currency = (request.currency or "RUB").strip().upper()
    try:
        url, payment_id = await creator(
            session,
            request.legacy_user_ref,
            amount,
            currency,
            request.success_url,
            request.failure_url,
            request.metadata,
        )
        return PaymentLinkResult(success=True, payment_url=url, payment_id=payment_id)
    except ValueError as e:
        return PaymentLinkResult(success=False, error=str(e))
    except Exception as e:
        logger.exception(f"[Payments] Ошибка создания ссылки для {provider_key}: {e}")
        return PaymentLinkResult(success=False, error="Ошибка при создании платёжной ссылки")

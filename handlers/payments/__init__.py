__all__ = (
    "router",
    "create_payment_link",
    "register_payment_creator",
    "PaymentLinkRequest",
    "PaymentLinkResult",
)

from aiogram import Router

from settings.config import PROVIDERS_ENABLED
from services.payments.payment_links import (
    PaymentLinkRequest,
    PaymentLinkResult,
    create_payment_link,
    register_payment_creator,
)
from services.payments.providers import get_providers

from .cryptobot import router as cryptobot_router
from .fast_payment_flow import router as fast_payment_flow_router
from .freekassa.freekassa_pay import router as freekassa_router
from .gift import router as gift_router
from .heleket import router as heleket_router
from .kassai import router as kassai_router
from .overpay import router as overpay_router
from .paritypay import router as paritypay_router
from .pay import router as pay_router
from .platega import router as platega_router
from .robokassa import router as robokassa_router
from .stars import router as stars_router
from .tribute import router as tribute_router
from .wata import router as wata_router
from .yookassa import router as yookassa_router
from .yoomoney import router as yoomoney_router


router = Router(name="payments_main_router")

PROVIDERS = get_providers(PROVIDERS_ENABLED)

if PROVIDERS.get("YOOKASSA", {}).get("enabled"):
    router.include_router(yookassa_router)
if PROVIDERS.get("YOOMONEY", {}).get("enabled"):
    router.include_router(yoomoney_router)
if PROVIDERS.get("ROBOKASSA", {}).get("enabled"):
    router.include_router(robokassa_router)
if PROVIDERS.get("FREEKASSA", {}).get("enabled"):
    router.include_router(freekassa_router)
if PROVIDERS.get("CRYPTOBOT", {}).get("enabled"):
    router.include_router(cryptobot_router)
if PROVIDERS.get("STARS", {}).get("enabled"):
    router.include_router(stars_router)
if PROVIDERS.get("KASSAI_CARDS", {}).get("enabled") or PROVIDERS.get("KASSAI_SBP", {}).get("enabled"):
    router.include_router(kassai_router)
if PROVIDERS.get("HELEKET", {}).get("enabled"):
    router.include_router(heleket_router)
if PROVIDERS.get("WATA_RU", {}).get("enabled") or PROVIDERS.get("WATA_INT", {}).get("enabled"):
    router.include_router(wata_router)
if PROVIDERS.get("PARITYPAY_SBP", {}).get("enabled"):
    router.include_router(paritypay_router)
if PROVIDERS.get("OVERPAY_CARDS", {}).get("enabled") or PROVIDERS.get("OVERPAY_SBP", {}).get("enabled"):
    router.include_router(overpay_router)
if (
    PROVIDERS.get("PLATEGA_SBP", {}).get("enabled")
    or PROVIDERS.get("PLATEGA_CARDS", {}).get("enabled")
    or PROVIDERS.get("PLATEGA_INT", {}).get("enabled")
    or PROVIDERS.get("PLATEGA_CRYPTO", {}).get("enabled")
):
    router.include_router(platega_router)

router.include_router(tribute_router)
router.include_router(gift_router)
router.include_router(pay_router)
router.include_router(fast_payment_flow_router)

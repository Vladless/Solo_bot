__all__ = ("router",)

from aiogram import Router

from config import PROVIDERS_ENABLED
from handlers.payments.providers import get_providers

from .cryptobot import router as cryptobot_router
from .fast_payment_flow import router as fast_payment_flow_router
from .freekassa.freekassa_pay import router as freekassa_router
from .gift import router as gift_router
from .heleket import router as heleket_router
from .kassai import router as kassai_router
from .pay import router as pay_router
from .robokassa import router as robokassa_router
from .stars import router as stars_router
from .tribute import router as tribute_router
from .wata.wata import router as wata_router
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

router.include_router(tribute_router)
router.include_router(wata_router)
router.include_router(gift_router)
router.include_router(pay_router)
router.include_router(fast_payment_flow_router)

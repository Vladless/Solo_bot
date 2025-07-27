__all__ = ("router",)

from aiogram import Router

from config import (
    CRYPTO_BOT_ENABLE,
    FREEKASSA_ENABLE,
    KASSAI_ENABLE,
    ROBOKASSA_ENABLE,
    STARS_ENABLE,
    YOOKASSA_ENABLE,
    YOOMONEY_ENABLE,
    HELEKET_ENABLE,
    CRYPTOCLOUD_ENABLE,
)

from .cryprobot_pay import router as cryprobot_router
from .freekassa_pay import router as freekassa_router
from .gift import router as gift_router
from .kassai import router as kassai_router
from .robokassa_pay import router as robokassa_router
from .stars_pay import router as stars_router
from .yookassa_pay import router as yookassa_router
from .yoomoney_pay import router as yoomoney_router
from .wata import router as wata_router
from .heleket import router as heleket_router
from .cryptocloud import router as cryptocloud_router

router = Router(name="payments_main_router")

if YOOKASSA_ENABLE:
    router.include_router(yookassa_router)
if YOOMONEY_ENABLE:
    router.include_router(yoomoney_router)
if ROBOKASSA_ENABLE:
    router.include_router(robokassa_router)
if FREEKASSA_ENABLE:
    router.include_router(freekassa_router)
if CRYPTO_BOT_ENABLE:
    router.include_router(cryprobot_router)
if STARS_ENABLE:
    router.include_router(stars_router)
if KASSAI_ENABLE:
    router.include_router(kassai_router)
if HELEKET_ENABLE:
    router.include_router(heleket_router)
if CRYPTOCLOUD_ENABLE:
    router.include_router(cryptocloud_router)

router.include_router(wata_router)
router.include_router(gift_router)

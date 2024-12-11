__all__ = ('router',)

from aiogram import Router

from config import CRYPTO_BOT_ENABLE, FREEKASSA_ENABLE, ROBOKASSA_ENABLE, STARS_ENABLE, YOOKASSA_ENABLE

from .cryprobot_pay import router as cryprobot_router
from .freekassa_pay import router as freekassa_router
from .robokassa_pay import router as robokassa_router
from .stars_pay import router as stars_router
from .yookassa_pay import router as yookassa_router

router = Router(name='payments_main_router')

if YOOKASSA_ENABLE:
    router.include_router(yookassa_router)
if FREEKASSA_ENABLE:
    router.include_router(freekassa_router)
if ROBOKASSA_ENABLE:
    router.include_router(robokassa_router)
if CRYPTO_BOT_ENABLE:
    router.include_router(cryprobot_router)
if STARS_ENABLE:
    router.include_router(stars_router)

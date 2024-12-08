__all__ = ('router',)

from aiogram import Router

from .cryprobot_pay import router as cryprobot_router
from .freekassa_pay import router as freekassa_router
from .robokassa_pay import router as robokassa_router
from .stars_pay import router as stars_router
from .yookassa_pay import router as yookassa_router

router = Router(name='payments_main_router')

router.include_routers(
    cryprobot_router,
    stars_router,
    freekassa_router,
    robokassa_router,
    yookassa_router,
)

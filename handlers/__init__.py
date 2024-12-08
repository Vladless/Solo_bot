__all__ = ('router',)

from aiogram import Router

from .coupons import router as coupons_router
from .donate import router as donate_router
from .notifications import router as notifications_router
from .pay import router as pay_router
from .payments import router as payments_router
from .profile import router as profile_router
from .start import router as start_router

router = Router(name='handlers_main_router')

router.include_routers(
    start_router,
    profile_router,
    pay_router,
    donate_router,
    coupons_router,
    notifications_router,

    payments_router,
)

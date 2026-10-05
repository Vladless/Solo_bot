__all__ = ("router",)

from aiogram import Router

from .admin import router as admin_router
from .captcha import router as captcha_router
from .chat_member import router as chat_member_router
from .coupons import router as coupons_router
from .donate import router as donate_router
from .email_binding import router as email_binding_router
from .instructions import router as instructions_router
from .keys import router as keys_router
from .legal import router as legal_router
from .notifications import router as notifications_router
from .payments import router as payments_router
from .polls import router as polls_router
from .profile import router as profile_router
from .refferal import router as refferal_router
from .start import router as start_router
from .support_triage import router as support_triage_router
from .tariffs import router as tariff_router


router = Router(name="handlers_main_router")

router.include_routers(
    chat_member_router,
    start_router,
    legal_router,
    captcha_router,
    profile_router,
    donate_router,
    coupons_router,
    notifications_router,
    payments_router,
    keys_router,
    instructions_router,
    admin_router,
    refferal_router,
    tariff_router,
    email_binding_router,
    polls_router,
    support_triage_router,
)

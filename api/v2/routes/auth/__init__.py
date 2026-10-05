from fastapi import APIRouter

from api.v2.routes.auth import email_verify, google, link, password, session, telegram, yandex


router = APIRouter(prefix="/auth", tags=["Auth"])
router.include_router(password.router)
router.include_router(telegram.router)
router.include_router(google.router)
router.include_router(yandex.router)
router.include_router(link.router)
router.include_router(email_verify.router)
router.include_router(session.router)

__all__ = ["router"]

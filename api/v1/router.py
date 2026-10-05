from fastapi import APIRouter

from api.v1.routes import (
    coupons,
    gifts,
    keys,
    management,
    misc,
    modules,
    partners,
    referrals,
    servers,
    settings,
    tariffs,
    users,
)


router = APIRouter()


@router.get("/api", include_in_schema=False)
async def root():
    return {"message": "Welcome to SoloBot API", "docs": "/api/docs"}


router.include_router(users.router, prefix="/api/users", tags=["Users"])
router.include_router(keys.router, prefix="/api/keys", tags=["Keys"])
router.include_router(coupons.router, prefix="/api/coupons", tags=["Coupons"])
router.include_router(servers.router, prefix="/api/servers", tags=["Servers"])
router.include_router(tariffs.router, prefix="/api/tariffs", tags=["Tariffs"])
router.include_router(gifts.router, prefix="/api/gifts", tags=["Gifts"])
router.include_router(referrals.router, prefix="/api/referrals", tags=["Referrals"])
router.include_router(partners.router, prefix="/api/partners", tags=["Partners"])
router.include_router(misc.router, prefix="/api")
router.include_router(modules.router, prefix="/api")
router.include_router(management.router, prefix="/api/management", tags=["Management"])
router.include_router(settings.router, prefix="/api/settings", tags=["Settings"])

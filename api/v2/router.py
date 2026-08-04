from fastapi import APIRouter, Depends

from api.v2.routes import (
    analytics,
    auth,
    coupons,
    flows,
    gifts,
    identities,
    keys,
    management,
    misc,
    modules,
    notifications,
    partners,
    payment_links,
    referrals,
    root_router,
    servers,
    settings,
    tariffs,
    tickets,
    users,
    web,
)


router = APIRouter()

router.include_router(root_router)
try:
    from core.rpc import build_pack_entitlements_router

    router.include_router(build_pack_entitlements_router())
except Exception:
    pass
router.include_router(auth.router, prefix="/api")
router.include_router(users.router, prefix="/api/users", tags=["Users"])
router.include_router(users.crud_router, prefix="/api/users", tags=["Users"])
router.include_router(keys.user_router, prefix="/api/keys", tags=["Keys"])
router.include_router(keys.stats_router, prefix="/api/admin/keys", tags=["AdminKeys"])
router.include_router(keys.subs_router, prefix="/api/admin/subscriptions", tags=["AdminSubscriptions"])
router.include_router(keys.router, prefix="/api/admin/keys", tags=["AdminKeys"])
router.include_router(coupons.admin_list_router, prefix="/api/coupons", tags=["Coupons"])
router.include_router(coupons.router, prefix="/api/coupons", tags=["Coupons"])
router.include_router(
    tickets.router, prefix="/api", tags=["Tickets"], dependencies=[Depends(tickets.require_tickets_enabled)]
)
router.include_router(
    tickets.admin_router,
    prefix="/api/admin",
    tags=["AdminTickets"],
    dependencies=[Depends(tickets.require_tickets_enabled)],
)
router.include_router(servers.stats_router, prefix="/api/servers", tags=["Servers"])
router.include_router(servers.router, prefix="/api/servers", tags=["Servers"])
router.include_router(tariffs.public_router, prefix="/api/tariffs", tags=["Tariffs"])
router.include_router(tariffs.user_tariff_router, prefix="/api/tariffs", tags=["Tariffs"])
router.include_router(tariffs.stats_router, prefix="/api/tariffs", tags=["Tariffs"])
router.include_router(tariffs.router, prefix="/api/tariffs", tags=["Tariffs"])
router.include_router(gifts.stats_router, prefix="/api/gifts", tags=["Gifts"])
router.include_router(gifts.router, prefix="/api/gifts", tags=["Gifts"])
router.include_router(gifts.gift_router, prefix="/api/gifts", tags=["Gifts"])
router.include_router(referrals.router, prefix="/api/referrals", tags=["Referrals"])
router.include_router(partners.stats_router, prefix="/api/partners", tags=["Partners"])
router.include_router(partners.router, prefix="/api/partners", tags=["Partners"])
router.include_router(analytics.router, prefix="/api/analytics", tags=["Analytics"])
router.include_router(payment_links.router, prefix="/api/payment-links", tags=["PaymentLinks"])
router.include_router(identities.router, prefix="/api/identities", tags=["Identities"])
router.include_router(misc.router, prefix="/api")
router.include_router(modules.router, prefix="/api")
router.include_router(management.router, prefix="/api/management", tags=["Management"])
router.include_router(settings.router, prefix="/api/settings", tags=["Settings"])
router.include_router(web.router, prefix="", tags=["Web"])
router.include_router(flows.router, prefix="/api", tags=["Flows"])
router.include_router(notifications.router, prefix="/api", tags=["Notifications"])

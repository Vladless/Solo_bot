from aiogram import Router

from . import (
    ads,
    bans,
    clusters,
    coupons,
    gifts,
    management,
    misc,
    panel,
    sender,
    servers,
    stats,
    tariffs,
    users,
)


router = Router(name="admins_main_router")

router.include_routers(
    ads.handler.router,
    bans.handler.router,
    clusters.handler.router,
    coupons.handler.router,
    gifts.handler.router,
    management.handler.router,
    panel.handler.router,
    misc.handler.router,
    sender.handler.router,
    servers.handler.router,
    stats.handler.router,
    tariffs.handler.router,
    users.handler.router,
)

from aiogram import Router

from . import users_balance, users_bans, users_gifts, users_hwid, users_keys, users_manage, users_tariffs


router = Router()
router.include_router(users_manage.router)
router.include_router(users_balance.router)
router.include_router(users_hwid.router)
router.include_router(users_keys.router)
router.include_router(users_bans.router)
router.include_router(users_tariffs.router)
router.include_router(users_gifts.router)

import asyncio

import config

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage


SUPPORT_BOT_TOKEN = (getattr(config, "SUPPORT_BOT_TOKEN", "") or "").strip()

support_bot: Bot | None = None
support_dp: Dispatcher | None = None

if SUPPORT_BOT_TOKEN:
    support_bot = Bot(SUPPORT_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    support_dp = Dispatcher(storage=MemoryStorage())

    from handlers.support_bot.client import router as _client_router
    from handlers.support_bot.forum import router as _forum_router

    support_dp.include_router(_forum_router)
    support_dp.include_router(_client_router)


_username: dict = {"value": None}


async def get_support_username() -> str | None:
    if support_bot is None:
        return None
    if _username["value"] is None:
        try:
            _username["value"] = (await support_bot.get_me()).username
        except Exception:
            return None
    return _username["value"]


async def support_deeplink(payload: str = "") -> str | None:
    username = await get_support_username()
    if not username:
        return None
    return f"https://telegram.me/{username}?start={payload}" if payload else f"https://telegram.me/{username}"


async def start_support_polling() -> None:
    if support_bot is None or support_dp is None:
        return
    from logger import logger

    delay = 5
    while True:
        try:
            await support_bot.delete_webhook(drop_pending_updates=True)
        except Exception:
            pass
        try:
            await support_dp.start_polling(
                support_bot,
                allowed_updates=support_dp.resolve_used_update_types(),
                handle_signals=False,
            )
            logger.warning("[SupportBot] поллинг завершился штатно — рестарт через {}с", delay)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("[SupportBot] поллинг упал: {} — рестарт через {}с", e, delay)
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            raise
        delay = min(delay * 2, 60)

import traceback

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import ExceptionTypeFilter
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BufferedInputFile, ErrorEvent
from aiogram.utils.markdown import hbold

from config import ADMIN_ID, API_TOKEN
from filters.private import IsPrivate
from logger import logger
from middlewares import register_middleware

bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(bot=bot, storage=storage)

register_middleware(dp)

dp.message.filter(IsPrivate())
dp.callback_query.filter(IsPrivate())


@dp.errors(ExceptionTypeFilter(Exception))
async def errors_handler(
    event: ErrorEvent,
    bot: Bot,
) -> bool:
    if isinstance(event.exception, TelegramForbiddenError):
        logger.info(f"User {event.update.message.from_user.id} blocked the bot.")
        return True
    logger.exception(f"Update: {event.update}\nException: {event.exception}")
    if not ADMIN_ID:
        return True
    try:
        for admin_id in ADMIN_ID:
            await bot.send_document(
                chat_id=admin_id,
                document=BufferedInputFile(
                    traceback.format_exc().encode(),
                    filename=f"error_{event.update.update_id}.txt",
                ),
                caption=f"{hbold(type(event.exception).__name__)}: {str(event.exception)[:1021]}...",
        )
    except TelegramBadRequest as exception:
        logger.warning(f"Failed to send error details: {exception}")
    except Exception as exception:
        logger.error(f"Unexpected error in error handler: {exception}")
    return True

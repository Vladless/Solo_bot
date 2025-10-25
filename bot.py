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
from database import async_session_maker
from filters.private import IsPrivateFilter
from logger import logger
from utils.modules_loader import load_modules_from_folder, modules_hub


bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(bot=bot, storage=storage)

dp.include_router(modules_hub)

load_modules_from_folder()

dp.message.filter(IsPrivateFilter())
dp.callback_query.filter(IsPrivateFilter())


@dp.errors(ExceptionTypeFilter(Exception))
async def errors_handler(event: ErrorEvent, bot: Bot) -> bool:
    if isinstance(event.exception, TelegramForbiddenError):
        logger.info(f"User {event.update.message.from_user.id} заблокировал бота.")
        return True

    if isinstance(event.exception, TelegramBadRequest):
        error_message = str(event.exception)

        if (
            "query is too old and response timeout expired or query ID is invalid" in error_message
            or "message can't be deleted for everyone" in error_message
            or "message to delete not found" in error_message
        ):
            try:
                tb = "".join(
                    traceback.format_exception(type(event.exception), event.exception, event.exception.__traceback__)
                )
                logger.warning(f"Показываем стартовое меню из-за TelegramBadRequest: {error_message}")
                logger.error(f"Traceback:\n{tb}")

                if ADMIN_ID:
                    for admin_id in ADMIN_ID:
                        await bot.send_document(
                            chat_id=admin_id,
                            document=BufferedInputFile(
                                tb.encode(),
                                filename=f"error_{event.update.update_id}.txt",
                            ),
                            caption=f"{hbold(type(event.exception).__name__)}: {error_message[:1021]}...",
                        )
            except Exception as e:
                logger.error(f"Сбой при логировании/отправке ошибки админу: {e}", exc_info=True)

            try:
                from handlers.start import start_entry

                if event.update.message:
                    fsm_context = dp.fsm.get_context(
                        bot=bot,
                        chat_id=event.update.message.chat.id,
                        user_id=event.update.message.from_user.id,
                    )
                    async with async_session_maker() as session:
                        await start_entry(
                            event=event.update.message,
                            state=fsm_context,
                            session=session,
                            admin=False,
                            captcha=False,
                        )
                elif event.update.callback_query:
                    fsm_context = dp.fsm.get_context(
                        bot=bot,
                        chat_id=event.update.callback_query.message.chat.id,
                        user_id=event.update.callback_query.from_user.id,
                    )
                    async with async_session_maker() as session:
                        await start_entry(
                            event=event.update.callback_query,
                            state=fsm_context,
                            session=session,
                            admin=False,
                            captcha=False,
                        )
            except Exception as e:
                logger.error(f"Ошибка при показе стартового меню после ошибки: {e}", exc_info=True)

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

        if event.update.message:
            fsm_context = dp.fsm.get_context(
                bot=bot,
                chat_id=event.update.message.chat.id,
                user_id=event.update.message.from_user.id,
            )
            async with async_session_maker() as session:
                await start_entry(
                    event=event.update.message,
                    state=fsm_context,
                    session=session,
                    admin=False,
                    captcha=False,
                )
        elif event.update.callback_query:
            fsm_context = dp.fsm.get_context(
                bot=bot,
                chat_id=event.update.callback_query.message.chat.id,
                user_id=event.update.callback_query.from_user.id,
            )
            async with async_session_maker() as session:
                await start_entry(
                    event=event.update.callback_query,
                    state=fsm_context,
                    session=session,
                    admin=False,
                    captcha=False,
                )

    except TelegramBadRequest as exception:
        logger.warning(f"Не удалось отправить детали ошибки: {exception}")
    except Exception as exception:
        logger.error(f"Неожиданная ошибка в error handler: {exception}")

    return True

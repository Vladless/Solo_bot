import html
import re
import traceback

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import ExceptionTypeFilter
from aiogram.types import BufferedInputFile, ErrorEvent
from aiogram.utils.markdown import hbold

from config import ADMIN_ID
from database import async_session_maker
from logger import logger


_OBFUSCATED_MIN_SEQ = 15
_PLACEHOLDER = "<obfuscated>"


def _sanitize_traceback(text: str) -> str:
    """Убирает из текста длинные последовательности \\xNN (обфусцированный код)."""
    return re.sub(r"(\\x[0-9a-fA-F]{2}){" + str(_OBFUSCATED_MIN_SEQ) + r",}", _PLACEHOLDER, text)


def setup_error_handlers(dp: Dispatcher) -> None:
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
                    tb = _sanitize_traceback(
                        "".join(
                            traceback.format_exception(
                                type(event.exception),
                                event.exception,
                                event.exception.__traceback__,
                            )
                        )
                    )
                    logger.warning(f"Показываем стартовое меню из-за TelegramBadRequest: {error_message}")
                    logger.error(f"Traceback:\n{tb}")

                    if ADMIN_ID:
                        if "query is too old and response timeout expired or query ID is invalid" in error_message:
                            caption = (
                                f"{hbold('TelegramBadRequest: устаревший callback-запрос')}\n\n"
                                "Что произошло:\n"
                                "• Пользователь нажал старую кнопку, или\n"
                                "• Telegram обработал callback уже после истечения таймаута.\n\n"
                                "Описание:\n"
                                "Такое может происходить из-за временной недоступности Telegram или "
                                "нестабильного подключения сервера к API (задержки, потери пакетов, очереди запросов).\n\n"
                                "Действия:\n"
                                "• Проверить стабильность интернет-соединения сервера.\n"
                                "• Оценить задержки/нагрузку на бота и частоту callback-запросов.\n"
                                "• При необходимости оптимизировать обработку или уменьшить время между нажатием кнопки и ответом."
                            )
                        else:
                            caption = f"{hbold(type(event.exception).__name__)}: {error_message[:1021]}..."

                        for admin_id in ADMIN_ID:
                            await bot.send_document(
                                chat_id=admin_id,
                                document=BufferedInputFile(
                                    tb.encode(),
                                    filename=f"error_{event.update.update_id}.txt",
                                ),
                                caption=caption[:1024],
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
            tb_text = _sanitize_traceback(traceback.format_exc())
            for admin_id in ADMIN_ID:
                exc_text = html.escape(str(event.exception)[:1021])
                await bot.send_document(
                    chat_id=admin_id,
                    document=BufferedInputFile(
                        tb_text.encode(),
                        filename=f"error_{event.update.update_id}.txt",
                    ),
                    caption=f"{hbold(type(event.exception).__name__)}: {exc_text}...",
                )

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

        except TelegramBadRequest as exception:
            logger.warning(f"Не удалось отправить детали ошибки: {exception}")
        except Exception as exception:
            logger.error(f"Неожиданная ошибка в error handler: {exception}")

        return True

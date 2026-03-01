import asyncio
import html
import re
import time
import traceback
from collections import deque

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


_ERROR_NOTIFY_MAX_PER_MINUTE = 2
_ERROR_DEDUPE_SEC = 120
_ERROR_MSG_PREFIX_LEN = 200
_error_send_times: deque[float] = deque(maxlen=500)
_error_dedup: dict[tuple[str, str], float] = {}
_error_lock = asyncio.Lock()


def _sanitize_traceback(text: str) -> str:
    """Убирает из текста длинные последовательности \\xNN (обфусцированный код)."""
    return re.sub(r"(\\x[0-9a-fA-F]{2}){" + str(_OBFUSCATED_MIN_SEQ) + r",}", _PLACEHOLDER, text)


async def _should_send_error_to_admins(exc_type: type[BaseException], exc_message: str) -> bool:
    """
    Разрешает отправку уведомления админу только если не превышен лимит в минуту
    и такая же ошибка не отправлялась недавно (дедуп). Сбрасывает старые записи.
    """
    now = time.monotonic()
    key = (exc_type.__name__, (exc_message or "")[:_ERROR_MSG_PREFIX_LEN])
    async with _error_lock:
        while _error_send_times and _error_send_times[0] < now - 60:
            _error_send_times.popleft()
        for k, t in list(_error_dedup.items()):
            if t < now - _ERROR_DEDUPE_SEC:
                del _error_dedup[k]
        if len(_error_send_times) >= _ERROR_NOTIFY_MAX_PER_MINUTE:
            return False
        if key in _error_dedup:
            return False
        _error_dedup[key] = now
        _error_send_times.append(now)
        return True


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

                    if ADMIN_ID and await _should_send_error_to_admins(type(event.exception), error_message):
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

                        await bot.send_document(
                            chat_id=ADMIN_ID[0],
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
            if await _should_send_error_to_admins(type(event.exception), str(event.exception)):
                tb_text = _sanitize_traceback(traceback.format_exc())
                exc_text = html.escape(str(event.exception)[:1021])
                await bot.send_document(
                    chat_id=ADMIN_ID[0],
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

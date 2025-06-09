import traceback

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import ExceptionTypeFilter
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BufferedInputFile, ErrorEvent
from aiogram.utils.markdown import hbold
import subprocess

from config import ADMIN_ID, API_TOKEN
from filters.private import IsPrivateFilter
from logger import logger

bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(bot=bot, storage=storage)


def get_git_commit_number() -> str:
    repo_url = "https://github.com/Vladless/Solo_bot"

    try:
        local_number = subprocess.check_output(
            ["git", "rev-list", "--count", "HEAD"]
        ).decode().strip()

        local_hash = subprocess.check_output(
            ["git", "rev-parse", "HEAD"]
        ).decode().strip()
    except Exception:
        return "\n(Требуется обновление через CLI)"

    try:
        remote_commit = subprocess.check_output(
            ["git", "ls-remote", "origin", "refs/heads/dev"]
        ).decode()
        remote_hash = remote_commit.split()[0]

        remote_number = subprocess.check_output(
            ["git", "rev-list", "--count", remote_hash]
        ).decode().strip()

        if local_hash == remote_hash:
            return "\n(Актуальная версия)"

        return (
            f"\n(commit <a href=\"{repo_url}/commit/{local_hash}\">"
            f"#{local_number}</a> / actual commit "
            f"<a href=\"{repo_url}/commit/{remote_hash}\">#{remote_number}</a>)"
        )

    except Exception:
        return (
            f"\n(commit <a href=\"{repo_url}/commit/{local_hash}\">"
            f"#{local_number}</a> / actual commit unknown)"
        )


version = f"v4.3-Release{get_git_commit_number()}"


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
            "query is too old and response timeout expired or query ID is invalid"
            in error_message
            or "message can't be deleted for everyone" in error_message
            or "message to delete not found" in error_message
        ):
            logger.warning("Отправляем стартовое меню.")
            try:
                from handlers.start import handle_start_callback_query, start_command

                if event.update.message:
                    fsm_context = dp.fsm.get_context(
                        bot=bot,
                        chat_id=event.update.message.chat.id,
                        user_id=event.update.message.from_user.id,
                    )
                    await start_command(
                        event.update.message,
                        state=fsm_context,
                        session=None,
                        admin=False,
                        captcha=False,
                    )
                elif event.update.callback_query:
                    fsm_context = dp.fsm.get_context(
                        bot=bot,
                        chat_id=event.update.callback_query.message.chat.id,
                        user_id=event.update.callback_query.from_user.id,
                    )
                    await handle_start_callback_query(
                        event.update.callback_query,
                        state=fsm_context,
                        session=None,
                        admin=False,
                        captcha=False,
                    )
            except Exception as e:
                logger.error(f"Ошибка при показе стартового меню после ошибки: {e}")

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

        from handlers.start import handle_start_callback_query, start_command

        if event.update.message:
            fsm_context = dp.fsm.get_context(
                bot=bot,
                chat_id=event.update.message.chat.id,
                user_id=event.update.message.from_user.id,
            )
            await start_command(
                event.update.message,
                state=fsm_context,
                session=None,
                admin=False,
                captcha=False,
            )
        elif event.update.callback_query:
            fsm_context = dp.fsm.get_context(
                bot=bot,
                chat_id=event.update.callback_query.message.chat.id,
                user_id=event.update.callback_query.from_user.id,
            )
            await handle_start_callback_query(
                event.update.callback_query,
                state=fsm_context,
                session=None,
                admin=False,
                captcha=False,
            )

    except TelegramBadRequest as exception:
        logger.warning(f"Не удалось отправить детали ошибки: {exception}")
    except Exception as exception:
        logger.error(f"Неожиданная ошибка в error handler: {exception}")

    return True

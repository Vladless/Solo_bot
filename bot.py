import os
import subprocess
import time
import traceback

from functools import lru_cache

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import ExceptionTypeFilter
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BufferedInputFile, ErrorEvent
from aiogram.utils.markdown import hbold

from config import ADMIN_ID, API_TOKEN
from filters.private import IsPrivateFilter
from logger import logger


bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(bot=bot, storage=storage)


_last_check_time = 0
_last_git_info = ""


def _get_git_commit_number_uncached() -> str:
    repo_url = "https://github.com/Vladless/Solo_bot"
    cwd = os.path.abspath(os.path.dirname(__file__))

    if not os.path.isdir(os.path.join(cwd, ".git")):
        cwd = "/root/Prod/Solo_bot"
        logger.info(f"[Git] .git не найден в текущем каталоге, используем {cwd}")

    env = os.environ.copy()
    env["GIT_DIR"] = os.path.join(cwd, ".git")
    env["GIT_WORK_TREE"] = cwd

    try:
        local_number = (
            subprocess.check_output(["git", "rev-list", "--count", "HEAD"], cwd=cwd, env=env).decode().strip()
        )
        local_hash = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=cwd, env=env).decode().strip()
        try:
            branch = (
                subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd, env=env).decode().strip()
            )
            if branch == "HEAD":
                describe = (
                    subprocess.check_output(
                        ["git", "describe", "--tags", "--exact-match"],
                        cwd=cwd,
                        env=env,
                        stderr=subprocess.DEVNULL,
                    )
                    .decode()
                    .strip()
                )
                branch = "main" if describe.startswith("v") or "release" in describe.lower() else "dev"
        except Exception:
            branch = "dev"

    except Exception as e:
        logger.error(f"[Git] Ошибка при получении локального коммита: {e}")
        return f"\n(Требуется обновление через CLI (команда <code>sudo solobot</code>): {e})"

    try:
        subprocess.check_output(["git", "fetch", "origin"], cwd=cwd, env=env)
        remote_commit = subprocess.check_output(
            ["git", "ls-remote", "origin", f"refs/heads/{branch}"], cwd=cwd, env=env
        ).decode()
        remote_hash = remote_commit.split()[0]

        remote_number = (
            subprocess.check_output(["git", "rev-list", "--count", remote_hash], cwd=cwd, env=env).decode().strip()
        )

        if local_hash == remote_hash:
            logger.info("[Git] Локальная версия актуальна")
            return "\n(Актуальная версия)"

        return (
            f'\n(commit <a href="{repo_url}/commit/{local_hash}">'
            f"#{local_number}</a> / actual commit "
            f'<a href="{repo_url}/commit/{remote_hash}">#{remote_number}</a>)'
        )
    except Exception as e:
        logger.error(f"[Git] Ошибка при получении удалённого коммита: {e}")
        return "\n(Требуется обновление через CLI, команда <code>sudo solobot</code>)"


@lru_cache(maxsize=1)
def _cached_git_info() -> str:
    return _get_git_commit_number_uncached()


def get_git_commit_number() -> str:
    global _last_check_time, _last_git_info
    now = time.time()
    if now - _last_check_time > 3600:
        _last_check_time = now
        _cached_git_info.cache_clear()
        _last_git_info = _cached_git_info()
    return _last_git_info


def get_version() -> str:
    return f"v4.4-Release{get_git_commit_number()}"


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

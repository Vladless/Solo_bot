from __future__ import annotations

import asyncio
import os
import time

from collections import OrderedDict, deque
from datetime import datetime

import aiofiles
import pytz

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup

from database import async_session_maker
from database.bans import save_blocked_user_ids
from handlers.admin.sender.sender_utils import is_telegram_chat_id
from handlers.utils import format_hours, format_minutes
from logger import logger
from services.tariffs.tariff_display import get_key_tariff_display
from utils.custom_emojis import _process_text


moscow_tz = pytz.timezone("Europe/Moscow")


_photo_cache: OrderedDict[str, str] = OrderedDict()
_photo_cache_lock = asyncio.Lock()
_PHOTO_CACHE_MAX = 64
_SUPPORTED_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif")


async def _get_cached_file_id(photo_path: str) -> str | None:
    async with _photo_cache_lock:
        fid = _photo_cache.get(photo_path)
        if fid:
            _photo_cache.move_to_end(photo_path)
        return fid


async def _set_cached_file_id(photo_path: str, file_id: str) -> None:
    async with _photo_cache_lock:
        if photo_path not in _photo_cache:
            _photo_cache[photo_path] = file_id
            if len(_photo_cache) > _PHOTO_CACHE_MAX:
                _photo_cache.popitem(last=False)


def _find_photo_file(photo_path: str) -> str | None:
    if os.path.isfile(photo_path):
        return photo_path
    base_name = os.path.splitext(photo_path)[0]
    for ext in _SUPPORTED_EXTENSIONS:
        candidate = base_name + ext
        if os.path.isfile(candidate):
            return candidate
    return None


class NotificationRateLimiter:
    def __init__(self, max_rate: int = 25, window: float = 1.0) -> None:
        self.max_rate = max_rate
        self.window = window
        self.send_times: deque = deque()
        self.lock = asyncio.Lock()

    async def acquire(self):
        async with self.lock:
            while True:
                now = time.time()
                cutoff = now - self.window
                while self.send_times and self.send_times[0] <= cutoff:
                    self.send_times.popleft()
                if len(self.send_times) < self.max_rate:
                    self.send_times.append(now)
                    return
                time_to_wait = (self.send_times[0] + self.window) - now
                if time_to_wait > 0:
                    await asyncio.sleep(time_to_wait + 0.001)


def rate_limited_send(func):
    async def wrapper(*args, **kwargs):
        while True:
            try:
                return await func(*args, **kwargs)
            except TelegramRetryAfter as e:
                await asyncio.sleep(int(e.retry_after) + 1)
            except TelegramForbiddenError:
                return False
            except TelegramBadRequest:
                return False
            except Exception as e:
                tg_id = kwargs.get("tg_id") or (args[1] if len(args) > 1 else "?")
                logger.error(f"Ошибка отправки пользователю {tg_id}: {e}")
                return False

    return wrapper


async def send_notification(
    bot: Bot,
    tg_id: int,
    image_filename: str | None,
    caption: str,
    keyboard: InlineKeyboardMarkup | None = None,
) -> bool:
    if image_filename is None:
        return await _send_text(bot, tg_id, caption, keyboard)

    photo_path = os.path.join("img", image_filename)
    cached_id = await _get_cached_file_id(photo_path)
    if cached_id:
        return await _send_photo(bot, tg_id, photo_path, image_filename, caption, keyboard, cached_id)

    actual_path = _find_photo_file(photo_path)
    if actual_path:
        return await _send_photo(bot, tg_id, actual_path, image_filename, caption, keyboard)
    else:
        logger.warning(f"Файл изображения не найден: {photo_path}")
        return await _send_text(bot, tg_id, caption, keyboard)


@rate_limited_send
async def _send_photo(
    bot: Bot,
    tg_id: int,
    photo_path: str,
    image_filename: str,
    caption: str,
    keyboard: InlineKeyboardMarkup | None = None,
    cached_file_id: str | None = None,
) -> bool:
    try:
        if cached_file_id:
            await bot.send_photo(tg_id, cached_file_id, caption=caption, reply_markup=keyboard)
            return True
        async with aiofiles.open(photo_path, "rb") as f:
            image_data = await f.read()
        buffered = BufferedInputFile(image_data, filename=image_filename)
        result = await bot.send_photo(tg_id, buffered, caption=caption, reply_markup=keyboard)
        if result and hasattr(result, "photo") and result.photo:
            await _set_cached_file_id(os.path.join("img", image_filename), result.photo[-1].file_id)
        return True
    except (TelegramForbiddenError, TelegramBadRequest):
        return False
    except Exception as e:
        logger.error(f"Ошибка отправки фото пользователю {tg_id}: {e}")
        return await _send_text(bot, tg_id, caption, keyboard)


@rate_limited_send
async def _send_text(
    bot: Bot,
    tg_id: int,
    caption: str,
    keyboard: InlineKeyboardMarkup | None = None,
) -> bool:
    try:
        processed, entities = await _process_text(caption)
        kwargs = {"reply_markup": keyboard}
        if entities:
            kwargs["entities"] = entities
            kwargs["parse_mode"] = None
        await bot.send_message(tg_id, processed, **kwargs)
        return True
    except (TelegramForbiddenError, TelegramBadRequest):
        return False
    except Exception as e:
        logger.error(f"Ошибка отправки текста пользователю {tg_id}: {e}")
        return False


_NOTIFY_MAX_ATTEMPTS = 5
_NOTIFY_MAX_RETRY_AFTER = 120.0


class FastNotificationSender:
    def __init__(self, bot: Bot, messages_per_second: int = 25, max_attempts: int = _NOTIFY_MAX_ATTEMPTS) -> None:
        self.bot = bot
        self.rate_limiter = NotificationRateLimiter(max_rate=messages_per_second)
        self.max_attempts = max_attempts
        self.blocked_users: set[int] = set()
        self.queue: asyncio.Queue = asyncio.Queue()
        self.results: list[bool] = []
        self.total_sent = 0
        self.pending_retries = 0
        self.is_running = False

    async def _send_one(self, msg: dict) -> str:
        tg_id = msg["tg_id"]
        if not is_telegram_chat_id(tg_id):
            return "fail"
        try:
            await self.rate_limiter.acquire()

            processed_text, entities = await _process_text(msg["text"])
            emoji_kwargs = {}
            if entities:
                emoji_kwargs["parse_mode"] = None

            if msg.get("photo"):
                photo_path = os.path.join("img", msg["photo"])
                cached_id = await _get_cached_file_id(photo_path)

                caption_kwargs = {"caption_entities": entities} if entities else {}

                if cached_id:
                    await self.bot.send_photo(
                        chat_id=tg_id,
                        photo=cached_id,
                        caption=processed_text,
                        reply_markup=msg.get("keyboard"),
                        **emoji_kwargs,
                        **caption_kwargs,
                    )
                else:
                    actual_path = _find_photo_file(photo_path)
                    if actual_path:
                        async with aiofiles.open(actual_path, "rb") as f:
                            image_data = await f.read()
                        buffered = BufferedInputFile(image_data, filename=os.path.basename(actual_path))
                        result = await self.bot.send_photo(
                            chat_id=tg_id,
                            photo=buffered,
                            caption=processed_text,
                            reply_markup=msg.get("keyboard"),
                            **emoji_kwargs,
                            **caption_kwargs,
                        )
                        if result and hasattr(result, "photo") and result.photo:
                            await _set_cached_file_id(photo_path, result.photo[-1].file_id)
                    else:
                        text_kwargs = {"entities": entities} if entities else {}
                        await self.bot.send_message(
                            chat_id=tg_id,
                            text=processed_text,
                            reply_markup=msg.get("keyboard"),
                            **emoji_kwargs,
                            **text_kwargs,
                        )
            else:
                text_kwargs = {"entities": entities} if entities else {}
                await self.bot.send_message(
                    chat_id=tg_id,
                    text=processed_text,
                    reply_markup=msg.get("keyboard"),
                    **emoji_kwargs,
                    **text_kwargs,
                )
            return "ok"

        except TelegramRetryAfter as e:
            wait_seconds = min(float(e.retry_after), _NOTIFY_MAX_RETRY_AFTER)
            msg["_attempts"] = msg.get("_attempts", 0) + 1
            msg["_retry_at"] = time.time() + wait_seconds
            return "retry"
        except TelegramForbiddenError:
            if is_telegram_chat_id(tg_id):
                self.blocked_users.add(tg_id)
            return "fail"
        except TelegramBadRequest as e:
            if "chat not found" in str(e).lower() and is_telegram_chat_id(tg_id):
                self.blocked_users.add(tg_id)
            return "fail"
        except Exception:
            return "fail"

    async def _schedule_retry(self, msg: dict) -> None:
        try:
            wait = msg.get("_retry_at", 0.0) - time.time()
            if wait > 0:
                await asyncio.sleep(wait)
            await self.queue.put(msg)
        except asyncio.CancelledError:
            self.results.append(False)
            raise
        except Exception:
            self.results.append(False)
        finally:
            self.pending_retries -= 1

    async def _worker(self):
        while True:
            try:
                msg = await asyncio.wait_for(self.queue.get(), timeout=0.5)
            except TimeoutError:
                if not self.is_running:
                    return
                continue

            try:
                result = await self._send_one(msg)
                if result == "ok":
                    self.total_sent += 1
                    self.results.append(True)
                elif result == "retry":
                    if msg.get("_attempts", 0) < self.max_attempts:
                        try:
                            asyncio.create_task(self._schedule_retry(msg))
                            self.pending_retries += 1
                        except Exception as e:
                            logger.error(f"Не удалось запланировать повтор для {msg.get('tg_id')}: {e}")
                            self.results.append(False)
                    else:
                        self.results.append(False)
                else:
                    self.results.append(False)
            except Exception as e:
                logger.error(f"Ошибка в воркере уведомлений: {e}")
                self.results.append(False)
            finally:
                self.queue.task_done()

    async def _save_blocked_users(self):
        if not self.blocked_users:
            return
        try:
            async with async_session_maker() as session:
                await save_blocked_user_ids(session, list(self.blocked_users))
                await session.commit()
            logger.info(f"Добавлено до {len(self.blocked_users)} пользователей в blocked_users")
        except Exception as e:
            logger.error(f"Ошибка сохранения заблокированных: {e}")

    async def send_all(self, messages: list[dict], workers: int = 15) -> list[bool]:
        if not messages:
            return []

        self.is_running = True
        self.results = []
        self.total_sent = 0
        self.blocked_users = set()
        self.pending_retries = 0
        start = time.time()

        for msg in messages:
            if is_telegram_chat_id(msg.get("tg_id")):
                await self.queue.put(msg)

        worker_tasks = [asyncio.create_task(self._worker()) for _ in range(workers)]

        while True:
            await self.queue.join()
            if self.pending_retries <= 0:
                break
            await asyncio.sleep(0.5)

        self.is_running = False
        for task in worker_tasks:
            task.cancel()
        await asyncio.gather(*worker_tasks, return_exceptions=True)
        await self._save_blocked_users()

        duration = time.time() - start
        speed = self.total_sent / duration if duration > 0 else 0
        logger.info(f"Уведомления: {self.total_sent}/{len(messages)} за {duration:.1f}s ({speed:.1f} msg/s)")

        return self.results


async def send_messages_with_limit(
    bot: Bot,
    messages: list[dict],
    messages_per_second: int = 25,
) -> list[bool]:
    sender = FastNotificationSender(bot, messages_per_second)
    return await sender.send_all(messages)


async def prepare_key_expiry_data(key, session, current_time: int) -> dict:
    if isinstance(key, dict):
        expiry_timestamp = key.get("expiry_time")
        email = key.get("email") or ""
        record = dict(key)
    else:
        expiry_timestamp = getattr(key, "expiry_time", None)
        email = getattr(key, "email", "") or ""
        record = {
            "tariff_id": getattr(key, "tariff_id", None),
            "server_id": getattr(key, "server_id", None),
            "client_id": getattr(key, "client_id", None),
            "selected_device_limit": getattr(key, "selected_device_limit", None),
            "selected_traffic_limit": getattr(key, "selected_traffic_limit", None),
        }

    if not expiry_timestamp:
        return {
            "hours_left_formatted": "—",
            "formatted_expiry_date": "—",
            "tariff_name": "—",
            "subgroup_title": "",
            "traffic": "—",
            "devices": "—",
        }

    delta_ms = max(0, expiry_timestamp - current_time)
    total_minutes = delta_ms // (60 * 1000)
    hours_left = total_minutes // 60
    minutes_left = total_minutes % 60

    if hours_left > 0 or minutes_left > 0:
        parts = []
        if hours_left > 0:
            parts.append(format_hours(hours_left))
        if minutes_left > 0:
            parts.append(format_minutes(minutes_left))
        hours_left_formatted = " ".join(parts)
    else:
        hours_left_formatted = "последний день"

    expiry_datetime = datetime.fromtimestamp(expiry_timestamp / 1000, tz=moscow_tz)
    formatted_expiry_date = expiry_datetime.strftime("%d.%m.%Y %H:%M")

    tariff_name = "—"
    subgroup_title = ""
    traffic_limit_gb = 0
    device_limit = 0

    try:
        name, subgroup_title, traffic_limit_gb, device_limit, _, _ = await get_key_tariff_display(
            session=session,
            key_record=record,
        )
        if name:
            tariff_name = name
    except Exception as error:
        logger.warning(f"[NOTIFY] Ошибка тарифных лимитов для {email}: {error}")

    traffic_text = "безлимит" if traffic_limit_gb == 0 else f"{traffic_limit_gb} ГБ"
    devices_text = "безлимит" if device_limit == 0 else str(device_limit)

    return {
        "hours_left_formatted": hours_left_formatted,
        "formatted_expiry_date": formatted_expiry_date,
        "tariff_name": tariff_name,
        "subgroup_title": subgroup_title or "",
        "traffic": traffic_text,
        "devices": devices_text,
    }

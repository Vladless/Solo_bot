import asyncio
import time

from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from core.settings.modes_config import resolve_protect_content
from database import async_session_maker, save_blocked_user_ids
from handlers.admin.sender.sender_utils import get_recipient_emails, is_telegram_chat_id, parse_channels
from logger import logger


DEFAULT_MESSAGES_PER_SECOND = 25
MAX_RETRY_ATTEMPTS = 5
MAX_RETRY_AFTER_SECONDS = 120.0


def run_broadcast_in_thread(
    api_token: str,
    tg_ids: list[int],
    text_message: str,
    photo: str | None,
    keyboard_data: dict | None,
    progress_cb: Callable[[int, int, int, int, int], None] | None = None,
    channel: str = "both",
) -> dict:
    """
    Синхронная обёртка: запускает рассылку в отдельном event loop в текущем потоке.
    progress_cb принимает (completed, total, sent, failed, pending_retries).
    channel: 'bot' / 'site' / 'both'.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bot = None
    try:
        bot = Bot(
            token=api_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML, protect_content=resolve_protect_content()),
        )
        keyboard = InlineKeyboardMarkup.model_validate(keyboard_data) if keyboard_data else None
        messages = [{"tg_id": tg_id, "text": text_message, "photo": photo, "keyboard": keyboard} for tg_id in tg_ids]
        service = BroadcastService(bot=bot, session=None, messages_per_second=DEFAULT_MESSAGES_PER_SECOND)

        async def on_progress(completed: int, total: int, sent: int, failed: int, pending: int) -> None:
            if progress_cb:
                progress_cb(completed, total, sent, failed, pending)

        return loop.run_until_complete(
            service.broadcast(
                messages,
                workers=5,
                on_progress=on_progress,
                progress_interval=2.0,
                channel=channel,
            )
        )
    finally:
        if bot is not None and bot.session is not None:
            try:
                loop.run_until_complete(bot.session.close())
            except Exception:
                pass
        loop.close()


class BroadcastMessage:
    __slots__ = ("tg_id", "text", "photo", "keyboard", "attempts", "retry_at")

    def __init__(self, tg_id: int, text: str, photo: str | None = None, keyboard: Any = None) -> None:
        self.tg_id = tg_id
        self.text = text
        self.photo = photo
        self.keyboard = keyboard
        self.attempts = 0
        self.retry_at = 0.0


class RateLimiter:
    def __init__(self, max_rate: int = DEFAULT_MESSAGES_PER_SECOND, window: float = 1.0) -> None:
        self.max_rate = max_rate
        self.window = window
        self.send_times = deque()
        self.lock = asyncio.Lock()

    def _clean_old_timestamps(self, current_time: float):
        cutoff_time = current_time - self.window
        while self.send_times and self.send_times[0] <= cutoff_time:
            self.send_times.popleft()

    async def acquire(self):
        async with self.lock:
            while True:
                now = time.time()

                self._clean_old_timestamps(now)

                if len(self.send_times) < self.max_rate:
                    self.send_times.append(now)
                    return

                oldest_timestamp = self.send_times[0]
                time_to_wait = (oldest_timestamp + self.window) - now

                if time_to_wait > 0:
                    await asyncio.sleep(time_to_wait + 0.001)


class BroadcastService:
    def __init__(
        self,
        bot: Bot,
        session: AsyncSession | None = None,
        messages_per_second: int = DEFAULT_MESSAGES_PER_SECOND,
        max_attempts: int = MAX_RETRY_ATTEMPTS,
    ) -> None:
        self.bot = bot
        self._session = session
        self.rate_limiter = RateLimiter(max_rate=messages_per_second)
        self.max_attempts = max_attempts
        self.blocked_users: set[int] = set()
        self.queue: asyncio.Queue = asyncio.Queue()
        self.results: list[bool] = []
        self.total_sent = 0
        self.pending_retries = 0
        self.start_time: float | None = None
        self.is_running = False

    async def _send_single_message(self, msg: BroadcastMessage) -> str:
        try:
            await self.rate_limiter.acquire()

            if msg.photo:
                await self.bot.send_photo(
                    chat_id=msg.tg_id,
                    photo=msg.photo,
                    caption=msg.text,
                    parse_mode="HTML",
                    reply_markup=msg.keyboard,
                )
            else:
                await self.bot.send_message(
                    chat_id=msg.tg_id,
                    text=msg.text,
                    parse_mode="HTML",
                    reply_markup=msg.keyboard,
                )

            return "ok"

        except TelegramRetryAfter as e:
            wait_seconds = min(float(e.retry_after), MAX_RETRY_AFTER_SECONDS)
            msg.attempts += 1
            msg.retry_at = time.time() + wait_seconds
            logger.warning(
                f"⚠️ Flood control для {msg.tg_id}: повтор через {wait_seconds:.0f} сек "
                f"(попытка {msg.attempts}/{self.max_attempts})"
            )
            return "retry"

        except TelegramForbiddenError:
            logger.warning(f"🚫 Бот заблокирован пользователем {msg.tg_id}")
            if is_telegram_chat_id(msg.tg_id):
                self.blocked_users.add(msg.tg_id)
            return "fail"

        except TelegramBadRequest as e:
            error_msg = str(e).lower()
            if "chat not found" in error_msg:
                logger.warning(f"🚫 Чат не найден для пользователя {msg.tg_id}")
                if is_telegram_chat_id(msg.tg_id):
                    self.blocked_users.add(msg.tg_id)
            else:
                logger.warning(f"📩 Не удалось отправить сообщение пользователю {msg.tg_id}: {e}")
            return "fail"

        except Exception as e:
            logger.error(f"❌ Ошибка отправки сообщения пользователю {msg.tg_id}: {e}")
            return "fail"

    async def _schedule_retry(self, msg: BroadcastMessage) -> None:
        try:
            wait = msg.retry_at - time.time()
            if wait > 0:
                await asyncio.sleep(wait)
            await self.queue.put(msg)
        except asyncio.CancelledError:
            self.results.append(False)
            raise
        except Exception as e:
            logger.error(f"❌ Ошибка retry-планировщика для {msg.tg_id}: {e}")
            self.results.append(False)
        finally:
            self.pending_retries -= 1

    async def _worker(self) -> None:
        while True:
            try:
                msg = await asyncio.wait_for(self.queue.get(), timeout=0.5)
            except TimeoutError:
                if not self.is_running:
                    return
                continue

            try:
                result = await self._send_single_message(msg)

                if result == "ok":
                    self.total_sent += 1
                    self.results.append(True)
                elif result == "retry":
                    if msg.attempts < self.max_attempts:
                        try:
                            asyncio.create_task(self._schedule_retry(msg))
                            self.pending_retries += 1
                        except Exception as e:
                            logger.error(f"❌ Не удалось запланировать повтор для {msg.tg_id}: {e}")
                            self.results.append(False)
                    else:
                        logger.error(f"❌ Достигнут лимит попыток для {msg.tg_id} (после {msg.attempts} попыток)")
                        self.results.append(False)
                else:
                    self.results.append(False)

            except Exception as e:
                logger.error(f"❌ Ошибка в воркере рассылки: {e}")
                self.results.append(False)
            finally:
                self.queue.task_done()

    async def _save_blocked_users(self) -> None:
        if not self.blocked_users:
            return
        try:
            if self._session is not None:
                await save_blocked_user_ids(self._session, list(self.blocked_users))
            else:
                async with async_session_maker() as session:
                    await save_blocked_user_ids(session, list(self.blocked_users))
                    await session.commit()
        except Exception as e:
            logger.error(f"❌ Ошибка при сохранении заблокированных пользователей: {e}")
            if self._session is not None:
                await self._session.rollback()

    async def _progress_loop(
        self,
        total: int,
        on_progress: Callable[[int, int, int, int, int], Awaitable[None]],
        interval: float,
        progress_every: int,
    ) -> None:
        """Периодически вызывает on_progress(completed, total, sent, failed, pending_retries)."""
        last_reported = 0
        last_emit_ts = time.time()
        force_interval = 30.0
        while self.is_running:
            await asyncio.sleep(interval)
            if not self.is_running:
                break
            completed = len(self.results)
            now = time.time()
            new_results = completed - last_reported
            if new_results < progress_every and (now - last_emit_ts) < force_interval:
                continue
            sent = self.total_sent
            failed = completed - sent
            pending = self.pending_retries
            try:
                await on_progress(completed, total, sent, failed, pending)
                last_reported = completed
                last_emit_ts = now
            except Exception as e:
                logger.debug(f"[Broadcast] Ошибка обновления прогресса: {e}")

    async def broadcast(
        self,
        messages: list[dict],
        workers: int = 20,
        on_progress: Callable[[int, int, int, int, int], Awaitable[None]] | None = None,
        progress_interval: float = 2.0,
        progress_every: int = 50,
        channel: str = "both",
    ) -> dict:
        channels = parse_channels(channel)
        send_to_bot = "bot" in channels
        send_to_site = "site" in channels
        send_to_email = "email" in channels
        self.is_running = True
        self.start_time = time.time()
        self.results = []
        self.total_sent = 0
        self.blocked_users = set()
        self.pending_retries = 0

        bot_recipient_count = 0
        if send_to_bot:
            for msg_data in messages:
                tg_id = msg_data["tg_id"]
                if not is_telegram_chat_id(tg_id):
                    continue
                bot_recipient_count += 1
                msg = BroadcastMessage(
                    tg_id=tg_id,
                    text=msg_data["text"],
                    photo=msg_data.get("photo"),
                    keyboard=msg_data.get("keyboard"),
                )
                await self.queue.put(msg)

        total = bot_recipient_count if send_to_bot else 0
        logger.info(
            f"📤 Начата рассылка channel={channel} на {len(messages)} получателей "
            f"(TG: {bot_recipient_count}) с {workers} воркерами "
            f"(rate={self.rate_limiter.max_rate}/сек, max_attempts={self.max_attempts})"
        )

        progress_task: asyncio.Task | None = None
        if on_progress and total > 0:
            progress_task = asyncio.create_task(
                self._progress_loop(total, on_progress, progress_interval, max(1, progress_every)),
            )

        worker_tasks = [asyncio.create_task(self._worker()) for _ in range(workers)] if send_to_bot else []

        if send_to_bot:
            while True:
                await self.queue.join()
                if self.pending_retries <= 0:
                    break
                await asyncio.sleep(0.5)

        self.is_running = False

        if progress_task is not None:
            progress_task.cancel()
            try:
                await progress_task
            except asyncio.CancelledError:
                pass
            completed = len(self.results)
            try:
                await on_progress(
                    completed,
                    total,
                    self.total_sent,
                    completed - self.total_sent,
                    0,
                )
            except Exception as e:
                logger.debug(f"[Broadcast] Финальное обновление прогресса: {e}")

        for task in worker_tasks:
            task.cancel()
        await asyncio.gather(*worker_tasks, return_exceptions=True)

        if self._session is not None:
            await self._save_blocked_users()

        end_time = time.time()
        total_duration = end_time - self.start_time
        success_count = sum(1 for r in self.results if r)
        avg_speed = self.total_sent / total_duration if total_duration > 0 else 0

        stats = {
            "total_duration": total_duration,
            "total_sent": self.total_sent,
            "success_count": success_count,
            "failed_count": len(self.results) - success_count,
            "avg_speed": avg_speed,
            "total_messages": len(messages),
            "blocked_users": len(self.blocked_users),
            "blocked_user_ids": list(self.blocked_users),
        }

        logger.info(
            f"✅ Рассылка завершена: {success_count}/{bot_recipient_count or len(messages)} успешно, "
            f"скорость: {avg_speed:.1f} сообщений/сек, время: {total_duration:.1f} сек"
        )

        if send_to_site:
            await self._create_web_notifications(messages)

        if send_to_email:
            email_sent, email_failed = await self._send_emails(messages)
            stats["email_sent"] = email_sent
            stats["email_failed"] = email_failed

        return stats

    async def _send_emails(self, messages: list[dict]) -> tuple[int, int]:
        """Рассылает письмо на почту получателям, у кого привязан email."""
        if not messages:
            return (0, 0)
        try:
            from mail.smtp import build_broadcast_email, send_broadcast_email, smtp_configured

            if not smtp_configured():
                logger.warning("[Broadcast] Канал 'почта' выбран, но SMTP не настроен")
                return (0, 0)

            tg_ids = [m.get("tg_id") for m in messages if m.get("tg_id")]
            if self._session is not None:
                email_map = await get_recipient_emails(self._session, tg_ids)
            else:
                async with async_session_maker() as session:
                    email_map = await get_recipient_emails(session, tg_ids)

            addresses = sorted(set(email_map.values()))
            if not addresses:
                return (0, 0)

            text = messages[0].get("text", "")
            image_url = None
            photo_id = messages[0].get("photo")
            if photo_id and self.bot is not None:
                try:
                    from utils.web_media import host_telegram_photo

                    image_url = await host_telegram_photo(self.bot, photo_id)
                except Exception:
                    image_url = None

            subject, html_body, text_body = build_broadcast_email(text, image_url)

            sent = 0
            failed = 0
            semaphore = asyncio.Semaphore(5)

            async def _send_one(addr: str) -> None:
                nonlocal sent, failed
                async with semaphore:
                    try:
                        await send_broadcast_email(addr, subject, html_body, text_body)
                        sent += 1
                    except Exception as exc:
                        failed += 1
                        logger.warning(f"[Broadcast] Письмо на {addr} не отправлено: {exc}")

            await asyncio.gather(*[_send_one(addr) for addr in addresses])
            logger.info(f"📧 Email-рассылка завершена: отправлено {sent}, ошибок {failed}")
            return (sent, failed)
        except Exception as e:
            logger.warning(f"[Broadcast] Ошибка email-рассылки: {e}")
            return (0, 0)

    async def _create_web_notifications(self, messages: list[dict]) -> None:
        """Создаёт web-уведомления для всех получателей рассылки."""
        if not messages:
            return
        try:
            from database.web_notifications import notify_web

            text = messages[0].get("text", "")
            import re

            clean = re.sub(r"<[^>]+>", "", text).strip()
            lines = clean.split("\n", 1)
            title = (lines[0][:120] + "…") if len(lines[0]) > 120 else lines[0]
            body = lines[1].strip() if len(lines) > 1 else ""
            if len(body) > 4000:
                body = body[:4000] + "…"

            image_url = None
            photo_id = messages[0].get("photo")
            if photo_id and self.bot is not None:
                from utils.web_media import host_telegram_photo

                image_url = await host_telegram_photo(self.bot, photo_id)
            notif_data = {"image_url": image_url} if image_url else None

            session = self._session
            if session is None:
                from database import async_session_maker

                async with async_session_maker() as session:
                    for msg in messages:
                        tg_id = msg.get("tg_id")
                        if tg_id and tg_id not in self.blocked_users:
                            await notify_web(
                                session, tg_id=tg_id, type="broadcast", title=title, message=body, data=notif_data
                            )
                    await session.commit()
            else:
                for msg in messages:
                    tg_id = msg.get("tg_id")
                    if tg_id and tg_id not in self.blocked_users:
                        await notify_web(
                            session, tg_id=tg_id, type="broadcast", title=title, message=body, data=notif_data
                        )
        except Exception as e:
            logger.warning(f"[Broadcast] Ошибка создания web-уведомлений: {e}")

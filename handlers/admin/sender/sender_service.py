import asyncio
import time

from collections import deque
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy.ext.asyncio import AsyncSession

from logger import logger


class BroadcastMessage:
    def __init__(self, tg_id: int, text: str, photo: str | None = None, keyboard: Any = None) -> None:
        self.tg_id = tg_id
        self.text = text
        self.photo = photo
        self.keyboard = keyboard
        self.retry_after = None
        self.attempts = 0


class RateLimiter:
    def __init__(self, max_rate: int = 35, window: float = 1.0) -> None:
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
    def __init__(self, bot: Bot, session: AsyncSession, messages_per_second: int = 35) -> None:
        self.bot = bot
        self.session = session
        self.rate_limiter = RateLimiter(max_rate=messages_per_second)
        self.blocked_users = set()
        self.queue = asyncio.Queue()
        self.delayed_queue = asyncio.Queue()
        self.results = []
        self.total_sent = 0
        self.start_time = None
        self.is_running = False

    async def _send_single_message(self, msg: BroadcastMessage) -> bool:
        try:
            await self.rate_limiter.acquire()

            if msg.photo:
                await self.bot.send_photo(
                    chat_id=msg.tg_id, photo=msg.photo, caption=msg.text, parse_mode="HTML", reply_markup=msg.keyboard
                )
            else:
                await self.bot.send_message(
                    chat_id=msg.tg_id, text=msg.text, parse_mode="HTML", reply_markup=msg.keyboard
                )

            return True

        except TelegramRetryAfter as e:
            msg.retry_after = e.retry_after
            msg.attempts += 1
            logger.warning(
                f"⚠️ Flood control для {msg.tg_id}: повтор через {e.retry_after} сек. (попытка {msg.attempts})"
            )
            await self.delayed_queue.put(msg)
            return False

        except TelegramForbiddenError:
            logger.warning(f"🚫 Бот заблокирован пользователем {msg.tg_id}")
            self.blocked_users.add(msg.tg_id)
            return False

        except TelegramBadRequest as e:
            error_msg = str(e).lower()
            if "chat not found" in error_msg:
                logger.warning(f"🚫 Чат не найден для пользователя {msg.tg_id}")
                self.blocked_users.add(msg.tg_id)
            else:
                logger.warning(f"📩 Не удалось отправить сообщение пользователю {msg.tg_id}: {e}")
            return False

        except Exception as e:
            logger.error(f"❌ Ошибка отправки сообщения пользователю {msg.tg_id}: {e}")
            return False

    async def _process_delayed_messages(self):
        while self.is_running:
            try:
                if not self.delayed_queue.empty():
                    msg = await asyncio.wait_for(self.delayed_queue.get(), timeout=0.1)

                    if msg.retry_after:
                        await asyncio.sleep(msg.retry_after)
                        msg.retry_after = None

                    if msg.attempts < 3:
                        await self.queue.put(msg)
                    else:
                        logger.error(f"❌ Достигнут лимит попыток для {msg.tg_id}")
                        self.results.append(False)
                else:
                    await asyncio.sleep(0.1)

            except TimeoutError:
                continue
            except Exception as e:
                logger.error(f"❌ Ошибка в обработчике отложенных сообщений: {e}")
                await asyncio.sleep(0.1)

    async def _worker(self):
        while self.is_running:
            try:
                msg = await asyncio.wait_for(self.queue.get(), timeout=0.1)

                success = await self._send_single_message(msg)

                if success:
                    self.total_sent += 1
                    self.results.append(True)
                elif msg.attempts == 0:
                    self.results.append(False)

                self.queue.task_done()

            except TimeoutError:
                continue
            except Exception as e:
                logger.error(f"❌ Ошибка в воркере рассылки: {e}")
                await asyncio.sleep(0.1)

    async def _save_blocked_users(self):
        if not self.blocked_users:
            return

        try:
            from sqlalchemy.dialects.postgresql import insert

            from database.models import BlockedUser

            values = [{"tg_id": tg_id} for tg_id in self.blocked_users]
            stmt = insert(BlockedUser).values(values).on_conflict_do_nothing(index_elements=[BlockedUser.tg_id])
            await self.session.execute(stmt)
            await self.session.commit()
            logger.info(f"📝 Добавлено {len(self.blocked_users)} пользователей в blocked_users")
        except Exception as e:
            logger.error(f"❌ Ошибка при сохранении заблокированных пользователей: {e}")
            await self.session.rollback()

    async def broadcast(self, messages: list[dict], workers: int = 20) -> dict:
        self.is_running = True
        self.start_time = time.time()
        self.results = []
        self.total_sent = 0
        self.blocked_users = set()

        for msg_data in messages:
            msg = BroadcastMessage(
                tg_id=msg_data["tg_id"],
                text=msg_data["text"],
                photo=msg_data.get("photo"),
                keyboard=msg_data.get("keyboard"),
            )
            await self.queue.put(msg)

        logger.info(f"📤 Начата рассылка на {len(messages)} пользователей с {workers} воркерами")

        worker_tasks = [asyncio.create_task(self._worker()) for _ in range(workers)]

        delayed_task = asyncio.create_task(self._process_delayed_messages())

        await self.queue.join()

        await asyncio.sleep(1)
        while not self.delayed_queue.empty():
            await asyncio.sleep(1)

        self.is_running = False

        for task in worker_tasks:
            task.cancel()
        delayed_task.cancel()

        await asyncio.gather(*worker_tasks, delayed_task, return_exceptions=True)

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
        }

        logger.info(
            f"✅ Рассылка завершена: {success_count}/{len(messages)} успешно, "
            f"скорость: {avg_speed:.1f} сообщений/сек, время: {total_duration:.1f} сек"
        )

        return stats

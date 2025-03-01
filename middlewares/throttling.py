from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware, Bot
from aiogram.types import TelegramObject, CallbackQuery
from cachetools import TTLCache

# Время троттлинга (в секундах)
THROTTLE_TIME = 3.0


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        # Общий кэш для всех запросов
        self.cache = TTLCache(maxsize=10_000, ttl=THROTTLE_TIME)
        # Кэш для уведомлений о троттлинге
        self.throttle_notice_cache = TTLCache(maxsize=10_000, ttl=3.0)

    async def __call__(
            self,
            handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
            event: TelegramObject,
            data: Dict[str, Any],
    ) -> Any:
        # Получаем бота и пользователя
        bot: Bot | None = data.get("bot", None)
        user_id = event.from_user.id if event.from_user else None

        # Проверяем троттлинг
        if user_id in self.cache:
            # Показываем уведомление, если это CallbackQuery и уведомление не показывалось недавно
            if isinstance(event, CallbackQuery) and user_id not in self.throttle_notice_cache:
                self.throttle_notice_cache[user_id] = None
                await bot.answer_callback_query(
                    callback_query_id=event.id,
                    text="Слишком много запросов! Пожалуйста, подождите...",
                    show_alert=False
                )
            return None
        else:
            self.cache[user_id] = None

        return await handler(event, data)

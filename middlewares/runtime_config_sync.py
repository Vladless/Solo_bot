from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from core.settings.runtime_sync import maybe_sync_runtime_configs


class RuntimeConfigSyncMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            await maybe_sync_runtime_configs()
        except Exception:
            pass
        return await handler(event, data)

from __future__ import annotations

from dataclasses import dataclass

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class NotificationContext:
    bot: Bot
    session: AsyncSession
    current_time: int
    preload_data: dict | None = None
    bulk_updates: dict | None = None

    def get_balance(self, tg_id: int) -> float:
        if self.preload_data and tg_id in self.preload_data.get("balances_cache", {}):
            return self.preload_data["balances_cache"][tg_id]
        return 0.0

    def get_tariff(self, tariff_id: int) -> dict | None:
        if self.preload_data and tariff_id in self.preload_data.get("tariffs_cache", {}):
            return self.preload_data["tariffs_cache"][tariff_id]
        return None

from __future__ import annotations

import threading

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Setting


class SettingsCache:
    _cache: dict[str, dict[str, Any]]
    _lock: threading.Lock

    def __init__(self) -> None:
        self._cache = {}
        self._lock = threading.Lock()

    @staticmethod
    def _row_to_item(s: Setting) -> dict[str, Any]:
        return {
            "key": s.key,
            "value": s.value,
            "description": s.description,
            "created_at": getattr(s, "created_at", None),
            "updated_at": getattr(s, "updated_at", None),
        }

    async def load(self, session: AsyncSession) -> None:
        result = await session.execute(select(Setting))
        rows = result.scalars().all()
        with self._lock:
            self._cache.clear()
            for s in rows:
                self._cache[s.key] = self._row_to_item(s)

    def get_all(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(x) for x in self._cache.values()]

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._cache[key]) if key in self._cache else None

    def update(
        self,
        key: str,
        value: Any,
        description: str | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> None:
        now = datetime.utcnow()
        with self._lock:
            if key in self._cache:
                self._cache[key]["value"] = value
                self._cache[key]["updated_at"] = updated_at if updated_at is not None else now
                if description is not None:
                    self._cache[key]["description"] = description
            else:
                self._cache[key] = {
                    "key": key,
                    "value": value,
                    "description": description,
                    "created_at": created_at if created_at is not None else now,
                    "updated_at": updated_at if updated_at is not None else now,
                }

    def delete(self, key: str) -> None:
        with self._lock:
            self._cache.pop(key, None)


settings_cache = SettingsCache()


async def load(session: AsyncSession) -> None:
    await settings_cache.load(session)

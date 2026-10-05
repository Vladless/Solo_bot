from __future__ import annotations

import asyncio
import time

from typing import Any

from core.redis_cache import cache_get, cache_key, cache_set
from settings.cache_config import (
    RUNTIME_CONFIG_SYNC_PULL_INTERVAL_SEC,
    RUNTIME_CONFIG_SYNC_TTL_SEC,
)


_RUNTIME_CONFIGS_KEY = cache_key("runtime_configs")
_REGISTRY: dict[str, dict[str, Any]] = {}
_LOCAL_VERSION = 0.0
_LAST_PULL_MONOTONIC = 0.0
_PULL_LOCK = asyncio.Lock()


def register_runtime_config(name: str, config_ref: dict[str, Any]) -> None:
    _REGISTRY[name] = config_ref


def _snapshot_from_registry() -> dict[str, dict[str, Any]]:
    return {name: dict(config_ref) for name, config_ref in _REGISTRY.items()}


def _apply_runtime_config(name: str, raw_value: Any) -> None:
    target = _REGISTRY.get(name)
    if target is None or not isinstance(raw_value, dict):
        return
    target.clear()
    target.update(raw_value)
    if name == "MODES_CONFIG":
        try:
            from core.settings.modes_config import apply_protect_content_to_bot

            apply_protect_content_to_bot()
        except Exception:
            pass


async def publish_runtime_snapshot() -> None:
    global _LOCAL_VERSION
    version = time.time()
    payload = {
        "version": version,
        "configs": _snapshot_from_registry(),
    }
    await cache_set(_RUNTIME_CONFIGS_KEY, payload, RUNTIME_CONFIG_SYNC_TTL_SEC)
    _LOCAL_VERSION = max(_LOCAL_VERSION, float(version))


async def publish_runtime_config(name: str, config_value: dict[str, Any]) -> None:
    global _LOCAL_VERSION
    if not isinstance(config_value, dict):
        return

    merged_configs: dict[str, dict[str, Any]] = {}
    cached = await cache_get(_RUNTIME_CONFIGS_KEY)
    if isinstance(cached, dict):
        raw_configs = cached.get("configs")
        if isinstance(raw_configs, dict):
            for key, value in raw_configs.items():
                if isinstance(value, dict):
                    merged_configs[key] = dict(value)

    merged_configs[name] = dict(config_value)
    version = time.time()
    payload = {"version": version, "configs": merged_configs}
    await cache_set(_RUNTIME_CONFIGS_KEY, payload, RUNTIME_CONFIG_SYNC_TTL_SEC)
    _LOCAL_VERSION = max(_LOCAL_VERSION, float(version))


async def maybe_sync_runtime_configs(force: bool = False) -> bool:
    global _LOCAL_VERSION, _LAST_PULL_MONOTONIC
    now = time.monotonic()
    if not force and (now - _LAST_PULL_MONOTONIC) < RUNTIME_CONFIG_SYNC_PULL_INTERVAL_SEC:
        return False

    async with _PULL_LOCK:
        now = time.monotonic()
        if not force and (now - _LAST_PULL_MONOTONIC) < RUNTIME_CONFIG_SYNC_PULL_INTERVAL_SEC:
            return False
        _LAST_PULL_MONOTONIC = now

        payload = await cache_get(_RUNTIME_CONFIGS_KEY)
        if not isinstance(payload, dict):
            return False

        try:
            remote_version = float(payload.get("version") or 0.0)
        except (TypeError, ValueError):
            remote_version = 0.0
        if remote_version <= _LOCAL_VERSION:
            return False

        raw_configs = payload.get("configs")
        if not isinstance(raw_configs, dict):
            return False

        for name, raw_value in raw_configs.items():
            _apply_runtime_config(name, raw_value)
        _LOCAL_VERSION = remote_version
        return True

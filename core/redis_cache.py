import asyncio
import json
import os
import time

from importlib import import_module
from typing import Any

from logger import logger
from settings.config import REDIS_URL


_REDIS_CLIENTS: dict[tuple[int, int], Any] = {}
_REDIS_UNAVAILABLE_UNTIL = 0.0
_REDIS_BACKOFF_SEC = 5.0


def _now() -> float:
    return time.monotonic()


def _client_key() -> tuple[int, int]:
    try:
        loop = asyncio.get_running_loop()
        loop_id = id(loop)
    except RuntimeError:
        loop_id = 0
    return os.getpid(), loop_id


def _drop_client(client_key: tuple[int, int]) -> None:
    _REDIS_CLIENTS.pop(client_key, None)


async def _get_redis() -> Any | None:
    global _REDIS_UNAVAILABLE_UNTIL

    client_key = _client_key()
    client = _REDIS_CLIENTS.get(client_key)
    if client is not None:
        return client
    if _REDIS_UNAVAILABLE_UNTIL > _now():
        return None

    try:
        redis_asyncio = import_module("redis.asyncio")
        blocking_pool_cls = import_module("redis.asyncio.connection").BlockingConnectionPool
        pool = blocking_pool_cls.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=128,
            timeout=10,
            health_check_interval=30,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
        )
        client = redis_asyncio.Redis(connection_pool=pool)
        await client.ping()
        _REDIS_CLIENTS[client_key] = client
        return client
    except Exception as exc:
        url_display = REDIS_URL.split("@")[-1] if "@" in REDIS_URL else REDIS_URL
        logger.warning(f"[Redis] Подключение не удалось ({url_display}): {exc}. Повтор через {_REDIS_BACKOFF_SEC} с.")
        _REDIS_UNAVAILABLE_UNTIL = _now() + _REDIS_BACKOFF_SEC
        _drop_client(client_key)
        return None


def cache_key(prefix: str, *parts: Any) -> str:
    tail = ":".join(str(p) for p in parts)
    return f"{prefix}:{tail}" if tail else prefix


async def cache_get(key: str) -> Any | None:
    client = await _get_redis()
    if client is None:
        return None
    try:
        raw = await client.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        return None


async def cache_mget(keys: list[str]) -> list[Any | None]:
    """Возвращает значения для ключей (None для отсутствующих). Один round-trip в Redis."""
    if not keys:
        return []
    client = await _get_redis()
    if client is None:
        return [None] * len(keys)
    try:
        raw_list = await client.mget(keys)
        result = []
        for raw in raw_list:
            if raw is None:
                result.append(None)
            else:
                try:
                    result.append(json.loads(raw))
                except Exception:
                    result.append(None)
        return result
    except Exception:
        return [None] * len(keys)


async def cache_set(key: str, value: Any, ttl_sec: float) -> bool:
    client = await _get_redis()
    if client is None:
        return False
    try:
        ttl = max(1, int(ttl_sec))
        await client.set(key, json.dumps(value, ensure_ascii=False), ex=ttl)
        return True
    except Exception:
        return False


async def cache_delete(key: str) -> None:
    client = await _get_redis()
    if client is None:
        return
    try:
        await client.delete(key)
    except Exception:
        return


async def cache_setnx(key: str, value: Any, ttl_sec: float) -> bool:
    client = await _get_redis()
    if client is None:
        return False
    try:
        ttl = max(1, int(ttl_sec))
        return bool(await client.set(key, json.dumps(value, ensure_ascii=False), ex=ttl, nx=True))
    except Exception:
        return False


async def cache_incr(key: str, ttl_sec: float) -> int:
    client = await _get_redis()
    if client is None:
        return 1
    try:
        value = await client.incr(key)
        if value == 1:
            await client.expire(key, max(1, int(ttl_sec)))
        return int(value)
    except Exception:
        return 1


async def cache_incr_checked(key: str, ttl_sec: float) -> tuple[int, bool]:
    """Возвращает (value, redis_available). redis_available=False значит клиент
    должен применить fallback-логику (например, in-memory limiter).
    """
    client = await _get_redis()
    if client is None:
        return 1, False
    try:
        value = await client.incr(key)
        if value == 1:
            await client.expire(key, max(1, int(ttl_sec)))
        return int(value), True
    except Exception:
        return 1, False


async def cache_delete_pattern(pattern: str) -> int:
    client = await _get_redis()
    if client is None:
        return 0
    deleted = 0
    try:
        async for key in client.scan_iter(match=pattern, count=200):
            deleted += int(await client.delete(key))
    except Exception:
        return deleted
    return deleted


async def cache_rpush(key: str, *values: Any) -> int:
    """Добавляет значения в хвост списка. Значения сериализуются в JSON. Возвращает длину списка после или 0 при ошибке."""
    if not values:
        return 0
    client_key = _client_key()
    client = await _get_redis()
    if client is None:
        return 0
    try:
        raw = [json.dumps(v, ensure_ascii=False) for v in values]
        return int(await client.rpush(key, *raw))
    except Exception as exc:
        logger.warning(f"[Redis] rpush({key}) не удался: {exc}")
        _drop_client(client_key)
        return 0


async def cache_expire(key: str, ttl_sec: int) -> bool:
    """Устанавливает TTL для ключа. Возвращает True при успехе."""
    client = await _get_redis()
    if client is None:
        return False
    try:
        return await client.expire(key, max(1, int(ttl_sec)))
    except Exception:
        return False


async def cache_lrange(key: str, start: int, end: int) -> list[Any]:
    """Возвращает срез списка. end=-1 — до конца. Элементы десериализуются из JSON."""
    client = await _get_redis()
    if client is None:
        return []
    try:
        raw_list = await client.lrange(key, start, end)
        out = []
        for raw in raw_list:
            try:
                out.append(json.loads(raw))
            except Exception:
                pass
        return out
    except Exception:
        return []


async def cache_lpop_batch(key: str, count: int) -> list[Any]:
    """Забирает до count элементов с головы списка (FIFO). Совместимо с Redis < 6.2."""
    if count <= 0:
        return []
    client = await _get_redis()
    if client is None:
        return []
    out = []
    try:
        for _ in range(count):
            raw = await client.lpop(key)
            if raw is None:
                break
            try:
                out.append(json.loads(raw))
            except Exception:
                pass
        return out
    except Exception:
        return out


async def cache_lmove_batch(source: str, destination: str, count: int) -> list[Any]:
    """Атомарно переносит до count элементов из головы source в хвост destination."""
    if count <= 0:
        return []
    client = await _get_redis()
    if client is None:
        return []
    try:
        raw_list = await client.eval(
            """
            local moved = {}
            local count = tonumber(ARGV[1])
            for i = 1, count do
                local item = redis.call('LPOP', KEYS[1])
                if not item then
                    break
                end
                redis.call('RPUSH', KEYS[2], item)
                table.insert(moved, item)
            end
            return moved
            """,
            2,
            source,
            destination,
            int(count),
        )
        out = []
        for raw in raw_list or []:
            try:
                out.append(json.loads(raw))
            except Exception:
                pass
        return out
    except Exception as exc:
        logger.warning(f"[Redis] lmove_batch({source}->{destination}) не удался: {exc}")
        return []


async def cache_publish(channel: str, payload: Any) -> int:
    client = await _get_redis()
    if client is None:
        return 0
    try:
        raw = json.dumps(payload, ensure_ascii=False) if not isinstance(payload, str | bytes) else payload
        return int(await client.publish(channel, raw))
    except Exception as exc:
        logger.warning(f"[Redis] publish({channel}) не удался: {exc}")
        return 0


async def redis_connection_ok() -> bool:
    return await _get_redis() is not None

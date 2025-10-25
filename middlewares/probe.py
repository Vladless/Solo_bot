import time

from aiogram.dispatcher.middlewares.base import BaseMiddleware

from logger import logger


class StreamProbeMiddleware(BaseMiddleware):
    def __init__(self, name: str = "global") -> None:
        self.name = name

    async def __call__(self, handler, event, data):
        t0 = data.get("_mw_t0")
        if t0 is None:
            now = time.perf_counter()
            data["_mw_t0"] = now
            data["_mw_prev"] = now
        try:
            return await handler(event, data)
        finally:
            total = (time.perf_counter() - data["_mw_t0"]) * 1000
            logger.info(f"[mw:{self.name}] {total:.2f} ms")


class MiddlewareProbe(BaseMiddleware):
    def __init__(self, inner: BaseMiddleware, name: str) -> None:
        self.inner = inner
        self.name = name

    async def __call__(self, handler, event, data):
        now = time.perf_counter()
        t0 = data.setdefault("_mw_t0", now)
        prev = data.setdefault("_mw_prev", now)
        logger.info(f"[mw:{self.name}] +{(now - prev) * 1000:.2f} ms total {(now - t0) * 1000:.2f} ms")

        downstream_ms = 0.0

        async def timed_handler(event, data):
            nonlocal downstream_ms
            ts = time.perf_counter()
            res = await handler(event, data)
            downstream_ms = (time.perf_counter() - ts) * 1000
            return res

        start = time.perf_counter()
        try:
            return await self.inner(timed_handler, event, data)
        finally:
            end = time.perf_counter()
            data["_mw_prev"] = end
            total_ms = (end - start) * 1000
            self_ms = total_ms - downstream_ms
            logger.info(f"[mw:{self.name}:self] {self_ms:.2f} ms")
            logger.info(f"[mw:{self.name}:down] {downstream_ms:.2f} ms")
            logger.info(f"[mw:{self.name}:total] {total_ms:.2f} ms")


class TailHandlerProbe(BaseMiddleware):
    def __init__(self, name: str = "handler") -> None:
        self.name = name

    async def __call__(self, handler, event, data):
        now = time.perf_counter()
        t0 = data.setdefault("_mw_t0", now)
        prev = data.setdefault("_mw_prev", now)
        logger.info(f"[mw:{self.name}:enter] +{(now - prev) * 1000:.2f} ms total {(now - t0) * 1000:.2f} ms")
        start = time.perf_counter()
        try:
            return await handler(event, data)
        finally:
            end = time.perf_counter()
            data["_mw_prev"] = end
            handler_ms = (end - start) * 1000
            total = (end - t0) * 1000
            logger.info(f"[mw:{self.name}] {handler_ms:.2f} ms total {total:.2f} ms")

import asyncio
import unittest

from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parent.parent
SOURCE = (ROOT / "api" / "main.py").read_text(encoding="utf-8")


def _load(shutting_down: bool):
    """Берёт глушитель отмены из api/main.py вместе с его определением состояния остановки."""
    start = SOURCE.index("_api_server = None")
    end = SOURCE.index('@app.on_event("shutdown")')
    namespace = {"asyncio": asyncio, "_shutting_down": shutting_down}
    exec(SOURCE[start:end], namespace)
    return namespace["QuietShutdownMiddleware"]


def _load_with_server_exit():
    """Тот же глушитель, но остановку сообщает сам сервер uvicorn: событие shutdown выключено."""
    start = SOURCE.index("_api_server = None")
    end = SOURCE.index('@app.on_event("shutdown")')
    namespace = {"asyncio": asyncio, "_shutting_down": False}
    exec(SOURCE[start:end], namespace)
    namespace["set_api_server"](SimpleNamespace(should_exit=True))
    return namespace["QuietShutdownMiddleware"]


async def _cancelling_app(scope, receive, send):
    await receive()
    raise asyncio.CancelledError()


async def _noop_send(message):
    return None


class QuietShutdownMiddlewareTests(unittest.TestCase):
    def test_отмена_после_отключения_клиента_не_всплывает(self):
        async def receive():
            return {"type": "http.disconnect"}

        middleware = _load(False)(_cancelling_app)
        asyncio.run(middleware({"type": "http"}, receive, _noop_send))

    def test_отмена_при_живом_клиенте_пробрасывается(self):
        async def receive():
            return {"type": "http.request", "body": b""}

        middleware = _load(False)(_cancelling_app)
        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(middleware({"type": "http"}, receive, _noop_send))

    def test_при_остановке_отмена_гасится(self):
        async def receive():
            return {"type": "http.request", "body": b""}

        middleware = _load(True)(_cancelling_app)
        asyncio.run(middleware({"type": "http"}, receive, _noop_send))

    def test_остановка_по_состоянию_uvicorn_гасит_отмену(self):
        async def receive():
            return {"type": "http.request", "body": b""}

        middleware = _load_with_server_exit()(_cancelling_app)
        asyncio.run(middleware({"type": "http"}, receive, _noop_send))

    def test_lifespan_проходит_насквозь(self):
        seen = []

        async def app(scope, receive, send):
            seen.append(scope["type"])

        async def receive():
            return {"type": "lifespan.startup"}

        middleware = _load(False)(app)
        asyncio.run(middleware({"type": "lifespan"}, receive, _noop_send))
        self.assertEqual(seen, ["lifespan"])

    def test_сообщения_доходят_до_приложения_без_изменений(self):
        got = []

        async def app(scope, receive, send):
            got.append(await receive())

        async def receive():
            return {"type": "http.request", "body": b"payload"}

        middleware = _load(False)(app)
        asyncio.run(middleware({"type": "http"}, receive, _noop_send))
        self.assertEqual(got, [{"type": "http.request", "body": b"payload"}])


if __name__ == "__main__":
    unittest.main()

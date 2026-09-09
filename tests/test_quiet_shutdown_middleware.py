import asyncio
import unittest

from pathlib import Path
from types import SimpleNamespace


_LOGGER_STUB = SimpleNamespace(debug=lambda *args, **kwargs: None)


ROOT = Path(__file__).resolve().parent.parent
SOURCE = (ROOT / "api" / "main.py").read_text(encoding="utf-8")


def _load(shutting_down: bool):
    """Берёт глушитель отмены из api/main.py вместе с его определением состояния остановки."""
    start = SOURCE.index("_api_server = None")
    end = SOURCE.index('@app.on_event("shutdown")')
    namespace = {"asyncio": asyncio, "_shutting_down": shutting_down, "logger": _LOGGER_STUB}
    exec(SOURCE[start:end], namespace)
    return namespace["QuietShutdownMiddleware"]


def _load_with_server_exit():
    """Тот же глушитель, но остановку сообщает сам сервер uvicorn: событие shutdown выключено."""
    start = SOURCE.index("_api_server = None")
    end = SOURCE.index('@app.on_event("shutdown")')
    namespace = {"asyncio": asyncio, "_shutting_down": False, "logger": _LOGGER_STUB}
    exec(SOURCE[start:end], namespace)
    namespace["set_api_server"](SimpleNamespace(should_exit=True))
    return namespace["QuietShutdownMiddleware"]


async def _cancelling_app(scope, receive, send):
    await receive()
    raise asyncio.CancelledError()


async def _noop_send(message):
    return None


class Sent:
    """Собирает то, что приложение отправило клиенту."""

    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def __call__(self, message: dict) -> None:
        self.messages.append(message)


async def _answering_app(scope, receive, send):
    """Успел ответить и только потом был отменён."""
    await receive()
    await send({"type": "http.response.start", "status": 200, "headers": []})
    raise asyncio.CancelledError()


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

    def test_оборванный_запрос_закрывается_ответом(self):
        """Без ответа uvicorn пишет «ASGI callable returned without completing response»."""

        async def receive():
            return {"type": "http.request", "body": b""}

        sent = Sent()
        middleware = _load(True)(_cancelling_app)
        asyncio.run(middleware({"type": "http"}, receive, sent))

        self.assertEqual([m["type"] for m in sent.messages], ["http.response.start", "http.response.body"])
        self.assertEqual(sent.messages[0]["status"], 503)

    def test_уже_начатый_ответ_не_дополняется(self):
        async def receive():
            return {"type": "http.request", "body": b""}

        sent = Sent()
        middleware = _load(True)(_answering_app)
        asyncio.run(middleware({"type": "http"}, receive, sent))

        self.assertEqual([m["type"] for m in sent.messages], ["http.response.start"])
        self.assertEqual(sent.messages[0]["status"], 200)

    def test_отключившемуся_клиенту_ответ_не_ломает_остановку(self):
        async def receive():
            return {"type": "http.disconnect"}

        async def broken_send(message):
            raise RuntimeError("соединение закрыто")

        middleware = _load(False)(_cancelling_app)
        asyncio.run(middleware({"type": "http"}, receive, broken_send))

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

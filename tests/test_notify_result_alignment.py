import unittest

from unittest.mock import AsyncMock

import database  # noqa: F401  порядок импорта: иначе core.bootstrap уходит в цикл

from handlers.notifications.sender import FastNotificationSender


class _Bot:
    """Разная задержка на клиента."""

    def __init__(self) -> None:
        self.sent: list[int] = []

    async def send_message(self, chat_id: int, **kwargs: object) -> object:
        import asyncio

        await asyncio.sleep(0.01 if chat_id % 2 else 0.001)
        self.sent.append(chat_id)
        return object()

    send_photo = AsyncMock()


def _msg(tg_id: int | None) -> dict:
    return {"tg_id": tg_id, "text": "x", "notification_id": "inactive_trial"}


class ResultAlignmentTests(unittest.IsolatedAsyncioTestCase):
    """Отметка об отправке привязана к получателю."""

    async def _send(self, ids: list[int | None]) -> list[bool]:
        sender = FastNotificationSender(_Bot(), 1000)
        return await sender.send_all([_msg(i) for i in ids], workers=15)

    async def test_длина_совпадает_со_входом(self):
        ids = [1001, -96, 1002, None, 1003]
        self.assertEqual(len(await self._send(ids)), len(ids))

    async def test_недоставляемые_не_сдвигают_соседей(self):
        ids = [1001, -96, 1002, None, 1003]
        results = await self._send(ids)
        marked = [i for i, ok in zip(ids, results, strict=True) if ok]
        self.assertEqual(marked, [1001, 1002, 1003])

    async def test_веб_клиент_не_помечается_отправленным(self):
        results = await self._send([-96, -97])
        self.assertEqual(results, [False, False])

    async def test_пустой_список(self):
        self.assertEqual(await self._send([]), [])


if __name__ == "__main__":
    unittest.main()

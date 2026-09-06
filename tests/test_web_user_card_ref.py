import unittest

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import database  # noqa: F401  порядок импорта: иначе core.bootstrap уходит в цикл

from handlers.admin.users.users_manage import process_user_search
from handlers.start import parse_admin_user_ref


NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _result(**kwargs: object) -> SimpleNamespace:
    base = {
        "first": lambda: None,
        "scalar_one": lambda: 0,
        "scalar_one_or_none": lambda: None,
        "scalar": lambda: None,
        "scalars": lambda: SimpleNamespace(all=list, first=lambda: None),
        "all": list,
        "one_or_none": lambda: None,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


class _Session:
    """Минимум данных для карточки."""

    def __init__(self, user) -> None:
        self._user = user
        self._scalars = [user.id, user]
        self.first_call = True

    async def execute(self, stmt):
        if self.first_call:
            self.first_call = False
            return _result(first=lambda: ("web", 0, NOW, NOW, 0))
        return _result()

    async def scalar(self, stmt):
        return self._scalars.pop(0) if self._scalars else None


async def _card_ref(user):
    kb_mock = AsyncMock(return_value=Mock(inline_keyboard=[]))
    message = Mock(answer=AsyncMock(), edit_text=AsyncMock(), from_user=SimpleNamespace(id=1))
    with (
        patch("handlers.admin.users.users_manage.build_user_edit_kb", new=kb_mock),
        patch("handlers.admin.users.users_manage.menu_text", new=lambda *a, **k: "x"),
    ):
        await process_user_search(message, AsyncMock(), _Session(user), user.id, actor_tg_id=1)
    return kb_mock.await_args.args[0]


class WebUserCardRefTests(unittest.IsolatedAsyncioTestCase):
    """Кнопка несёт ref из базы: телеграмный, если он есть."""

    async def test_веб_клиент_получает_свой_синтетический_tg(self):
        user = SimpleNamespace(id=96, tg_id=-96, identity_id=None)
        self.assertEqual(await _card_ref(user), -96)

    async def test_обычный_клиент_получает_свой_tg(self):
        user = SimpleNamespace(id=77, tg_id=8119047207, identity_id=None)
        self.assertEqual(await _card_ref(user), 8119047207)

    async def test_клиент_без_tg_получает_внутренний_id(self):
        user = SimpleNamespace(id=88, tg_id=None, identity_id=None)
        self.assertEqual(await _card_ref(user), 88)


class SuserDeeplinkTests(unittest.TestCase):
    """Разбор /start suser_<ref>."""

    def test_положительный_принимается(self):
        self.assertEqual(parse_admin_user_ref("suser_96"), 96)

    def test_синтетический_со_знаком_минус_принимается(self):
        self.assertEqual(parse_admin_user_ref("suser_-96"), -96)

    def test_мусор_отбрасывается(self):
        for arg in ("--96", "-", "", "abc", "9-6"):
            self.assertIsNone(parse_admin_user_ref(f"suser_{arg}"), arg)

    def test_чужой_payload_не_перехватывается(self):
        self.assertIsNone(parse_admin_user_ref("gift_12345"))


if __name__ == "__main__":
    unittest.main()

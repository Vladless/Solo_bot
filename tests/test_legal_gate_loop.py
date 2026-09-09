import unittest

from unittest.mock import AsyncMock, patch

import database  # noqa: F401

from core.settings.legal_config import LEGAL_CONFIG
from handlers.utils import resolve_actor_data


BOT_ID = 8119047207
USER_ID = 476217106


class FakeTgUser:
    def __init__(self, uid: int, is_bot: bool, username: str | None = None) -> None:
        self.id = uid
        self.username = username
        self.first_name = "Bot" if is_bot else "Client"
        self.last_name = None
        self.language_code = "ru"
        self.is_bot = is_bot


class FakeBotMessage:
    """Сообщение бота: у него from_user — сам бот, как в реальном апдейте."""

    def __init__(self) -> None:
        self.from_user = FakeTgUser(BOT_ID, True, "service_bot")
        self.chat = FakeTgUser(USER_ID, False, "client")
        self.text = None
        self.caption = None


class FakeState:
    def __init__(self, data: dict | None = None) -> None:
        self.data = dict(data or {})

    async def update_data(self, **kw: object) -> None:
        self.data.update(kw)

    async def get_data(self) -> dict:
        return dict(self.data)

    async def clear(self) -> None:
        self.data.clear()


class ActorResolverTests(unittest.TestCase):
    """Сценарий старта не должен принимать бота за клиента: иначе гейты уходят в цикл."""

    def test_автор_сообщения_бот_отбрасывается(self):
        data = resolve_actor_data(FakeBotMessage())
        self.assertEqual(data["tg_id"], USER_ID)
        self.assertFalse(data["is_bot"])

    def test_явный_автор_нажатия_главнее(self):
        data = resolve_actor_data(FakeBotMessage(), actor=FakeTgUser(USER_ID, False, "client"))
        self.assertEqual(data["tg_id"], USER_ID)

    def test_бот_в_явном_авторе_тоже_отбрасывается(self):
        data = resolve_actor_data(FakeBotMessage(), actor=FakeTgUser(BOT_ID, True))
        self.assertEqual(data["tg_id"], USER_ID)

    def test_сохранённые_данные_переживают_очистку_состояния(self):
        saved = {"tg_id": 555, "username": "saved", "is_bot": False}
        data = resolve_actor_data(FakeBotMessage(), saved=saved)
        self.assertEqual(data["tg_id"], 555)

    def test_сохранённый_бот_не_используется(self):
        data = resolve_actor_data(FakeBotMessage(), saved={"tg_id": BOT_ID, "is_bot": True})
        self.assertEqual(data["tg_id"], USER_ID)


class LegalAcceptLoopTests(unittest.IsolatedAsyncioTestCase):
    """Нажатие «Принимаю» закрывает гейт и не создаёт клиента с номером бота."""

    async def asyncSetUp(self) -> None:
        self._legal_backup = dict(LEGAL_CONFIG)
        LEGAL_CONFIG.update({
            "LEGAL_DOCS_ENABLED": True,
            "LEGAL_PRIVACY_URL": "https://example.com/privacy",
            "LEGAL_TERMS_URL": "https://example.com/terms",
        })

    async def asyncTearDown(self) -> None:
        LEGAL_CONFIG.clear()
        LEGAL_CONFIG.update(self._legal_backup)

    async def _run_accept(self, state_data: dict):
        from handlers import legal, start

        accepted: set[int] = set()
        added: list[dict] = []
        gate_shown: list[int] = []

        class FakeSession:
            async def execute(self, *a: object, **k: object) -> None:
                accepted.add(USER_ID)

            async def commit(self) -> None:
                return None

        class FakeCallback:
            def __init__(self) -> None:
                self.from_user = FakeTgUser(USER_ID, False, "client")
                self.message = FakeBotMessage()
                self.data = "legal_accept"

            async def answer(self, *a: object, **k: object) -> None:
                return None

        async def add_user(session=None, **kw: object) -> None:
            added.append(kw)

        with (
            patch.object(start, "add_user", AsyncMock(side_effect=add_user)),
            patch.object(start, "check_user_exists", AsyncMock(side_effect=lambda s, tg: tg == USER_ID)),
            patch.object(legal, "legal_accepted", AsyncMock(side_effect=lambda s, tg: tg in accepted)),
            patch.object(legal, "show_legal_gate", AsyncMock(side_effect=lambda m: gate_shown.append(1))),
            patch.object(start, "get_or_load_user_snapshot", AsyncMock(return_value=(0, 0))),
            patch.object(start, "show_start_menu", AsyncMock()),
            patch.object(start, "process_callback_view_profile", AsyncMock()),
            patch.object(start, "run_hooks", AsyncMock()),
        ):
            await legal.accept_legal(FakeCallback(), FakeState(state_data), FakeSession(), False)
        return {"accepted": accepted, "added": added, "gate_shown": gate_shown}

    async def test_после_согласия_гейт_не_возвращается(self):
        result = await self._run_accept({})
        self.assertEqual(result["gate_shown"], [])
        self.assertIn(USER_ID, result["accepted"])

    async def test_клиент_с_номером_бота_не_создаётся(self):
        result = await self._run_accept({})
        self.assertEqual([row.get("tg_id") for row in result["added"]], [])

    async def test_очищенное_состояние_не_ломает_согласие(self):
        with_state = await self._run_accept({"user_data": {"tg_id": USER_ID, "is_bot": False}})
        self.assertEqual(with_state["gate_shown"], [])


if __name__ == "__main__":
    unittest.main()

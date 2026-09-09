import ast
import unittest

from pathlib import Path
from unittest.mock import AsyncMock, patch

from core.client_origin import (
    CLIENT_ORIGINS,
    ORIGIN_API,
    ORIGIN_BOT,
    ORIGIN_WEBAPP,
    client_campaign,
    client_origin,
    normalize_campaign,
    normalize_origin,
    set_client_campaign,
    set_client_origin,
)
from database.payments import _with_client_origin
from database.tracking_sources import attribute_source_if_known


ROOT = Path(__file__).resolve().parents[1]


class ClientOriginContextTests(unittest.TestCase):
    def tearDown(self):
        set_client_origin(ORIGIN_BOT)
        set_client_campaign(None)

    def test_канал_по_умолчанию_бот(self):
        set_client_origin(ORIGIN_BOT)
        self.assertEqual(client_origin(), ORIGIN_BOT)

    def test_известная_метка_принимается(self):
        self.assertEqual(set_client_origin("webapp"), ORIGIN_WEBAPP)
        self.assertEqual(client_origin(), ORIGIN_WEBAPP)

    def test_запрос_без_метки_не_становится_сайтом(self):
        self.assertEqual(set_client_origin(None), ORIGIN_API)
        self.assertEqual(set_client_origin("mars"), ORIGIN_API)

    def test_нормализация_канала(self):
        self.assertEqual(normalize_origin(" WEB "), "web")
        self.assertIsNone(normalize_origin("browser"))
        for origin in CLIENT_ORIGINS:
            self.assertEqual(normalize_origin(origin), origin)

    def test_нормализация_кода_источника(self):
        self.assertEqual(normalize_campaign(" promo_1 "), "promo_1")
        self.assertIsNone(normalize_campaign("плохой код"))
        self.assertIsNone(normalize_campaign("a" * 65))
        self.assertIsNone(normalize_campaign(None))

    def test_код_источника_живёт_в_контексте_запроса(self):
        self.assertEqual(set_client_campaign("summer"), "summer")
        self.assertEqual(client_campaign(), "summer")
        self.assertIsNone(set_client_campaign("bad code"))
        self.assertIsNone(client_campaign())


class OriginMiddlewareTests(unittest.TestCase):
    """Метка должна доезжать из middleware до самого хендлера, иначе всё запишется как «бот»."""

    def _probe_app(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from core.client_origin import ClientOriginMiddleware

        app = FastAPI()

        @app.get("/probe")
        async def probe():
            return {"origin": client_origin(), "campaign": client_campaign()}

        app.add_middleware(ClientOriginMiddleware)
        return TestClient(app)

    def tearDown(self):
        set_client_origin(ORIGIN_BOT)
        set_client_campaign(None)

    def test_метка_клиента_доезжает_до_хендлера(self):
        client = self._probe_app()
        body = client.get("/probe", headers={"X-Solo-Origin": "webapp", "X-Solo-Utm": "promo_3"}).json()
        self.assertEqual(body, {"origin": ORIGIN_WEBAPP, "campaign": "promo_3"})

    def test_запрос_без_меток_помечается_как_api(self):
        client = self._probe_app()
        body = client.get("/probe").json()
        self.assertEqual(body, {"origin": ORIGIN_API, "campaign": None})

    def test_api_ставит_метку_обычным_asgi_слоем(self):
        source = (ROOT / "api" / "main.py").read_text(encoding="utf-8")
        self.assertIn("app.add_middleware(ClientOriginMiddleware)", source)
        self.assertNotIn("async def client_origin_middleware", source)

    def test_шум_отмены_гасится_по_состоянию_uvicorn(self):
        source = (ROOT / "api" / "main.py").read_text(encoding="utf-8")
        self.assertIn("def shutting_down() -> bool:", source)
        self.assertIn('getattr(_api_server, "should_exit", False)', source)
        self.assertIn("if not (shutting_down() or disconnected):", source)
        self.assertIn("_close_unanswered(send)", source)
        app_source = (ROOT / "core" / "app.py").read_text(encoding="utf-8")
        self.assertIn("set_api_server(server)", app_source)


class PaymentOriginTests(unittest.TestCase):
    def tearDown(self):
        set_client_origin(ORIGIN_BOT)

    def test_канал_попадает_в_метаданные_платежа(self):
        set_client_origin("web")
        self.assertEqual(_with_client_origin(None)["origin"], "web")
        self.assertEqual(_with_client_origin({"payment_flow": "tariff_purchase"})["origin"], "web")

    def test_свой_канал_в_метаданных_сильнее_контекста(self):
        set_client_origin("web")
        self.assertEqual(_with_client_origin({"origin": "bot"})["origin"], "bot")

    def test_метаданные_не_мутируются(self):
        source = {"payment_flow": "gift_create"}
        _with_client_origin(source)
        self.assertNotIn("origin", source)

    def test_каждый_платёж_помечается_каналом(self):
        source = (ROOT / "database" / "payments.py").read_text(encoding="utf-8")
        self.assertIn("metadata = _with_client_origin(metadata)", source)


class WebhookOriginTests(unittest.IsolatedAsyncioTestCase):
    """Покупку подтверждает вебхук провайдера, и канал клиента там надо взять из платежа."""

    def tearDown(self):
        set_client_origin(ORIGIN_BOT)

    def test_канал_берётся_из_метаданных_платежа(self):
        from services.payments.pipeline import _adopt_payment_origin

        set_client_origin(ORIGIN_BOT)
        _adopt_payment_origin({"payment_flow": "tariff_purchase", "origin": "webapp"})
        self.assertEqual(client_origin(), ORIGIN_WEBAPP)

    def test_без_метки_в_платеже_канал_не_подменяется(self):
        from services.payments.pipeline import _adopt_payment_origin

        set_client_origin("web")
        _adopt_payment_origin(None, {}, {"origin": "мусор"})
        self.assertEqual(client_origin(), "web")

    def test_вебхук_надевает_канал_до_создания_ключа(self):
        source = (ROOT / "services" / "payments" / "pipeline.py").read_text(encoding="utf-8")
        adopt_at = source.index('_adopt_payment_origin(getattr(row, "metadata_", None)')
        notify_at = source.index("send_payment_success_notification(tg_id")
        self.assertLess(adopt_at, notify_at, "канал надо поставить раньше, чем создаётся подписка")


class SubscriptionSourceTests(unittest.TestCase):
    def test_журнал_подписок_пишет_реальный_канал(self):
        for rel in ("database/keys.py", "services/addons.py"):
            source = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("source=client_origin()", source, rel)
            self.assertNotIn('source="bot"', source, rel)


class SignupOriginTests(unittest.TestCase):
    def test_канал_регистрации_и_входа_сохраняются(self):
        identity = (ROOT / "database" / "models" / "identity.py").read_text(encoding="utf-8")
        self.assertIn("signup_origin = Column(String(16), nullable=True)", identity)
        session_model = (ROOT / "database" / "models" / "identity_session.py").read_text(encoding="utf-8")
        self.assertIn("origin = Column(String(16), nullable=True)", session_model)
        identities = (ROOT / "database" / "identities.py").read_text(encoding="utf-8")
        self.assertIn("signup_origin=client_origin()", identities)
        sessions = (ROOT / "database" / "identity_sessions.py").read_text(encoding="utf-8")
        self.assertIn("origin = client_origin()", sessions)

    def test_колонки_приходят_из_моделей_а_не_из_цепочки(self):
        """Колонки заводит сравнение моделей со схемой: ручного шага для них больше нет."""
        migrations = (ROOT / "database" / "migrations" / "schema_upgrade.py").read_text(encoding="utf-8")
        self.assertNotIn("signup_origin", migrations)
        self.assertNotIn("identity_sessions ADD COLUMN IF NOT EXISTS origin", migrations)


class CampaignAttributionTests(unittest.IsolatedAsyncioTestCase):
    async def test_неизвестный_код_не_пишется(self):
        with patch("database.tracking_sources.is_known_tracking_source", AsyncMock(return_value=False)) as known:
            with patch("database.tracking_sources.upsert_source_if_empty", AsyncMock()) as upsert:
                self.assertFalse(await attribute_source_if_known(object(), 1, "promo"))
        known.assert_awaited_once()
        upsert.assert_not_awaited()

    async def test_известный_код_ставится_один_раз(self):
        with patch("database.tracking_sources.is_known_tracking_source", AsyncMock(return_value=True)):
            with patch("database.tracking_sources.upsert_source_if_empty", AsyncMock(return_value=True)) as upsert:
                self.assertTrue(await attribute_source_if_known(object(), 42, " promo_1 "))
        upsert.assert_awaited_once()
        self.assertEqual(upsert.await_args.args[2], "promo_1")

    async def test_мусорный_код_не_доходит_до_базы(self):
        with patch("database.tracking_sources.is_known_tracking_source", AsyncMock()) as known:
            self.assertFalse(await attribute_source_if_known(object(), 1, "drop table"))
        known.assert_not_awaited()

    async def test_сайт_ставит_источник_как_диплинк_в_боте(self):
        starter = (ROOT / "handlers" / "start.py").read_text(encoding="utf-8")
        self.assertIn('attribute_source_if_known(session, user_data["tg_id"], utm_code)', starter)
        identities = (ROOT / "database" / "identities.py").read_text(encoding="utf-8")
        self.assertIn("_attribute_client_campaign", identities)


if __name__ == "__main__":
    unittest.main()

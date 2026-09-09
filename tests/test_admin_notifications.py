import unittest

from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

from core.client_origin import INVITE_PARTNER, INVITE_REFERRAL, INVITE_UTM, set_client_invite
from core.defaults import DEFAULT_NOTIFICATIONS_CONFIG
from handlers.admin.settings.keyboard import (
    build_settings_notifications_admin_kb,
    build_settings_notifications_kb,
)
from handlers.admin.settings.settings_config import ADMIN_NOTIFICATION_TITLES
from services.admin_notify import (
    build_client_keyboard,
    build_new_client_text,
    build_payment_text,
    notify_new_client,
    notify_payment,
    origin_label,
    resolve_attribution,
)


CARD = {
    "id": 219,
    "tg_id": 6611278769,
    "username": "vasya",
    "source_code": "promo_1",
    "email": "vasya@mail.ru",
    "signup_origin": "webapp",
}


class AdminNotificationSettingsTests(unittest.TestCase):
    def test_тумблеры_есть_в_дефолтах_и_выключены(self):
        for key in ADMIN_NOTIFICATION_TITLES:
            self.assertIn(key, DEFAULT_NOTIFICATIONS_CONFIG, key)
            self.assertFalse(DEFAULT_NOTIFICATIONS_CONFIG[key], key)

    def test_подменю_показывает_оба_тумблера(self):
        markup = build_settings_notifications_admin_kb({"ADMIN_NEW_USER_ENABLED": True})
        rows = [[b.text for b in row] for row in markup.inline_keyboard]
        self.assertEqual(rows[0], ["✅ Новый пользователь"])
        self.assertEqual(rows[1], ["❌ Успешная оплата"])
        self.assertIn("Назад", rows[-1][0])

    def test_вход_в_подменю_из_уведомлений(self):
        markup = build_settings_notifications_kb({})
        actions = [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]
        self.assertTrue(any("settings_notifications_admin" in a for a in actions))


class AdminNotificationTextTests(unittest.TestCase):
    def test_канал_подписан_словами(self):
        self.assertEqual(origin_label("webapp"), "Telegram WebApp")
        self.assertEqual(origin_label("bot"), "бот")
        self.assertEqual(origin_label("web"), "сайт")
        self.assertEqual(origin_label("api"), "неизвестно")
        self.assertEqual(origin_label(None), "неизвестно")

    def test_новый_клиент_с_каналом_и_меткой(self):
        text = build_new_client_text(CARD, origin="webapp", site_enabled=True)
        self.assertIn("Новый пользователь", text)
        self.assertIn("Telegram WebApp", text)
        self.assertIn("@vasya", text)
        self.assertIn("promo_1", text)

    def test_оплата_показывает_откуда_платили(self):
        text = build_payment_text(CARD, amount=300, payment_system="YOOKASSA", origin="web", site_enabled=True)
        self.assertIn("Успешная оплата", text)
        self.assertIn("300 ₽", text)
        self.assertIn("YOOKASSA", text)
        self.assertIn("сайт", text)

    def test_без_сайта_почты_в_уведомлении_нет(self):
        with_site = build_new_client_text(CARD, origin="web", site_enabled=True)
        without_site = build_new_client_text(CARD, origin="bot", site_enabled=False)
        self.assertIn("vasya@mail.ru", with_site)
        self.assertNotIn("vasya@mail.ru", without_site)
        self.assertIn("@vasya", without_site)

    def test_клиент_без_ника_и_почты_не_остаётся_пустым(self):
        text = build_new_client_text({"id": 5, "tg_id": None}, origin="web", site_enabled=True)
        self.assertIn("👤 клиент №5", text)
        self.assertNotIn(" ·  · ", text)

    def test_уведомление_читается_сразу_и_не_раздуто(self):
        """Сумма и касса — первой строкой после заголовка, а всё уведомление короткое."""
        text = build_payment_text(
            CARD,
            amount=609,
            payment_system="YOOKASSA",
            origin="bot",
            site_enabled=True,
            payment_id="3233a85c-000f",
            internal_id=5944,
        )
        lines = [line for line in text.split("\n") if line]
        self.assertTrue(lines[0].startswith("💰 <b>Успешная оплата</b>"))
        self.assertEqual(lines[1], "💵 <b>609 ₽</b> · YOOKASSA · бот")
        self.assertLessEqual(len(lines), 5)
        self.assertNotIn("<blockquote>", text)
        self.assertNotIn("├", text)

    def test_новый_клиент_тоже_короткий(self):
        text = build_new_client_text(CARD, origin="bot", site_enabled=True, attribution=None)
        self.assertLessEqual(len([line for line in text.split("\n") if line]), 4)
        self.assertNotIn("<blockquote>", text)


class AdminNotificationKeyboardTests(unittest.TestCase):
    def test_с_сайтом_две_кнопки(self):
        with patch("services.admin_notify._site", return_value=(True, "https://solonet.ru")):
            markup = build_client_keyboard(219, 6611278769)
        rows = [[b.text for b in row] for row in markup.inline_keyboard]
        self.assertEqual(rows, [["Открыть в боте"], ["Открыть на сайте"]])
        site_button = markup.inline_keyboard[1][0]
        self.assertEqual(site_button.url, "https://solonet.ru/admin/users?ref=6611278769")

    def test_без_сайта_одна_кнопка(self):
        with patch("services.admin_notify._site", return_value=(False, "")):
            markup = build_client_keyboard(219, 6611278769)
        self.assertEqual([[b.text for b in row] for row in markup.inline_keyboard], [["Открыть в боте"]])

    def test_кнопка_бота_ведёт_в_карточку_клиента(self):
        with patch("services.admin_notify._site", return_value=(False, "")):
            markup = build_client_keyboard(219, None)
        data = markup.inline_keyboard[0][0].callback_data
        self.assertIn("users_editor", data)
        self.assertIn("219", data)


class AdminNotificationDetailTests(unittest.TestCase):
    """Админ должен опознать событие по уведомлению: кто, когда и по какому счёту."""

    def test_новый_клиент_показывает_номер_и_время(self):
        text = build_new_client_text(CARD, origin="bot", site_enabled=True, attribution=None)
        self.assertIn("клиент №219", text)
        self.assertRegex(text, r"🕐 \d{2}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2}")

    def test_оплата_показывает_оба_номера_и_время(self):
        text = build_payment_text(
            CARD,
            amount=548,
            payment_system="YOOKASSA",
            origin="web",
            site_enabled=True,
            payment_id="2f1a9c7b-0001",
            internal_id=1114,
        )
        self.assertIn("1114", text)
        self.assertIn("2f1a9c7b-0001", text)
        self.assertIn("219", text)
        self.assertRegex(text, r"\d{2}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2}")

    def test_без_счёта_кассы_остаётся_только_наш_номер(self):
        text = build_payment_text(
            CARD, amount=100, payment_system="stars", origin="bot", site_enabled=False, internal_id=7
        )
        self.assertIn("🧾 платёж №7", text)
        self.assertNotIn("—", text)

    def test_совсем_без_номеров_строки_счёта_нет(self):
        text = build_payment_text(CARD, amount=100, payment_system="stars", origin="bot", site_enabled=False)
        self.assertNotIn("🧾", text)

    def test_длинный_счёт_кассы_копируется_целиком(self):
        text = build_payment_text(
            CARD,
            amount=548,
            payment_system="YOOKASSA",
            origin="web",
            site_enabled=True,
            payment_id="2f1a9c7b-0001-5000-8000-1d2e3f4a5b6c",
            internal_id=1114,
        )
        self.assertIn("🧾 платёж №1114 · <code>2f1a9c7b-0001-5000-8000-1d2e3f4a5b6c</code>", text)

    def test_дробная_сумма_не_теряет_копейки(self):
        text = build_payment_text(CARD, amount=609.5, payment_system="stars", origin="bot", site_enabled=False)
        self.assertIn("<b>609.50 ₽</b>", text)


class AdminNotificationContactTests(unittest.TestCase):
    """Связаться с клиентом — одно нажатие из уведомления, поэтому контакт стоит под заголовком."""

    def test_ник_ведёт_в_telegram(self):
        text = build_new_client_text(CARD, origin="bot", site_enabled=True, attribution=None)
        client_row = next(line for line in text.split("\n") if line.startswith("👤"))
        self.assertIn('<a href="https://t.me/vasya">@vasya</a>', client_row)
        self.assertIn('<a href="tg://user?id=6611278769">tg 6611278769</a>', client_row)

    def test_без_ника_ведёт_на_почту(self):
        card = {**CARD, "username": None}
        text = build_new_client_text(card, origin="web", site_enabled=True, attribution=None)
        self.assertIn('<a href="mailto:vasya@mail.ru">vasya@mail.ru</a>', text)

    def test_без_ника_и_почты_ведёт_по_номеру_telegram(self):
        card = {**CARD, "username": None, "email": None}
        text = build_new_client_text(card, origin="bot", site_enabled=False, attribution=None)
        self.assertIn('<a href="tg://user?id=6611278769">tg 6611278769</a>', text)

    def test_совсем_без_контактов_строки_нет(self):
        card = {**CARD, "username": None, "email": None, "tg_id": None}
        text = build_new_client_text(card, origin="web", site_enabled=True, attribution=None)
        self.assertNotIn("<a href=", text)

    def test_в_уведомлении_об_оплате_контакт_тоже_есть(self):
        text = build_payment_text(
            CARD, amount=100, payment_system="stars", origin="bot", site_enabled=True, internal_id=7
        )
        self.assertIn('<a href="https://t.me/vasya">@vasya</a>', text)


class AdminNotificationGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_выключенный_тумблер_ничего_не_шлёт(self):
        with patch("services.admin_notify._notifications_enabled", return_value=False):
            with patch("services.admin_notify.load_client_card", AsyncMock()) as loader:
                with patch("services.admin_notify.spawn") as sender:
                    await notify_new_client(object(), 1)
                    await notify_payment(object(), 1, amount=100, payment_system="YOOKASSA")
        loader.assert_not_awaited()
        sender.assert_not_called()

    async def test_включённый_тумблер_отправляет_один_раз(self):
        with patch("services.admin_notify._notifications_enabled", return_value=True):
            with patch("services.admin_notify.load_client_card", AsyncMock(return_value=CARD)):
                with patch("services.admin_notify._site", return_value=(False, "")):
                    with patch("services.admin_notify.spawn") as sender:
                        await notify_payment(object(), 219, amount=100, payment_system="stars", origin="bot")
        sender.assert_called_once()

    async def test_начисления_и_бонусы_не_считаются_оплатой(self):
        from database.payments import _notify_admins_payment

        with patch("services.admin_notify.notify_payment", AsyncMock()) as notifier:
            for system in ("admin", "daily_bonus", "referral", "coupon", "cashback"):
                await _notify_admins_payment(object(), 1, 100.0, system, None)
        notifier.assert_not_awaited()

    async def test_реальная_касса_уведомляет(self):
        from database.payments import _notify_admins_payment

        with patch("services.admin_notify.notify_payment", AsyncMock()) as notifier:
            await _notify_admins_payment(object(), 1, 100.0, "YOOKASSA", {"origin": "web"})
        notifier.assert_awaited_once()
        self.assertEqual(notifier.await_args.kwargs["origin"], "web")


if __name__ == "__main__":
    unittest.main()


class FakeResult:
    def __init__(self, row: object) -> None:
        self._row = row

    def first(self) -> object:
        return self._row


@dataclass
class FakeRow:
    id: int
    tg_id: int | None
    username: str | None


class FakeSession:
    """Сессия для резолвера привлечения: отдаёт заранее заданные ответы по порядку запросов."""

    def __init__(self, rows: list | None = None, partner_tg: int | None = None) -> None:
        self._rows = list(rows or [])
        self._partner_tg = partner_tg

    async def execute(self, *_args: object, **_kwargs: object) -> FakeResult:
        return FakeResult(self._rows.pop(0) if self._rows else None)

    async def scalar(self, *_args: object, **_kwargs: object) -> int | None:
        return self._partner_tg


class AdminNotificationAttributionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        set_client_invite(None, None)

    async def test_реферал_из_базы_с_именем_пригласившего(self):
        session = FakeSession(rows=[FakeRow(11, 555, "petrov")])
        attribution = await resolve_attribution(session, CARD)
        self.assertEqual(attribution["kind"], INVITE_REFERRAL)
        self.assertEqual(attribution["who"], "@petrov")

    async def test_партнёр_когда_записи_реферала_нет(self):
        session = FakeSession(rows=[None, FakeRow(12, 777, "ivan")], partner_tg=777)
        attribution = await resolve_attribution(session, CARD)
        self.assertEqual(attribution["kind"], INVITE_PARTNER)
        self.assertEqual(attribution["who"], "@ivan")

    async def test_приглашение_берётся_из_контекста_пока_записи_нет(self):
        set_client_invite(INVITE_REFERRAL, 555)
        session = FakeSession(rows=[None, FakeRow(11, 555, None)])
        attribution = await resolve_attribution(session, CARD)
        self.assertEqual(attribution["kind"], INVITE_REFERRAL)
        self.assertEqual(attribution["who"], "tg 555")

    async def test_метка_без_пригласившего(self):
        set_client_invite(INVITE_UTM, "promo_1")
        session = FakeSession(rows=[None])
        attribution = await resolve_attribution(session, CARD)
        self.assertEqual(attribution["kind"], INVITE_UTM)
        self.assertIsNone(attribution["who"])

    async def test_прямой_запуск_когда_ничего_нет(self):
        session = FakeSession(rows=[None])
        attribution = await resolve_attribution(session, {**CARD, "source_code": None})
        self.assertIsNone(attribution["kind"])

    def test_текст_показывает_вид_привлечения_и_кто_пригласил(self):
        text = build_new_client_text(
            CARD,
            origin="bot",
            site_enabled=True,
            attribution={"kind": INVITE_REFERRAL, "who": "@petrov"},
        )
        self.assertIn("🧭 реферал @petrov · метка promo_1 · бот", text)

    def test_прямой_запуск_подписан_словами(self):
        text = build_new_client_text(
            {**CARD, "source_code": None}, origin="bot", site_enabled=True, attribution={"kind": None, "who": None}
        )
        self.assertIn("🧭 прямой запуск · бот", text)

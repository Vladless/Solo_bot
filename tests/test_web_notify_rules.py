import ast
import unittest

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parent.parent
RULES_SOURCE = (ROOT / "services" / "web_notify_rules.py").read_text(encoding="utf-8")
LAYOUT_SOURCE = (ROOT / "database" / "web_layout.py").read_text(encoding="utf-8")
WEB_ROUTES = (ROOT / "api" / "v2" / "routes" / "web.py").read_text(encoding="utf-8")
CONSTRUCTOR_PAGE = (ROOT / "web-app" / "app" / "(marketing)" / "landing" / "ConstructorPage.tsx").read_text(
    encoding="utf-8"
)
ADMIN_SEGMENTS = (ROOT / "web-app" / "app" / "(marketing)" / "landing" / "adminPanelSegments.ts").read_text(
    encoding="utf-8"
)
ADMIN_PANEL = (ROOT / "web-app" / "app" / "(marketing)" / "landing" / "AdminPanel.tsx").read_text(encoding="utf-8")


def _load_function(source: str, name: str, extra_globals: dict | None = None):
    """Достаёт одну функцию из модуля, который нельзя импортировать целиком."""
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            namespace: dict = {"Any": Any, "urlencode": urlencode}
            namespace.update(extra_globals or {})
            exec(compile(ast.Module(body=[node], type_ignores=[]), filename=name, mode="exec"), namespace)
            return namespace[name]
    raise AssertionError(f"функция {name} не найдена")


def _load_rules_module() -> dict:
    """Модуль правил без импортов: базе и логгеру здесь делать нечего."""
    tree = ast.parse(RULES_SOURCE)
    body = [node for node in tree.body if not isinstance(node, ast.Import | ast.ImportFrom)]
    namespace: dict = {
        "Any": Any,
        "UTC": UTC,
        "datetime": datetime,
        "timedelta": timedelta,
        "urlencode": urlencode,
        "slug_to_path": _load_function(LAYOUT_SOURCE, "slug_to_path"),
        "Setting": object,
        "WebNotification": object,
        "AsyncSession": object,
        "select": None,
        "func": None,
        "logger": None,
    }
    exec(compile(ast.Module(body=body, type_ignores=[]), filename="web_notify_rules", mode="exec"), namespace)
    return namespace


RULES = _load_rules_module()


class NotifyRuleNormalizeTests(unittest.TestCase):
    """Правило приходит из браузера — принимаем только то, что умеем показать."""

    def test_без_заголовка_правило_отбрасывается(self):
        self.assertEqual(RULES["normalize_rules"]([{"message": "текст"}, {"title": "   "}]), [])

    def test_неизвестные_значения_заменяются_рабочими(self):
        rule = RULES["normalize_rules"]([
            {
                "title": "Подарок",
                "trigger": "перед сном",
                "audience": "кому-нибудь",
                "repeat": "иногда",
                "display": "везде",
            }
        ])[0]
        self.assertEqual(
            (rule["trigger"], rule["audience"], rule["repeat"], rule["display"]), ("login", "all", "once", "popup")
        )

    def test_одинаковые_id_разводятся(self):
        rules = RULES["normalize_rules"]([{"id": "gift", "title": "Раз"}, {"id": "gift", "title": "Два"}])
        self.assertEqual([r["id"] for r in rules], ["gift", "gift-2"])

    def test_число_правил_ограничено(self):
        rules = RULES["normalize_rules"]([{"title": f"Правило {i}"} for i in range(40)])
        self.assertEqual(len(rules), RULES["MAX_RULES"])

    def test_галочка_включения_сохраняется(self):
        rules = RULES["normalize_rules"]([{"title": "Раз", "enabled": False}, {"title": "Два"}])
        self.assertEqual([r["enabled"] for r in rules], [False, True])


class NotifyRuleHrefTests(unittest.TestCase):
    """Кнопка уведомления ведёт на страницу, вкладку кабинета, экран раздела и к блоку по якорю."""

    def href(self, target: dict) -> str:
        rule = RULES["normalize_rules"]([{"title": "Подарок", "target": target}])[0]
        return RULES["rule_href"](rule)

    def test_вкладка_и_якорь_кабинета(self):
        self.assertEqual(
            self.href({"page": "dashboard", "tab": "gifts", "anchor": "#prize"}), "/dashboard?tab=gifts#prize"
        )

    def test_на_своей_странице_вкладка_не_нужна(self):
        self.assertEqual(
            self.href({"page": "dashboard-profile", "tab": "profile", "anchor": "security"}),
            "/dashboard/profile#security",
        )

    def test_экран_раздела_передаётся_группой(self):
        self.assertEqual(
            self.href({"page": "dashboard-profile", "screen_group": "profile", "screen": "support"}),
            "/dashboard/profile?screen=profile%3Asupport",
        )

    def test_внешняя_ссылка_главнее_страницы(self):
        self.assertEqual(
            self.href({"page": "dashboard", "tab": "gifts", "url": "https://t.me/solo"}), "https://t.me/solo"
        )

    def test_лендинг_это_корень(self):
        self.assertEqual(self.href({"page": "landing", "anchor": "tariffs"}), "/#tariffs")

    def test_без_страницы_ведёт_в_кабинет(self):
        self.assertEqual(self.href({}), "/dashboard")


class NotifyRuleAudienceTests(unittest.TestCase):
    """Аудиторию правила проверяем до создания уведомления."""

    def ok(self, rule: dict, **kwargs) -> bool:
        normalized = RULES["normalize_rules"]([{**rule, "title": "Подарок"}])[0]
        return RULES["_audience_ok"](normalized, **kwargs)

    def test_всем(self):
        self.assertTrue(self.ok({}, has_subscription=True, source_code=None))

    def test_без_подписки(self):
        self.assertTrue(self.ok({"audience": "no_subscription"}, has_subscription=False, source_code=None))
        self.assertFalse(self.ok({"audience": "no_subscription"}, has_subscription=True, source_code=None))

    def test_с_подпиской(self):
        self.assertTrue(self.ok({"audience": "with_subscription"}, has_subscription=True, source_code=None))
        self.assertFalse(self.ok({"audience": "with_subscription"}, has_subscription=False, source_code=None))

    def test_по_метке_перехода(self):
        rule = {"audience": "source", "source_code": "Promo-TG"}
        self.assertTrue(self.ok(rule, has_subscription=False, source_code="promo-tg"))
        self.assertFalse(self.ok(rule, has_subscription=False, source_code="other"))
        self.assertFalse(self.ok({"audience": "source"}, has_subscription=False, source_code=""))


class NotifyRulePayloadTests(unittest.TestCase):
    """Страница получает готовый текст, подпись кнопки и адрес перехода."""

    def test_состав_полей(self):
        rule = RULES["normalize_rules"]([
            {
                "title": "Получите подарок",
                "message": "Заберите приз",
                "button_label": "Получить",
                "target": {"page": "dashboard", "tab": "gifts", "anchor": "prize"},
            }
        ])[0]
        payload = RULES["rule_payload"](rule)
        self.assertEqual(payload["title"], "Получите подарок")
        self.assertEqual(payload["button_label"], "Получить")
        self.assertEqual(payload["href"], "/dashboard?tab=gifts#prize")
        self.assertEqual(payload["display"], "popup")
        self.assertEqual(payload["target"]["anchor"], "prize")

    def test_уведомление_несёт_свой_id_для_отметки_прочтения(self):
        body = RULES_SOURCE[RULES_SOURCE.index("async def apply_rules_on_login") :]
        self.assertIn('fired.append({**payload, "notification_id": str(notification.id)})', body)
        self.assertIn('type="custom"', body)


class FirstLoginWindowTests(unittest.TestCase):
    """«Первый вход» — клиент зарегистрировался меньше суток назад."""

    def setUp(self):
        self.is_new = _load_function(
            WEB_ROUTES,
            "_identity_is_new",
            {
                "datetime": datetime,
                "timedelta": timedelta,
                "timezone": timezone,
                "FIRST_LOGIN_WINDOW": timedelta(hours=24),
            },
        )

    def test_свежая_регистрация(self):
        class Identity:
            created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)

        self.assertTrue(self.is_new(Identity()))

    def test_давний_клиент(self):
        class Identity:
            created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=9)

        self.assertFalse(self.is_new(Identity()))


class NotifyRuleWiringTests(unittest.TestCase):
    """Правила должны быть доступны админке и клиенту."""

    def test_маршруты_на_месте(self):
        self.assertIn('@router.get("/api/web/notify-rules")', WEB_ROUTES)
        self.assertIn('@router.put("/api/web/notify-rules")', WEB_ROUTES)
        self.assertIn('@router.post("/api/web/notify-rules/apply")', WEB_ROUTES)
        self.assertIn(
            "verify_identity_designer", WEB_ROUTES[WEB_ROUTES.index('@router.put("/api/web/notify-rules")') :][:600]
        )

    def test_вкладка_уведомлений_в_основном_разделе(self):
        self.assertIn('"notifyRules"', ADMIN_SEGMENTS)
        self.assertRegex(ADMIN_SEGMENTS, r'notifyRules: \{\s*label: "Уведомления"')
        self.assertIn('"variants", "packs", "notifyRules"', ADMIN_PANEL)
        self.assertIn("<AdminNotifyRulesTab />", ADMIN_PANEL)

    def test_повтор_считается_по_клиенту_а_не_по_платёжному_id(self):
        body = RULES_SOURCE[RULES_SOURCE.index("async def _already_sent") :]
        body = body[: body.index("async def apply_rules_on_login")]
        self.assertIn("WebNotification.identity_id == identity_id", body)
        self.assertNotIn("WebNotification.user_id", body)

    def test_клиент_определяется_каноническим_резолвером(self):
        body = WEB_ROUTES[WEB_ROUTES.index('@router.post("/api/web/notify-rules/apply")') :][:1200]
        self.assertIn("verify_identity_token", body)
        self.assertIn("ensure_billing_user_for_identity(session, identity)", body)
        self.assertIn("_identity_is_new(identity)", body)

    def test_смена_сегмента_анимирована(self):
        self.assertIn('<div key={adminMenuTab} className="ec-segment-enter flex flex-col gap-3">', ADMIN_PANEL)
        css = (ROOT / "web-app" / "app" / "styles" / "editor-design.css").read_text(encoding="utf-8")
        for name in ("ec-segment-enter", "ec-row-enter", "ec-row-leave"):
            self.assertIn(f"@keyframes {name}", css)

    def test_окно_уведомления_живёт_на_странице_клиента(self):
        self.assertIn("<NotifyRulesGate theme={theme} disabled={editMode} />", CONSTRUCTOR_PAGE)


if __name__ == "__main__":
    unittest.main()

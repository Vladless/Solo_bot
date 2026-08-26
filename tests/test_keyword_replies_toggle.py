import re
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
FALLBACK = (ROOT / "handlers" / "fallback_router.py").read_text(encoding="utf-8")
DEFAULTS = (ROOT / "core" / "defaults.py").read_text(encoding="utf-8")
TITLES = (ROOT / "handlers" / "admin" / "settings" / "settings_config.py").read_text(encoding="utf-8")
DESCRIPTIONS = (ROOT / "handlers" / "admin" / "settings" / "settings_descriptions.py").read_text(encoding="utf-8")

KEY = "KEYWORD_REPLIES_ENABLED"


_PIECES = (
    r"^_START_WORDS = [^\n]+$",
    r"^_BUY_WORDS = [^\n]+$",
    r"^def keyword_replies_enabled\(\) -> bool:\n    return bool\([^\n]+\)$",
    r"^def _match_keyword_intent\(.*?\n    return None\n",
)


def _load_matcher(modes_config: dict):
    """handlers.fallback_router тянет пол-бота — разбор слов чистый, берём его из файла."""
    namespace: dict = {"MODES_CONFIG": modes_config}
    for pattern in _PIECES:
        found = re.search(pattern, FALLBACK, re.S | re.M)
        assert found, pattern
        exec(found.group(0), namespace)
    return namespace["_match_keyword_intent"]


class RegistrationTests(unittest.TestCase):
    def test_тумблер_есть_в_режимах(self):
        self.assertIn(f'"{KEY}": True', DEFAULTS)
        self.assertIn(f'"{KEY}": "Горячие слова"', TITLES)
        self.assertIn(f'"{KEY}": "Отвечать на слова', DESCRIPTIONS)

    def test_по_умолчанию_включено(self):
        modes = DEFAULTS[DEFAULTS.index("DEFAULT_MODES_CONFIG") :]
        modes = modes[: modes.index("\n}")]
        self.assertIn(f'"{KEY}": True', modes, "поведение до тумблера — слова работали")

    def test_читается_по_контракту_тумблеров(self):
        self.assertIn(f'MODES_CONFIG or {{}}).get("{KEY}", True)', FALLBACK)


class GateTests(unittest.TestCase):
    def test_включённый_бот_отвечает_на_слова(self):
        match = _load_matcher({KEY: True})
        self.assertEqual(match("купить"), "buy")
        self.assertEqual(match("Тарифы"), "buy")
        self.assertEqual(match("/старт"), "start")
        self.assertEqual(match("привет"), "start")

    def test_выключенный_бот_не_реагирует_ни_на_одно_слово(self):
        match = _load_matcher({KEY: False})
        for text in ("купить", "тарифы", "старт", "привет", "buy", "menu"):
            self.assertIsNone(match(text), text)

    def test_посторонний_текст_и_так_не_ловится(self):
        self.assertIsNone(_load_matcher({KEY: True})("а когда будет скидка на год"))

    def test_отсутствие_ключа_в_конфиге_сохраняет_старое_поведение(self):
        self.assertEqual(_load_matcher({})("купить"), "buy")

    def test_проверка_стоит_до_разбора_текста(self):
        body = FALLBACK[FALLBACK.index("def _match_keyword_intent") :]
        body = body[: body.index("\n\n\n")]
        self.assertLess(body.index("keyword_replies_enabled()"), body.index("cleaned ="))


class FallbackStillWorksTests(unittest.TestCase):
    def test_выключенный_режим_не_отключает_сам_ответ_бота(self):
        handler = FALLBACK[FALLBACK.index("async def handle_unhandled_messages") :]
        self.assertIn("FALLBACK_MESSAGE", handler, "бот по-прежнему отвечает, просто без действий по словам")
        self.assertIn('run_hooks(\n        "user_message"', handler)


if __name__ == "__main__":
    unittest.main()

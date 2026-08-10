import re
import unittest

from handlers.utils import fill_text
from settings.texts import KEY_VIEW_TEXT


TAG_RE = re.compile(r"<\s*(/?)\s*([a-zA-Z]+)[^<>]*>")


def unbalanced_tags(text: str) -> list[str]:
    stack: list[str] = []
    problems: list[str] = []
    for match in TAG_RE.finditer(text):
        closing, name = match.group(1), match.group(2).lower()
        if not closing:
            stack.append(name)
        elif stack and stack[-1] == name:
            stack.pop()
        else:
            problems.append(f"лишний </{name}>")
    return problems + [f"<{name}> не закрыт" for name in stack]


def key_view(**overrides) -> str:
    values = {
        "key": "vless://example",
        "left": "5 дней",
        "expiry": "10.09.2026",
        "expired_at": "",
        "tariff_name": "Базовый",
        "traffic": "100 ГБ",
        "used": "12 ГБ",
        "devices": "3",
        "connected": "1",
        "country": "Германия",
    }
    values.update(overrides)
    return fill_text(KEY_VIEW_TEXT, **values)


class ScreenMarkupBalanceTests(unittest.TestCase):
    def test_активная_подписка_собирается_с_парными_тегами(self):
        self.assertEqual(unbalanced_tags(key_view()), [])

    def test_пустая_последняя_строка_блока_не_уносит_закрывающий_тег(self):
        text = key_view(country="")
        self.assertEqual(unbalanced_tags(text), [])
        self.assertNotIn("Локация", text)

    def test_истёкшая_подписка_собирается_с_парными_тегами(self):
        text = key_view(left="", expiry="", expired_at="10.08.2026", connected="")
        self.assertEqual(unbalanced_tags(text), [])
        self.assertIn("10.08.2026", text)

    def test_экран_без_данных_не_оставляет_открытых_блоков(self):
        empty = dict.fromkeys(
            ("key", "left", "expiry", "expired_at", "tariff_name", "traffic", "used", "devices", "connected", "country"),
            "",
        )
        self.assertEqual(unbalanced_tags(key_view(**empty)), [])

    def test_каждое_пустое_поле_проверяется_отдельно(self):
        fields = ("left", "expiry", "expired_at", "tariff_name", "traffic", "used", "devices", "connected", "country")
        for field in fields:
            with self.subTest(field=field):
                self.assertEqual(unbalanced_tags(key_view(**{field: ""})), [])


if __name__ == "__main__":
    unittest.main()

import re
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
START = (ROOT / "handlers" / "start.py").read_text(encoding="utf-8")
FALLBACK = (ROOT / "handlers" / "fallback_router.py").read_text(encoding="utf-8")
CONFIG = (ROOT / "handlers" / "admin" / "settings" / "settings_config.py").read_text(encoding="utf-8")


def _about_menu() -> str:
    start = START.index("async def handle_about_vpn")
    return START[start : START.index("await edit_or_send_message", start)]


class AboutSupportButtonTests(unittest.TestCase):
    def test_кнопка_смотрит_на_опросник_а_не_на_тикеты(self):
        block = _about_menu()
        self.assertIn('MODES_CONFIG.get("SUPPORT_TRIAGE_ENABLED", False)', block)
        self.assertNotIn('MODES_CONFIG.get("SUPPORT_TICKETS_ENABLED")', block)

    def test_с_выключенным_опросником_ведём_в_поддержку(self):
        block = _about_menu()
        self.assertIn("support_btn = await build_support_button()", block)
        триаж = block.index("TriageCallback(action=\"root\")")
        прямо = block.index("build_support_button()")
        self.assertLess(триаж, прямо, "опросник — только в ветке, где он включён")

    def test_поведение_совпадает_с_разделом_обращений(self):
        for source in (START, FALLBACK):
            self.assertIn('SUPPORT_TRIAGE_ENABLED", False)', source)
            self.assertIn("build_support_button()", source)

    def test_это_два_разных_тумблера(self):
        self.assertIn('"SUPPORT_TRIAGE_ENABLED": "Опросник поддержки"', CONFIG)
        self.assertIn('"SUPPORT_TICKETS_ENABLED": "Система тикетов"', CONFIG)

    def test_хелпер_импортирован(self):
        self.assertIn("build_support_button", re.search(r"^from \.utils import .+$", START, re.M).group(0))


if __name__ == "__main__":
    unittest.main()

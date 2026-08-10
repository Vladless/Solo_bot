import ast
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
START = (ROOT / "handlers" / "start.py").read_text(encoding="utf-8")
CONFIG = (ROOT / "handlers" / "admin" / "settings" / "settings_config.py").read_text(encoding="utf-8")
MARKUP = (ROOT / "handlers" / "notifications" / "webapp_only.py").read_text(encoding="utf-8")
SENDER = (ROOT / "handlers" / "notifications" / "sender.py").read_text(encoding="utf-8")
PAYMENTS = (ROOT / "handlers" / "payments" / "utils.py").read_text(encoding="utf-8")
RENEW = (ROOT / "handlers" / "keys" / "renew" / "flow.py").read_text(encoding="utf-8")


class WebappOnlyModeTests(unittest.TestCase):
    def test_режим_есть_в_списке_настроек(self):
        self.assertIn('"WEBAPP_ONLY_MODE": "Только веб-приложение"', CONFIG)

    def test_тумблер_читается_из_конфига_режимов(self):
        self.assertIn('MODES_CONFIG.get("WEBAPP_ONLY_MODE", False)', START)

    def test_проверка_стоит_до_сборки_обычного_меню(self):
        gate = START.index('MODES_CONFIG.get("WEBAPP_ONLY_MODE"')
        trial = START.index("show_trial = ")
        self.assertLess(gate, trial, "веб-режим должен отсекать сборку обычного меню")

    def test_на_стартовом_экране_только_одна_кнопка(self):
        tree = ast.parse(MARKUP)
        fn = next(
            node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "webapp_only_markup"
        )
        rows = sum(
            1
            for node in ast.walk(fn)
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "InlineKeyboardMarkup"
        )
        self.assertEqual(rows, 1, "клавиатура собирается один раз, из одного ряда")
        self.assertIn("inline_keyboard=[[button]]", MARKUP)

    def test_стартовый_экран_берёт_ту_же_клавиатуру(self):
        segment = START[START.index("async def show_webapp_only_start") : START.index("async def show_start_menu")]
        self.assertIn("webapp_only_markup()", segment)

    def test_медиа_остаётся(self):
        segment = START[START.index("async def show_webapp_only_start") : START.index("async def show_start_menu")]
        self.assertIn("media_path=image_path", segment)

    def test_без_включённого_сайта_режим_не_срабатывает(self):
        self.assertIn("if not is_web_enabled():", MARKUP)
        self.assertIn("if not site_url:", MARKUP)
        self.assertIn('if not MODES_CONFIG.get("WEBAPP_ONLY_MODE", False):', MARKUP)

    def test_одиночные_уведомления_идут_с_кнопкой_приложения(self):
        self.assertIn("keyboard = webapp_only_markup() or keyboard", SENDER)
        подмена = SENDER.index("keyboard = webapp_only_markup() or keyboard")
        отправка = SENDER.index("return await _send_text(bot, tg_id, caption, keyboard)")
        self.assertLess(подмена, отправка, "подмена должна стоять до отправки")

    def test_массовая_рассылка_тоже_подменяет_клавиатуру(self):
        segment = SENDER[SENDER.index("async def _send_one") : SENDER.index("async def _schedule_retry")]
        self.assertIn("webapp_markup = webapp_only_markup()", segment)
        self.assertIn('msg = {**msg, "keyboard": webapp_markup}', segment)

    def test_уведомление_о_пополнении_идёт_с_кнопкой_приложения(self):
        self.assertNotIn("reply_markup=builder.as_markup()", PAYMENTS)
        self.assertIn("reply_markup=webapp_only_markup() or builder.as_markup(),", PAYMENTS)

    def test_допы_и_подарки_после_оплаты_тоже_подменяются(self):
        self.assertNotIn("reply_markup=reply_markup)", PAYMENTS)
        self.assertEqual(PAYMENTS.count("webapp_only_markup() or reply_markup"), 2)

    def test_итог_продления_тоже_подменяется(self):
        self.assertIn("webapp_only_markup() or builder.as_markup()", RENEW)
        self.assertNotIn("reply_markup=builder.as_markup())", RENEW)

    def test_выключенный_режим_ничего_не_меняет(self):
        self.assertIn("return None", MARKUP)
        self.assertIn("or keyboard", SENDER)

    def test_сессия_освобождается_при_раннем_выходе(self):
        gate = START.index('MODES_CONFIG.get("WEBAPP_ONLY_MODE"')
        tail = START[gate : gate + 260]
        self.assertIn("release_session_early(session)", tail)
        self.assertIn("return", tail)


if __name__ == "__main__":
    unittest.main()

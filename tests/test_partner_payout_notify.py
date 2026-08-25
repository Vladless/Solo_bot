import re
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MENU = (ROOT / "modules" / "partner_program" / "handlers" / "admin" / "admin_partner_menu.py").read_text(
    encoding="utf-8"
)


def _handler(name: str) -> str:
    match = re.search(rf"^async def {name}\(.*?(?=^@admin_menu_router|\Z)", MENU, re.S | re.M)
    assert match, f"не найден обработчик {name}"
    return match.group(0)


class PayoutNotifyTests(unittest.TestCase):
    def test_решение_не_зависит_от_доставки_уведомления(self):
        for name in ("approve_payout", "reject_payout"):
            body = _handler(name)
            self.assertIn("_notify_partner(callback,", body, name)
            self.assertNotIn("await callback.bot.send_message(", body, f"{name}: голая отправка осталась")

    def test_веб_партнёру_не_шлём(self):
        helper = _handler("_notify_partner")
        self.assertIn("if not is_telegram_chat_id(tg_id):\n        return", helper)

    def test_сбой_отправки_гасится_и_логируется(self):
        helper = _handler("_notify_partner")
        self.assertIn("except Exception as error:", helper)
        self.assertIn("logger.warning", helper)

    def test_экран_админки_обновляется_после_уведомления(self):
        for name in ("approve_payout", "reject_payout"):
            body = _handler(name)
            notify = body.index("_notify_partner(callback,")
            screen = body.index("await callback.message.edit_text(")
            self.assertLess(notify, screen, f"{name}: экран должен обновляться после уведомления")

    def test_кнопка_профиля_осталась(self):
        self.assertIn("B.BTN_TO_PROFILE", _handler("_notify_partner"))


if __name__ == "__main__":
    unittest.main()

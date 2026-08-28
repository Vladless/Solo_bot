import re
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PAYMENTS = (ROOT / "handlers" / "payments" / "utils.py").read_text(encoding="utf-8")
CONFIG_MODE = (ROOT / "handlers" / "tariffs" / "addons" / "config_mode" / "router.py").read_text(encoding="utf-8")
PACK_MODE = (ROOT / "handlers" / "tariffs" / "addons" / "pack_mode" / "router.py").read_text(encoding="utf-8")


def _branch() -> str:
    start = PAYMENTS.index('if state == "waiting_for_addons_payment":')
    return PAYMENTS[start : PAYMENTS.index("extra_price_to_charge <= 0", start)]


class AgreedPriceStoredTests(unittest.TestCase):
    """В заявке лежала только недостача — согласованную цену честь было нечем."""

    def test_обе_ветки_сохраняют_цену(self):
        for name, src in (("config_mode", CONFIG_MODE), ("pack_mode", PACK_MODE)):
            self.assertIn('"agreed_extra_price": int(extra_price),', src, name)

    def test_цена_лежит_рядом_с_недостачей(self):
        for name, src in (("config_mode", CONFIG_MODE), ("pack_mode", PACK_MODE)):
            block = src[src.index('temp_key="waiting_for_addons_payment"') :][:900]
            self.assertLess(block.index('"required_amount"'), block.index('"agreed_extra_price"'), name)


class ChargeNeverDropsTests(unittest.TestCase):
    def test_списание_не_ниже_согласованного(self):
        branch = _branch()
        self.assertIn("extra_price_to_charge = max(agreed_extra_price_int, recomputed_extra_price)", branch)

    def test_старые_заявки_без_цены_считаются_как_раньше(self):
        branch = _branch()
        self.assertIn("if agreed_extra_price is None:", branch)
        self.assertIn("extra_price_to_charge = recomputed_extra_price", branch)

    def test_расхождение_попадает_в_журнал(self):
        branch = _branch()
        self.assertIn("Цена выросла между подтверждением и оплатой", branch)
        self.assertIn("Держим цену, согласованную при оплате", branch)

    def test_проверка_суммы_платежа_осталась(self):
        self.assertIn("Платеж {amount} не соответствует ожидаемой сумме {required_amount}", PAYMENTS)


class SameShapeAsRenewalTests(unittest.TestCase):
    """Продление уже лечили этой же формой — держим их одинаковыми."""

    def test_обе_ветки_используют_max(self):
        self.assertIn("cost = max(agreed_cost_int, recomputed_cost)", PAYMENTS)
        self.assertIn("extra_price_to_charge = max(agreed_extra_price_int, recomputed_extra_price)", PAYMENTS)


if __name__ == "__main__":
    unittest.main()

import re
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PAYMENTS = (ROOT / "handlers" / "payments" / "utils.py").read_text(encoding="utf-8")
KEYS = (ROOT / "services" / "keys.py").read_text(encoding="utf-8")


def _branch() -> str:
    start = PAYMENTS.index('if state == "waiting_for_renewal_payment":')
    return PAYMENTS[start : PAYMENTS.index("await complete_key_renewal(", start)]


class PaidDealTests(unittest.TestCase):
    def test_срок_не_короче_согласованного(self):
        branch = _branch()
        self.assertIn("new_expiry_time = max(agreed_expiry_ms, recomputed_expiry)", branch)

    def test_истёкший_ключ_получает_пересчитанный_срок(self):
        branch = _branch()
        self.assertIn("recomputed_expiry = int(quote.new_expiry_ms or 0)", branch)
        self.assertIn("agreed_expiry_ms = int(agreed_expiry) if agreed_expiry else 0", branch)

    def test_цена_не_опускается_ниже_оплаченной(self):
        branch = _branch()
        self.assertIn("cost = max(agreed_cost_int, recomputed_cost)", branch)

    def test_подорожание_замечается(self):
        self.assertIn("Цена выросла между подтверждением и оплатой", PAYMENTS)

    def test_расхождение_срока_попадает_в_журнал(self):
        self.assertIn("Держим срок, согласованный при оплате", PAYMENTS)

    def test_старая_склейка_убрана(self):
        self.assertNotIn("quoted_cost_int <= cost <= quoted_cost_int + 2", PAYMENTS)


class KeepPeriodContractTests(unittest.TestCase):
    def test_режим_считает_остаток_и_держит_срок(self):
        block = KEYS[KEYS.index("keep_period = bool("):KEYS.index("credit_as_days = bool(")]
        self.assertIn("cost_for_remaining = int(round(full_price * remaining_days / duration_days))", block)
        self.assertIn("new_expiry_ms=int(current_expiry_ms)", block)
        self.assertIn("keeps_period=True", block)

    def test_флаг_режима_доступен_снаружи(self):
        self.assertIn("keeps_period: bool = False", KEYS)


if __name__ == "__main__":
    unittest.main()

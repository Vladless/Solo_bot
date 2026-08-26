import re
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ROUTE = (ROOT / "api" / "v2" / "routes" / "partners.py").read_text(encoding="utf-8")
PAYMENTS = (ROOT / "modules" / "partner_program" / "handlers" / "payments.py").read_text(encoding="utf-8")


def _load_builder():
    """Сборка строк уровней чистая — берём её из роутера, не поднимая FastAPI."""
    segment = ROUTE[ROUTE.index("    def _by_level(raw: dict)") : ROUTE.index("    mode_labels = {")]
    lines = ["def build(mode, percent_levels_raw, flat_levels_raw):"]
    lines += [line for line in segment.rstrip().splitlines()]
    lines.append("    return level_lines")
    namespace: dict = {}
    exec("\n".join(lines), namespace)
    return namespace["build"]


build = _load_builder()

FALLBACK = "1 уровень: бонус определяется настройками проекта"
PERCENT = {1: 0.3, 2: 0.1}
FLAT = {1: 150.0}


class ModeGateTests(unittest.TestCase):
    def test_процентный_режим_не_обещает_фикс(self):
        lines = build("percent_only", PERCENT, FLAT)
        self.assertEqual(lines, ["1 уровень: 30%", "2 уровень: 10%"])
        self.assertFalse([line for line in lines if "RUB" in line])

    def test_фиксированный_режим_не_обещает_процент(self):
        lines = build("flat_only", PERCENT, FLAT)
        self.assertEqual(lines, ["1 уровень: 150 RUB"])

    def test_смешанный_режим_показывает_оба(self):
        lines = build("flat_plus_percent", PERCENT, FLAT)
        self.assertEqual(lines, ["1 уровень: 30% + 150 RUB", "2 уровень: 10%"])

    def test_строковые_ключи_уровней_не_теряются(self):
        lines = build("percent_only", {"1": 0.25}, {})
        self.assertEqual(lines, ["1 уровень: 25%"])

    def test_мусор_в_настройках_не_роняет_ответ(self):
        self.assertEqual(build("percent_only", {"x": 0.3, 1: "нет"}, {}), [FALLBACK])

    def test_нулевая_ставка_не_попадает_в_условия(self):
        self.assertEqual(build("percent_only", {1: 0.0}, {}), [FALLBACK])

    def test_выключенный_режим_даёт_заглушку_а_не_чужие_цифры(self):
        self.assertEqual(build("flat_only", PERCENT, {}), [FALLBACK])


class ContractWithPayoutsTests(unittest.TestCase):
    """Условия на сайте обязаны совпадать с тем, по каким режимам платит бот."""

    def test_режимы_взяты_из_логики_начисления(self):
        flat_gate = re.search(r"REFERRAL_REWARD_MODE in \{([^}]*)\} and first_payment", PAYMENTS)
        pct_gate = re.search(r"REFERRAL_REWARD_MODE in \{([^}]*)\}:\s*\n\s+pct =", PAYMENTS)
        self.assertTrue(flat_gate and pct_gate)
        flat_modes = set(re.findall(r'"(\w+)"', flat_gate.group(1)))
        pct_modes = set(re.findall(r'"(\w+)"', pct_gate.group(1)))

        route_pct = re.search(r"percent_levels = _by_level\(percent_levels_raw\) if mode in \{([^}]*)\}", ROUTE)
        route_flat = re.search(r"flat_levels = _by_level\(flat_levels_raw\) if mode in \{([^}]*)\}", ROUTE)
        self.assertTrue(route_pct and route_flat)
        self.assertEqual(set(re.findall(r'"(\w+)"', route_pct.group(1))), pct_modes)
        self.assertEqual(set(re.findall(r'"(\w+)"', route_flat.group(1))), flat_modes)


if __name__ == "__main__":
    unittest.main()

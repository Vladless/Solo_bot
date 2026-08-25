import re
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
START = (ROOT / "handlers" / "start.py").read_text(encoding="utf-8")


def _load_splitter():
    """Функция чистая, но handlers.start тянет за собой пол-бота — берём её из файла."""
    namespace: dict = {"re": re}
    const = re.search(r"^_START_DIRECTIVE_RE = .+$", START, re.M)
    assert const, "не найдена таблица директив"
    exec(const.group(0), namespace)

    body = re.search(r"^def _split_start_payload\(.*?\n    return parts\n", START, re.S | re.M)
    assert body, "не найдена функция разбора"
    exec(body.group(0), namespace)
    return namespace["_split_start_payload"]


split = _load_splitter()


class DashInCodeTests(unittest.TestCase):
    def test_код_партнёра_с_дефисом_не_режется(self):
        self.assertEqual(split("partner_AbCdEf-Gh"), ["partner_AbCdEf-Gh"])

    def test_настоящий_сгенерированный_код(self):
        self.assertEqual(split("partner_p1_i15DbA0TOGGwgUy-uGU"), ["partner_p1_i15DbA0TOGGwgUy-uGU"])

    def test_реферальный_код_с_дефисом_целый(self):
        self.assertEqual(split("referral_r1_3u05DDpPqEOSj58-8wM"), ["referral_r1_3u05DDpPqEOSj58-8wM"])

    def test_несколько_дефисов_подряд_в_коде(self):
        self.assertEqual(split("partner_aB-cD-eF"), ["partner_aB-cD-eF"])


class CombinedPayloadTests(unittest.TestCase):
    def test_склейка_директив_по_прежнему_делится(self):
        self.assertEqual(split("referral_123-utm_summer"), ["referral_123", "utm_summer"])

    def test_три_директивы(self):
        self.assertEqual(split("coupons_abc-referral_5-utm_x"), ["coupons_abc", "referral_5", "utm_x"])

    def test_директива_с_кодом_и_следующая_директива(self):
        self.assertEqual(split("partner_aB-cD-utm_x"), ["partner_aB-cD", "utm_x"])


class EdgeTests(unittest.TestCase):
    def test_пустой_payload(self):
        self.assertEqual(split(""), [])
        self.assertEqual(split(None), [])

    def test_payload_без_дефисов_не_меняется(self):
        self.assertEqual(split("partner_123"), ["partner_123"])

    def test_регистр_директивы_не_важен(self):
        self.assertEqual(split("partner_aB-UTM_x"), ["partner_aB", "UTM_x"])


class SourceGuardTests(unittest.TestCase):
    def test_ядро_больше_не_режет_payload_вслепую(self):
        self.assertNotIn('parts = text.split("-") if text else []', START)
        self.assertIn("parts = _split_start_payload(text)", START)

    def test_сигнатура_хука_не_поменялась(self):
        self.assertIn(
            'run_hooks("start_link", message=message, state=state, session=session, user_data=user_data, part=part)',
            START,
        )

    def test_все_директивы_ядра_учтены(self):
        directives = set(re.findall(r'if "(\w+)" in part:', START))
        pattern = re.search(r'_START_DIRECTIVE_RE = re\.compile\(r"\^\(\?:([^)]+)\)', START).group(1)
        known = set(pattern.split("|"))
        self.assertTrue(directives <= known, f"в разборе нет директив: {directives - known}")


if __name__ == "__main__":
    unittest.main()

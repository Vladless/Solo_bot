import re
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SERVICE = (ROOT / "handlers" / "payments" / "overpay" / "service.py").read_text(encoding="utf-8")


def _fn(name: str, end: str) -> str:
    match = re.search(rf"^def {name}\(.*?\n    {re.escape(end)}\n", SERVICE, re.S | re.M)
    assert match, name
    return match.group(0)


class PemOrderTests(unittest.TestCase):
    def test_ключ_идёт_после_цепочки(self):
        body = _fn("_pem_from_cryptography", "return bundle")
        ca = body.index("ca_cert.public_bytes")
        key = body.index("private_key.private_bytes")
        self.assertLess(ca, key, "CA после ключа openssl молча теряет")

    def test_лист_идёт_первым(self):
        body = _fn("_pem_from_cryptography", "return bundle")
        leaf = body.index("bundle = certificate.public_bytes")
        ca = body.index("ca_cert.public_bytes")
        self.assertLess(leaf, ca)


class FallbackTests(unittest.TestCase):
    def test_есть_запасной_разбор(self):
        self.assertIn("_pem_from_openssl(resolved, raw_password)", SERVICE)

    def test_сначала_штатный_путь(self):
        block = SERVICE[SERVICE.index("raw_password = OVERPAY_CERT_PASSWORD"):SERVICE.index("context = ssl.create_default_context()")]
        self.assertLess(block.index("_pem_from_cryptography"), block.index("_pem_from_openssl"))

    def test_пробуем_и_legacy_и_обычный(self):
        body = _fn("_pem_from_openssl", "return None")
        self.assertIn('for extra in (["-legacy"], []):', body)

    def test_пароль_не_уходит_в_аргументы(self):
        body = _fn("_pem_from_openssl", "return None")
        self.assertIn('"env:OVERPAY_P12_PASS"', body)
        self.assertNotIn("-passin\", f\"pass:", body)

    def test_отсутствие_openssl_не_роняет_бота(self):
        body = _fn("_pem_from_openssl", "return None")
        self.assertIn("except (OSError, subprocess.SubprocessError) as error:", body)

    def test_у_запуска_есть_таймаут(self):
        self.assertIn("timeout=20", _fn("_pem_from_openssl", "return None"))

    def test_причина_сбоя_попадает_в_журнал(self):
        self.assertIn("Штатный разбор .p12 не удался", SERVICE)
        self.assertIn("openssl тоже не смог разобрать .p12", SERVICE)


if __name__ == "__main__":
    unittest.main()

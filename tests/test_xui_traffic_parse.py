import asyncio
import re
import unittest

from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parent.parent
PANEL = (ROOT / "panels" / "_3xui.py").read_text(encoding="utf-8")
CONSUMER = (ROOT / "services" / "operations" / "traffic.py").read_text(encoding="utf-8")

LEGACY = "panel/api/inbounds/getClientTrafficsById/{key}"
MODERN = "panel/api/clients/traffic/{key}"


def _load():
    """panels/_3xui.py тянет за собой панель, поэтому разбор берём из файла."""
    namespace: dict = {
        "Any": object,
        "py3xui": SimpleNamespace(AsyncApi=object),
        "logger": SimpleNamespace(info=lambda *a: None, warning=lambda *a: None),
    }
    for name in ("_LEGACY_TRAFFIC_ENDPOINT", "_MODERN_TRAFFIC_ENDPOINT", "_modern_panels"):
        line = re.search(rf"^{name}[:\s].*$", PANEL, re.M)
        assert line, name
        exec(line.group(0), namespace)
    for pattern in (r"^def _traffic_rows\(.*?\n\n", r"^async def _fetch_traffic\(.*?\n\n"):
        body = re.search(pattern, PANEL, re.S | re.M)
        assert body, pattern
        exec(body.group(0), namespace)
    return namespace


NS = _load()


class FakePanel:
    """Отвечает только на маршрут своего поколения, как настоящая панель."""

    def __init__(self, supported: str, rows) -> None:
        self.supported, self.rows, self.calls = supported, rows, []
        self._host = "https://panel.example"

    def _url(self, endpoint):
        return f"{self._host}/{endpoint}"

    async def _get(self, url, headers):
        self.calls.append(url)
        route = url.replace(self._host + "/", "")
        expected = self.supported.format(key=route.rsplit("/", 1)[1])
        payload = {"success": True, "obj": self.rows} if route == expected else {"success": False, "obj": None}
        return SimpleNamespace(json=lambda: payload)


class RouteFallbackTests(unittest.TestCase):
    def setUp(self):
        NS["_modern_panels"].clear()
        self.fetch = NS["_fetch_traffic"]

    def _call(self, panel, endpoint, key):
        return asyncio.run(self.fetch(SimpleNamespace(client=panel), endpoint, key))

    def test_старая_панель_отвечает_по_uuid(self):
        panel = FakePanel(LEGACY, [{"up": 100, "down": 200}])
        self.assertEqual(self._call(panel, LEGACY, "uuid-1"), [{"up": 100, "down": 200}])

    def test_старый_маршрут_на_панели_36_молчит(self):
        panel = FakePanel(MODERN, {"up": 1, "down": 2})
        self.assertIsNone(self._call(panel, LEGACY, "uuid-1"))

    def test_панель_36_отвечает_по_email(self):
        panel = FakePanel(MODERN, {"up": 5, "down": 7})
        self.assertEqual(self._call(panel, MODERN, "user1"), [{"up": 5, "down": 7}])

    def test_одиночный_объект_становится_списком(self):
        self.assertEqual(NS["_traffic_rows"]({"up": 1}), [{"up": 1}])

    def test_мусор_в_ответе_отсекается(self):
        self.assertEqual(NS["_traffic_rows"]([{"up": 1}, "мусор", None]), [{"up": 1}])


class SourceGuardTests(unittest.TestCase):
    def test_маршруты_совпадают_с_панелью(self):
        self.assertIn(f'_LEGACY_TRAFFIC_ENDPOINT = "{LEGACY}"', PANEL)
        self.assertIn(f'_MODERN_TRAFFIC_ENDPOINT = "{MODERN}"', PANEL)

    def test_новый_маршрут_ищется_по_email(self):
        self.assertIn("_fetch_traffic(xui, _MODERN_TRAFFIC_ENDPOINT, email)", PANEL)
        self.assertIn("_fetch_traffic(xui, _LEGACY_TRAFFIC_ENDPOINT, client_id)", PANEL)

    def test_поколение_панели_запоминается(self):
        self.assertIn("_modern_panels.add(host)", PANEL)
        self.assertIn("_modern_panels.discard(host)", PANEL)

    def test_байты_считаются_по_полям_панели(self):
        self.assertIn('int(row.get("up") or 0) + int(row.get("down") or 0)', PANEL)

    def test_потребитель_передаёт_email_и_берёт_байты(self):
        self.assertIn("get_client_traffic(xui, client_id, email)", CONSUMER)
        self.assertIn('traffic_info.get("used_bytes")', CONSUMER)


if __name__ == "__main__":
    unittest.main()

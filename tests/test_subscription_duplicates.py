import unittest

from unittest.mock import AsyncMock, patch

import database  # noqa: F401

from services import subscriptions as subs


EMAIL = "client1"
ONE_PANEL_TWO_INBOUNDS = {
    "cluster1": [
        {
            "server_name": "Server 1",
            "api_url": "https://panel.example.com:54321",
            "subscription_url": "https://panel.example.com:2096/sub",
            "inbound_id": "1",
            "panel_type": "3x-ui",
            "enabled": True,
        },
        {
            "server_name": "Server 2",
            "api_url": "https://panel.example.com:54321",
            "subscription_url": "https://panel.example.com:2096/sub",
            "inbound_id": "2",
            "panel_type": "3x-ui",
            "enabled": True,
        },
    ]
}
TWO_PANELS = {
    "cluster1": [
        {
            "server_name": "DE",
            "api_url": "https://de.example.com:54321",
            "subscription_url": "https://de.example.com:2096/sub",
            "inbound_id": "1",
            "panel_type": "3x-ui",
            "enabled": True,
        },
        {
            "server_name": "NL",
            "api_url": "https://nl.example.com:54321",
            "subscription_url": "https://nl.example.com:2096/sub",
            "inbound_id": "1",
            "panel_type": "3x-ui",
            "enabled": True,
        },
    ]
}


class SubscriptionUrlsTests(unittest.IsolatedAsyncioTestCase):
    """Соседние inbound одной панели живут на общем адресе подписки — запрашиваем его один раз."""

    async def _urls(self, servers: dict, remnawave: str | None = None) -> list[str]:
        with (
            patch.object(subs, "get_servers", AsyncMock(return_value=servers)),
            patch.object(subs, "MODES_CONFIG", {}),
            patch.object(subs, "USE_COUNTRY_SELECTION", False),
            patch.object(subs, "RANDOM_SUBSCRIPTIONS", False),
        ):
            return await subs.get_subscription_urls("cluster1", EMAIL, None, include_remnawave_key=remnawave)

    async def test_общий_адрес_подписки_не_дублируется(self):
        urls = await self._urls(ONE_PANEL_TWO_INBOUNDS)
        self.assertEqual(urls, ["https://panel.example.com:2096/sub/client1"])

    async def test_разные_панели_собираются_обе(self):
        urls = await self._urls(TWO_PANELS)
        self.assertEqual(len(urls), 2)

    async def test_ссылка_remnawave_остаётся(self):
        urls = await self._urls(ONE_PANEL_TWO_INBOUNDS, remnawave="https://remna.example.com/sub/xyz")
        self.assertEqual(len(urls), 2)
        self.assertIn("https://remna.example.com/sub/xyz", urls)

    async def test_косая_черта_на_конце_не_создаёт_второй_адрес(self):
        servers = {
            "cluster1": [
                {**ONE_PANEL_TWO_INBOUNDS["cluster1"][0], "subscription_url": "https://panel.example.com:2096/sub"},
                {**ONE_PANEL_TWO_INBOUNDS["cluster1"][1], "subscription_url": "https://panel.example.com:2096/sub/"},
            ]
        }
        urls = await self._urls(servers)
        self.assertEqual(len(urls), 1)


class SubscriptionLinesTests(unittest.IsolatedAsyncioTestCase):
    """Панель отдаёт в подписи остаток трафика: он меняется, а точка та же — сравнивать надо адрес."""

    async def test_одна_точка_не_попадает_дважды_из_за_подписи(self):
        counter = {"n": 0}

        async def fetch(url, identifier):
            counter["n"] += 1
            n = counter["n"]
            return (
                [
                    f"vless://uuid-1@panel.example.com:443?type=tcp#DE-Inbound1-{n}0.5GB",
                    f"vless://uuid-2@panel.example.com:8443?type=tcp#NL-Inbound2-{n}1.5GB",
                ],
                {},
            )

        with (
            patch.object(subs, "SUPERNODE", False),
            patch.object(subs, "fetch_url_content", AsyncMock(side_effect=fetch)),
        ):
            lines, headers = await subs.combine_unique_lines(["https://a/sub/c", "https://a/sub/c"], EMAIL, "")
        self.assertEqual(len(lines), 2)
        self.assertEqual(len(headers), 2)

    async def test_разные_точки_остаются_обе(self):
        async def fetch(url, identifier):
            if "de" in url:
                return ["vless://de@de.example.com:443?type=tcp#DE"], {}
            return ["vless://nl@nl.example.com:443?type=tcp#NL"], {}

        with (
            patch.object(subs, "SUPERNODE", False),
            patch.object(subs, "fetch_url_content", AsyncMock(side_effect=fetch)),
        ):
            lines, _ = await subs.combine_unique_lines(["https://de/sub/c", "https://nl/sub/c"], EMAIL, "")
        self.assertEqual(len(lines), 2)

    async def test_супернода_берёт_один_адрес(self):
        calls: list[str] = []

        async def fetch(url, identifier):
            calls.append(url)
            return ["vless://uuid-1@panel.example.com:443?type=tcp#DE"], {}

        with (
            patch.object(subs, "SUPERNODE", True),
            patch.object(subs, "fetch_url_content", AsyncMock(side_effect=fetch)),
        ):
            lines, _ = await subs.combine_unique_lines(["https://a/sub/c", "https://b/sub/c"], EMAIL, "")
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(lines), 1)

    def test_подпись_не_участвует_в_сравнении(self):
        left = "vless://uuid@host:443?type=tcp#DE-10.5GB"
        right = "vless://uuid@host:443?type=tcp#DE-27.9GB"
        self.assertEqual(subs._config_identity(left), subs._config_identity(right))


if __name__ == "__main__":
    unittest.main()

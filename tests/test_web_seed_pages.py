import json
import unittest

from pathlib import Path

from database.web_default_seed import seed_worthy_slug
from database.web_layout import KNOWN_PAGE_SLUGS


ROOT = Path(__file__).resolve().parent.parent


class SeedPageFilterTests(unittest.TestCase):
    """Сайт заводит страницу на любой запрошенный путь — следы сканеров в поставку не идут."""

    def test_страница_с_блоками_идёт_в_сид(self):
        self.assertTrue(seed_worthy_slug("landing", True))
        self.assertTrue(seed_worthy_slug("какая-то-своя", True))

    def test_известный_маршрут_без_блоков_идёт_в_сид(self):
        for slug in ("faq", "tariffs", "register", "payment-success", "dashboard-referrals"):
            self.assertTrue(seed_worthy_slug(slug, False), slug)

    def test_следы_сканеров_отбрасываются(self):
        for slug in ("phpinfo", "wordpress", "dns-query", "graphql", "wsman", "nmaplowercheck1776629504"):
            self.assertFalse(seed_worthy_slug(slug, False), slug)

    def test_список_страниц_один_на_проект(self):
        api = (ROOT / "api" / "v2" / "routes" / "web.py").read_text(encoding="utf-8")
        seed = (ROOT / "database" / "web_default_seed.py").read_text(encoding="utf-8")
        self.assertIn("from database.web_layout import KNOWN_PAGE_SLUGS", seed)
        self.assertIn("KNOWN_PAGE_SLUGS", api)
        self.assertNotIn("KNOWN_PAGE_SLUGS = [", api)
        self.assertIn("landing", KNOWN_PAGE_SLUGS)


class SeedFileTests(unittest.TestCase):
    def test_снятый_сид_без_мусорных_страниц(self):
        path = ROOT / "static" / "default-seed-new.json"
        if not path.exists():
            self.skipTest("сид ещё не снят")
        seed = json.loads(path.read_text(encoding="utf-8"))
        pages = {k: v for k, v in seed.items() if not k.startswith("_") and isinstance(v, list)}
        for slug, blocks in pages.items():
            self.assertTrue(seed_worthy_slug(slug, bool(blocks)), slug)
        self.assertIn("landing", pages)
        self.assertIn("dashboard", pages)
        self.assertTrue(seed.get("_theme"))


if __name__ == "__main__":
    unittest.main()

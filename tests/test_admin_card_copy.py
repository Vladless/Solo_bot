import re
import unittest

from handlers.admin.panel.headers import align_screen, card, menu_text, section


CODE_RE = re.compile(r"<code>(.*?)</code>", re.S)
TAG_RE = re.compile(r"<[^>]+>")


def client_card() -> str:
    return card(
        section("👤 Данные", "TG ID: 2018209685", "Логин: @Petya_1620", "Почта: —"),
        section("💰 Деньги", "Баланс: 0 ₽", "Пополнил: 2324.0 ₽ (7)"),
        section("📈 Активность", "Пришёл: 02.08.25", "Активен: 30.07.26 10:40", "Пробный: использован"),
    )


class AdminCardCopyTests(unittest.TestCase):
    def test_каждое_значение_отдельный_моноблок(self):
        blocks = CODE_RE.findall(client_card())
        values = [b for b in blocks if not b.startswith(("├", "└"))]
        self.assertIn("2018209685", values)
        self.assertIn("@Petya_1620", values)
        self.assertIn("2324.0 ₽ (7)", values)

    def test_значение_не_слипается_с_меткой(self):
        for block in CODE_RE.findall(client_card()):
            self.assertFalse(
                "TG ID" in block and "2018209685" in block,
                f"метка и значение в одном блоке: {block!r}",
            )

    def test_метка_и_значение_разделены_одним_пробелом(self):
        rendered = client_card()
        pairs = re.findall(r"<code>([^<]*)</code> <code>([^<]*)</code>", rendered)
        self.assertTrue(pairs)
        for _label, value in pairs:
            self.assertNotIn("  ", value.strip())

    def test_колонки_выравнены_по_одной_вертикали(self):
        rendered = client_card()
        starts = set()
        for label, _ in re.findall(r"<code>([^<]*)</code> <code>([^<]*)</code>", rendered):
            starts.add(len(label))
        self.assertEqual(len(starts), 1, f"метки разной ширины: {starts}")

    def test_дерево_сохранено(self):
        rendered = client_card()
        self.assertIn("├", rendered)
        self.assertIn("└", rendered)

    def test_повторный_проход_ничего_не_меняет(self):
        once = client_card()
        self.assertEqual(align_screen(once), once)

    def test_экран_целиком_не_ломается(self):
        screen = menu_text("Клиент", "@Petya_1620", client_card())
        self.assertEqual(align_screen(screen), screen)
        self.assertIn("<code>2018209685</code>", screen)

    def test_ссылка_остаётся_одним_блоком(self):
        rendered = card(section("🌐 Кабинет", "<code>https://telegram.me/Bot?start=tab_keys</code>"))
        self.assertIn("<code>https://telegram.me/Bot?start=tab_keys</code>", rendered)

    def test_одинокая_строка_тоже_копируется(self):
        rendered = card(section("💰 Деньги", "Баланс: 0 ₽"))
        self.assertIn("<code>0 ₽</code>", rendered)


if __name__ == "__main__":
    unittest.main()

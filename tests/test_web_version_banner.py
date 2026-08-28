import re
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CLI = (ROOT / "cli_launcher.py").read_text(encoding="utf-8")


def _load():
    """Разбор версий чистый — берём его из CLI, не поднимая docker и сеть."""
    namespace: dict = {"re": re}
    exec(re.search(r"^_SEMVER_CLI_RE = re\.compile\((?:.|\n)*?\n\)\n", CLI, re.M).group(0), namespace)
    exec(
        re.search(
            r"^def _parse_solo_brick_semver\(.*?\n    return \(major, minor, patch, 0, tuple\(ids\)\)\n",
            CLI,
            re.S | re.M,
        ).group(0),
        namespace,
    )
    return namespace["_parse_solo_brick_semver"]


parse = _load()
TAGS = ["0.1.9", "0.2.0", "0.2.0-dev.115", "0.2.0-dev.117", "0.1.8-dev.3"]


def pick(channel: str):
    """Повторяет отбор из fetch_latest_ghcr_tag."""
    out = []
    for raw in TAGS:
        parsed = parse(raw)
        if parsed is None:
            continue
        prerelease = parsed[3] == 0
        if channel == "latest" and prerelease:
            continue
        if channel == "dev" and not prerelease:
            continue
        out.append((parsed, raw))
    if not out:
        return None
    out.sort(key=lambda item: item[0], reverse=True)
    return out[0][1]


class InstalledVersionTests(unittest.TestCase):
    """Версия бралась только у образа :latest — на канале dev его может не быть."""

    def test_сперва_спрашивают_контейнер(self):
        body = CLI[CLI.index("def read_installed_solo_brick_version") :]
        body = body[: body.index("\n\ndef ")]
        self.assertIn('_docker_label("container", WEB_CONTAINER_NAME)', body)
        self.assertLess(body.index('"container"'), body.index('"image"'))

    def test_образ_ищут_по_сохранённому_каналу(self):
        body = CLI[CLI.index("def read_installed_solo_brick_version") :]
        body = body[: body.index("\n\ndef ")]
        self.assertIn("saved_tag = _get_saved_web_tag()", body)
        self.assertIn('refs = [f"ghcr.io/{GHCR_IMAGE}:{saved_tag}"]', body)

    def test_проверяются_оба_канала_и_образ_без_тега(self):
        body = CLI[CLI.index("def read_installed_solo_brick_version") :]
        body = body[: body.index("\n\ndef ")]
        self.assertIn("for tag in WEB_TAG_CHOICES:", body)
        self.assertIn('refs.append(f"ghcr.io/{GHCR_IMAGE}")', body)

    def test_чтение_лейбла_вынесено_и_не_падает(self):
        helper = CLI[CLI.index("def _docker_label") : CLI.index("def read_installed_solo_brick_version")]
        self.assertIn("except Exception:\n        return None", helper)
        self.assertIn('label == "<no value>"', helper)


class RegistryChannelTests(unittest.TestCase):
    def test_на_стабильном_канале_предрелизы_не_предлагаются(self):
        self.assertEqual(pick("latest"), "0.2.0")

    def test_на_тестовом_канале_берётся_свежий_предрелиз(self):
        self.assertEqual(pick("dev"), "0.2.0-dev.117")

    def test_без_канала_поведение_прежнее(self):
        self.assertEqual(pick(""), "0.2.0")

    def test_баннер_передаёт_канал(self):
        body = CLI[CLI.index("def show_website_version_banner") :]
        self.assertIn("channel = _get_saved_web_tag()", body)
        self.assertIn("fetch_latest_ghcr_tag(GHCR_IMAGE, channel)", body)

    def test_пустая_выборка_откатывается_к_общему_списку(self):
        body = CLI[CLI.index("def fetch_latest_ghcr_tag") :]
        self.assertIn("if not versions and channel:\n            return fetch_latest_ghcr_tag(image)", body)


if __name__ == "__main__":
    unittest.main()

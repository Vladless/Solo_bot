import os
import re
import shutil
import tempfile
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CLI = (ROOT / "cli_launcher.py").read_text(encoding="utf-8")

TREE = (
    "core/redis_cache.py",
    "core/rpc.py",
    "core/__init__.py",
    "core/__pycache__/rpc.pyc",
    "core/settings/modes_config.py",
    "handlers/buttons.py",
    "settings/buttons.py",
    "settings/config.py",
    "settings/texts.py",
    "img/pic.jpg",
    "img/sub/logo.png",
    "modules/mine/router.py",
    "static/web_uploads/a.png",
    "bot.py",
)


class _FakeSubprocess:
    """sudo rm -rf выполняется настоящим удалением — иначе тест не увидит потерю файла."""

    @staticmethod
    def run(cmd, **_kwargs) -> None:
        if cmd[:3] == ["sudo", "rm", "-rf"]:
            shutil.rmtree(cmd[3], ignore_errors=True)
        return None


def _load_cleaner(project_dir: str):
    body = re.search(
        r'^def clean_project_dir_safe\(.*?\n                subprocess\.run\(\["sudo", "rm", "-rf", dir_path\]\)\n',
        CLI,
        re.S | re.M,
    )
    assert body, "не найдена очистка папки проекта"
    namespace: dict = {
        "os": os,
        "subprocess": _FakeSubprocess,
        "PROJECT_DIR": project_dir,
        "step_warn": lambda *a: None,
        "step_fail": lambda *a: None,
        "step_ok": lambda *a: None,
    }
    exec(body.group(0), namespace)
    return namespace["clean_project_dir_safe"]


def _excludes(update_buttons: bool, update_img: bool, update_redis_cache: bool) -> list[str]:
    body = re.search(r"^def _build_update_rsync_excludes\(.*?\n    return excludes\n", CLI, re.S | re.M)
    assert body, "не найден список исключений rsync"
    namespace: dict = {}
    exec(body.group(0), namespace)
    return namespace["_build_update_rsync_excludes"](update_buttons, update_img, update_redis_cache)


class CleanupPreservesFilesTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        for rel in TREE:
            path = os.path.join(self.root, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("x")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def _clean(self, **flags):
        _load_cleaner(self.root)(**flags)

    def _alive(self, rel: str) -> bool:
        return os.path.exists(os.path.join(self.root, rel))

    def test_всё_сохранить_значит_все_три_файла_на_месте(self):
        self._clean(update_buttons=False, update_img=False, update_redis_cache=False)
        self.assertTrue(self._alive("core/redis_cache.py"), "файл Redis снесён вместе с папкой core")
        self.assertTrue(self._alive("settings/buttons.py"))
        self.assertTrue(self._alive("handlers/buttons.py"))
        self.assertTrue(self._alive("img/pic.jpg"))
        self.assertTrue(self._alive("img/sub/logo.png"))

    def test_папка_core_переживает_очистку(self):
        self._clean(update_buttons=False, update_img=False, update_redis_cache=False)
        self.assertTrue(os.path.isdir(os.path.join(self.root, "core")))

    def test_разрешённая_перезапись_удаляет_файл_чтобы_приехал_свежий(self):
        self._clean(update_buttons=True, update_img=True, update_redis_cache=True)
        self.assertFalse(self._alive("core/redis_cache.py"))
        self.assertFalse(self._alive("settings/buttons.py"))
        self.assertFalse(self._alive("img/pic.jpg"))

    def test_конфиг_и_тексты_не_трогаются_никогда(self):
        self._clean(update_buttons=True, update_img=True, update_redis_cache=True)
        self.assertTrue(self._alive("settings/config.py"))
        self.assertTrue(self._alive("settings/texts.py"))

    def test_модули_и_загрузки_не_трогаются_никогда(self):
        self._clean(update_buttons=True, update_img=True, update_redis_cache=True)
        self.assertTrue(self._alive("modules/mine/router.py"))
        self.assertTrue(self._alive("static/web_uploads/a.png"))

    def test_остальной_код_удаляется_под_перезапись(self):
        self._clean(update_buttons=False, update_img=False, update_redis_cache=False)
        self.assertFalse(self._alive("bot.py"))
        self.assertFalse(self._alive("core/rpc.py"))
        self.assertFalse(self._alive("core/settings/modes_config.py"))


class CleanupMatchesRsyncTests(unittest.TestCase):
    """Что очистка бережёт, то rsync обязан исключить — иначе файл затрётся свежим."""

    def test_сохранённые_файлы_исключены_из_rsync(self):
        excludes = _excludes(False, False, False)
        for path in ("core/redis_cache.py", "settings/buttons.py", "handlers/buttons.py", "img"):
            self.assertIn(f"--exclude={path}", excludes)

    def test_разрешённая_перезапись_снимает_исключение(self):
        excludes = _excludes(True, True, True)
        for path in ("core/redis_cache.py", "settings/buttons.py", "img"):
            self.assertNotIn(f"--exclude={path}", excludes)

    def test_модули_и_загрузки_исключены_всегда(self):
        for flags in ((False, False, False), (True, True, True)):
            excludes = _excludes(*flags)
            self.assertIn("--exclude=modules", excludes)
            self.assertIn("--exclude=static/web_uploads", excludes)


class ProtectedDirsTests(unittest.TestCase):
    def test_непустая_папка_не_сносится_принудительно(self):
        block = CLI[CLI.index("for dir in dirs:") :]
        block = block[: block.index("\n\ndef ")]
        self.assertIn("if os.listdir(dir_path):\n                    continue", block)
        self.assertLess(block.index("os.listdir(dir_path)"), block.index('"rm", "-rf", dir_path'))


if __name__ == "__main__":
    unittest.main()

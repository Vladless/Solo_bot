import re
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CLI = (ROOT / "cli_launcher.py").read_text(encoding="utf-8")
BACKUP = (ROOT / "utils" / "backup.py").read_text(encoding="utf-8")


class ProjectBackupTests(unittest.TestCase):
    """venv пересобирается установкой зависимостей — держать его в копии незачем."""

    def test_список_пропускаемого_объявлен(self):
        self.assertIn('BACKUP_SKIP_DIRS = ("venv", "node_modules", ".git", "__pycache__")', CLI)

    def test_копия_делается_rsync_с_исключениями(self):
        body = CLI[CLI.index("def backup_project() -> str | None:") :]
        body = body[: body.index("\n\ndef ")]
        self.assertIn('f"--exclude={name}" for name in BACKUP_SKIP_DIRS', body)
        self.assertIn('["rsync", "-a", *excludes,', body)
        self.assertNotIn('"cp", "-r"', body, "cp -r копировал папку целиком")

    def test_rsync_ставится_перед_копированием(self):
        body = CLI[CLI.index("def backup_project() -> str | None:") :]
        body = body[: body.index("\n\ndef ")]
        self.assertLess(body.index("install_rsync_if_needed()"), body.index("subprocess.run"))

    def test_откат_не_сносит_пропущенное(self):
        """rsync --delete удалил бы venv, раз в копии его нет."""
        body = CLI[CLI.index("def _restore_backup_unattended") :]
        body = body[: body.index("\n\ndef ")]
        self.assertIn("*excludes", body)
        self.assertIn("BACKUP_SKIP_DIRS", body)
        self.assertIn('"--delete"', body)

    def test_обе_стороны_берут_один_список(self):
        self.assertEqual(CLI.count("for name in BACKUP_SKIP_DIRS"), 2)


class DumpFallbackTests(unittest.TestCase):
    """Архив не влез — админ должен понять, что с базой, а не гадать."""

    def _branch(self) -> str:
        start = BACKUP.index("if file_size > TELEGRAM_SEND_LIMIT:")
        return BACKUP[start : BACKUP.index("for target in targets:", start)]

    def test_ошибка_создания_дампа_объясняется(self):
        branch = self._branch()
        self.assertIn("Дамп базы не создан", branch)

    def test_слишком_большой_дамп_не_молчит_и_остаётся_на_диске(self):
        branch = self._branch()
        self.assertIn("Дамп базы тоже не влез", branch)
        self.assertIn("лежит рядом", branch)

    def test_сбой_чтения_объясняется(self):
        branch = self._branch()
        self.assertIn("Дамп базы прочитать не удалось", branch)

    def test_причина_попадает_в_сообщение(self):
        branch = self._branch()
        self.assertIn('blocks.append(section("🗄 База", db_note))', branch)
        self.assertIn("if db_note:", branch)

    def test_удаление_только_после_успешного_чтения(self):
        branch = self._branch()
        read_at = branch.index("aiofiles.open(db_path")
        unlink_at = branch.index("os.unlink(db_path)")
        self.assertLess(read_at, unlink_at, "дамп нельзя удалять, если он не отправлен")


if __name__ == "__main__":
    unittest.main()

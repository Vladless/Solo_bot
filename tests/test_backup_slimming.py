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


class DumpSplitTests(unittest.IsolatedAsyncioTestCase):
    """pg_dump -Fc уже сжат внутри, поэтому большой дамп можно только резать."""

    async def _send(self, size_mb: int, limit_mb: int = 49, max_parts: int | None = None):
        import os
        import tempfile
        from unittest.mock import AsyncMock

        import database  # noqa: F401 — модели не поднимутся первым импортом

        import utils.backup as backup

        saved = backup.MAX_DUMP_PARTS
        if max_parts is not None:
            backup.MAX_DUMP_PARTS = max_parts
        tmp = tempfile.NamedTemporaryFile(suffix=".sql", delete=False)
        tmp.write(b"x" * (size_mb * 1024 * 1024))
        tmp.close()
        bot = AsyncMock()
        try:
            sent = await backup._send_dump_in_parts(bot, [111], {}, tmp.name, limit_mb * 1024 * 1024)
        finally:
            backup.MAX_DUMP_PARTS = saved
            os.unlink(tmp.name)
        return sent, bot

    async def test_дамп_режется_на_части_под_лимит(self):
        sent, bot = await self._send(95)
        self.assertEqual(sent, 2)
        sizes = [len(c.kwargs["document"].data) for c in bot.send_document.await_args_list]
        self.assertEqual(sum(sizes), 95 * 1024 * 1024, "части должны складываться в исходный файл")
        self.assertTrue(all(size <= 49 * 1024 * 1024 for size in sizes))

    async def test_части_пронумерованы(self):
        _, bot = await self._send(95)
        names = [c.kwargs["document"].filename for c in bot.send_document.await_args_list]
        self.assertTrue(names[0].endswith(".part01"), names)
        self.assertTrue(names[1].endswith(".part02"), names)

    async def test_подпись_объясняет_как_собрать(self):
        _, bot = await self._send(95)
        caption = bot.send_document.await_args_list[0].kwargs["caption"]
        self.assertIn("Часть 1 из 2", caption)
        self.assertIn(".part*", caption)

    async def test_слишком_много_частей_не_шлём(self):
        sent, bot = await self._send(95, max_parts=1)
        self.assertEqual(sent, 0)
        self.assertEqual(bot.send_document.await_count, 0, "лучше оставить на сервере, чем засыпать чат")

    async def test_помещающийся_дамп_уходит_одной_частью(self):
        sent, bot = await self._send(20)
        self.assertEqual(sent, 1)
        self.assertEqual(bot.send_document.await_count, 1)


class SplitWiringTests(unittest.TestCase):
    def test_ветка_большого_дампа_пробует_резать(self):
        branch = BACKUP[BACKUP.index("elif os.path.getsize(db_path) > TELEGRAM_SEND_LIMIT:") :][:1400]
        self.assertIn("parts_sent = await _send_dump_in_parts(", branch)
        self.assertIn("if parts_sent:", branch)

    def test_если_резать_нельзя_остаётся_прежнее_сообщение(self):
        branch = BACKUP[BACKUP.index("elif os.path.getsize(db_path) > TELEGRAM_SEND_LIMIT:") :][:1400]
        self.assertIn("Дамп базы тоже не влез", branch)
        self.assertIn("лежит рядом", branch)


class DumpAsBackupTests(unittest.TestCase):
    """При BACKUP_CREATE_ARCHIVE=False бэкап и есть дамп — второй раз снимать его незачем."""

    def _branch(self) -> str:
        start = BACKUP.index("if file_size > TELEGRAM_SEND_LIMIT:")
        return BACKUP[start : BACKUP.index("for target in targets:", start)]

    def test_дамп_не_снимается_повторно(self):
        branch = self._branch()
        self.assertIn('already_dump = backup_file_path.endswith(".sql")', branch)
        self.assertIn("db_path, db_err = backup_file_path, None", branch)

    def test_исходный_файл_не_удаляется(self):
        branch = self._branch()
        self.assertIn("if not already_dump:", branch)
        self.assertLess(branch.index("if not already_dump:"), branch.index("os.unlink(db_path)"))

    def test_заголовок_называет_вещи_своими_именами(self):
        branch = self._branch()
        self.assertIn('"⚠️ Дамп в чат не влез." if already_dump else "⚠️ Архив в чат не влез."', branch)

    def test_подпись_блока_тоже_меняется(self):
        branch = self._branch()
        self.assertIn('section("📦 Дамп" if already_dump else "📦 Архив"', branch)


class DumpAlwaysSentTests(unittest.IsolatedAsyncioTestCase):
    """База уходит своим файлом и не зависит от того, сколько весит папка."""

    async def _run(self, dump_mb: int | None, limit_mb: int = 49):
        import os
        import tempfile
        from unittest.mock import AsyncMock, patch

        import database  # noqa: F401

        import utils.backup as backup

        if dump_mb is None:
            maker = lambda: (None, RuntimeError("pg_dump упал"))  # noqa: E731
            path = None
        else:
            tmp = tempfile.NamedTemporaryFile(suffix=".sql", delete=False)
            tmp.write(b"d" * (dump_mb * 1024 * 1024))
            tmp.close()
            path = tmp.name
            maker = lambda: (path, None)  # noqa: E731

        bot = AsyncMock()
        with patch.object(backup, "_create_database_backup", maker):
            await backup._send_dump_alongside(bot, "/tmp/arch.tar.gz", 555, None, limit_mb * 1024 * 1024)
        if path and os.path.exists(path):
            os.unlink(path)
        return bot, path

    async def test_небольшой_дамп_уходит_одним_файлом(self):
        bot, _ = await self._run(3)
        self.assertEqual(bot.send_document.await_count, 1)
        self.assertEqual(bot.send_document.await_args_list[0].kwargs["caption"], "Дамп базы")

    async def test_временный_файл_убирается(self):
        _, path = await self._run(3)
        import os

        self.assertFalse(os.path.exists(path))

    async def test_большой_дамп_уходит_частями(self):
        bot, _ = await self._run(95)
        names = [c.kwargs["document"].filename for c in bot.send_document.await_args_list]
        self.assertEqual(len(names), 2)
        self.assertTrue(all(".part" in name for name in names))

    async def test_сбой_снятия_не_ломает_отправку(self):
        bot, _ = await self._run(None)
        self.assertEqual(bot.send_document.await_count, 0)


class DumpAlongsideWiringTests(unittest.TestCase):
    def test_дамп_шлётся_до_архива(self):
        body = BACKUP[BACKUP.index("async def _send_backup_telegram") :]
        self.assertIn("await _send_dump_alongside(active_bot, backup_file_path", body)
        self.assertLess(body.index("_send_dump_alongside"), body.index("if file_size > TELEGRAM_SEND_LIMIT:"))

    def test_для_самого_дампа_дубля_не_будет(self):
        body = BACKUP[BACKUP.index("async def _send_backup_telegram") :]
        self.assertIn('not backup_file_path.endswith(".sql")', body)


if __name__ == "__main__":
    unittest.main()

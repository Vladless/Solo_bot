import re
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MODEL = (ROOT / "database" / "models" / "payments.py").read_text(encoding="utf-8")
MIGRATIONS = (ROOT / "database" / "migrations" / "schema_upgrade.py").read_text(encoding="utf-8")
SETUP = (ROOT / "database" / "setup" / "init_db.py").read_text(encoding="utf-8")


def _migration_body() -> str:
    start = MIGRATIONS.index("async def _migration_v52_payments_status_created_index")
    return MIGRATIONS[start : MIGRATIONS.index("_MIGRATIONS = [")]


class ModelTests(unittest.TestCase):
    def test_составной_индекс_объявлен_в_модели(self):
        self.assertIn('Index("ix_payments_status_created", "status", "created_at")', MODEL)

    def test_индекс_по_дате_отдельно(self):
        self.assertIn('Index("ix_payments_created", "created_at")', MODEL)

    def test_порядок_колонок_совпадает_с_запросами(self):
        """status сравнивают на равенство, created_at — диапазоном: равенство идёт первым."""
        args = MODEL[MODEL.index("__table_args__") : MODEL.index("id = Column")]
        composite = re.search(r'"ix_payments_status_created",\s*"(\w+)",\s*"(\w+)"', args)
        self.assertEqual(composite.groups(), ("status", "created_at"))


class MigrationTests(unittest.TestCase):
    def test_зарегистрирована_под_52(self):
        self.assertIn(
            '(52, "Индексы payments (status, created_at)", _migration_v52_payments_status_created_index)',
            MIGRATIONS,
        )

    def test_реестр_без_дублей_и_пропусков(self):
        block = MIGRATIONS[MIGRATIONS.index("_MIGRATIONS = [") :]
        block = block[: block.index("\n]")]
        nums = [int(n) for n in re.findall(r"^\s*\((\d+),", block, re.M)]
        self.assertEqual(nums, sorted(nums))
        self.assertEqual(len(nums), len(set(nums)))
        self.assertEqual(nums[-1], 52)

    def test_идемпотентна(self):
        body = _migration_body()
        self.assertIn('if await _index_exists(conn, "payments", index_name):', body)
        self.assertLess(body.index("_index_exists"), body.index("CREATE INDEX"))

    def test_не_падает_без_таблицы(self):
        body = _migration_body()
        self.assertIn('if not await _table_exists(conn, "payments"):', body)
        self.assertLess(body.index("_table_exists"), body.index("_index_exists"))

    def test_создаёт_оба_индекса(self):
        body = _migration_body()
        self.assertIn('("ix_payments_status_created", "status, created_at")', body)
        self.assertIn('("ix_payments_created", "created_at")', body)

    def test_без_concurrently(self):
        """Миграции идут внутри одной транзакции, а CONCURRENTLY там запрещён Postgres."""
        self.assertNotIn("CONCURRENTLY", _migration_body())
        self.assertIn("async with engine.begin() as conn:", SETUP)


class MigrationRuntimeTests(unittest.IsolatedAsyncioTestCase):
    """Миграция прогоняется с подменёнными проверками — важно поведение, а не текст."""

    async def _run(self, existing: set[str], table: bool = True) -> list[str]:
        from unittest.mock import AsyncMock, patch

        import database  # noqa: F401
        import database.migrations.schema_upgrade as su

        executed: list[str] = []
        conn = AsyncMock()
        conn.execute = AsyncMock(side_effect=lambda q, *a, **k: executed.append(str(q)) or AsyncMock())
        with (
            patch.object(su, "_table_exists", AsyncMock(return_value=table)),
            patch.object(su, "_index_exists", AsyncMock(side_effect=lambda c, t, i: i in existing)),
        ):
            await su._migration_v52_payments_status_created_index(conn)
        return [e for e in executed if "CREATE INDEX" in e]

    async def test_на_чистой_базе_создаёт_оба(self):
        created = await self._run(set())
        self.assertEqual(len(created), 2)
        self.assertIn("CREATE INDEX ix_payments_status_created ON payments (status, created_at)", created[0])

    async def test_повторный_запуск_ничего_не_делает(self):
        created = await self._run({"ix_payments_status_created", "ix_payments_created"})
        self.assertEqual(created, [])

    async def test_создаёт_только_недостающий(self):
        created = await self._run({"ix_payments_created"})
        self.assertEqual(len(created), 1)
        self.assertIn("ix_payments_status_created", created[0])

    async def test_без_таблицы_молча_выходит(self):
        self.assertEqual(await self._run(set(), table=False), [])


if __name__ == "__main__":
    unittest.main()

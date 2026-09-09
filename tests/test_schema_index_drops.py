import unittest

from unittest.mock import AsyncMock, patch

from database.migrations import schema_upgrade as su


class FakeResult:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple]:
        return self._rows

    def first(self) -> tuple | None:
        return self._rows[0] if self._rows else None


class FakeSavepoint:
    def __init__(self, log: list[str]) -> None:
        self._log = log

    async def __aenter__(self) -> "FakeSavepoint":
        self._log.append("savepoint")
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self._log.append("rollback" if exc_type else "release")
        return False


class FakeConn:
    """Соединение, которое пишет полученный SQL и отдаёт заранее заданные зависимости индекса."""

    def __init__(self, dependents: list[str], drop_error: Exception | None = None) -> None:
        self.dependents = dependents
        self.drop_error = drop_error
        self.sql: list[str] = []
        self.savepoints: list[str] = []

    def begin_nested(self) -> FakeSavepoint:
        return FakeSavepoint(self.savepoints)

    async def execute(self, statement: object, params: dict | None = None) -> FakeResult:
        sql = str(statement)
        self.sql.append(" ".join(sql.split()))
        if "pg_constraint" in sql:
            return FakeResult([(name,) for name in self.dependents])
        if sql.startswith("DROP INDEX") and self.drop_error is not None:
            raise self.drop_error
        return FakeResult([])


class IndexDropSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_занятый_индекс_остаётся(self):
        conn = FakeConn(["fk_keys_user", "fk_payments_user"])
        with (
            patch.object(su, "_table_exists", new=AsyncMock(return_value=True)),
            patch.object(su, "_index_exists", new=AsyncMock(return_value=True)),
        ):
            dropped = await su._drop_index_if_free(conn, "users", "ix_users_id")
        self.assertFalse(dropped)
        self.assertFalse([s for s in conn.sql if s.startswith("DROP INDEX")])

    async def test_свободный_индекс_снимается(self):
        conn = FakeConn([])
        with (
            patch.object(su, "_table_exists", new=AsyncMock(return_value=True)),
            patch.object(su, "_index_exists", new=AsyncMock(return_value=True)),
        ):
            dropped = await su._drop_index_if_free(conn, "users", "ix_users_tg_id")
        self.assertTrue(dropped)
        self.assertIn("DROP INDEX IF EXISTS ix_users_tg_id", conn.sql)

    async def test_отсутствующий_индекс_не_роняет(self):
        conn = FakeConn([])
        with (
            patch.object(su, "_table_exists", new=AsyncMock(return_value=True)),
            patch.object(su, "_index_exists", new=AsyncMock(return_value=False)),
        ):
            dropped = await su._drop_index_if_free(conn, "users", "ix_users_id")
        self.assertFalse(dropped)
        self.assertEqual(conn.sql, [])

    async def test_снятие_идёт_в_точке_сохранения(self):
        """Занятый индекс не должен рвать общую транзакцию миграций."""

        class Dependent(Exception):
            pgcode = "2BP01"

        class Wrapped(Exception):
            def __init__(self) -> None:
                super().__init__("cannot drop index")
                self.orig = Dependent()

        conn = FakeConn([], drop_error=Wrapped())
        with (
            patch.object(su, "_table_exists", new=AsyncMock(return_value=True)),
            patch.object(su, "_index_exists", new=AsyncMock(return_value=True)),
        ):
            dropped = await su._drop_index_if_free(conn, "users", "ix_users_tg_id")
        self.assertFalse(dropped)
        self.assertEqual(conn.savepoints, ["savepoint", "rollback"])

    async def test_чужая_ошибка_снятия_не_прячется(self):
        conn = FakeConn([], drop_error=RuntimeError("настоящая поломка"))
        with (
            patch.object(su, "_table_exists", new=AsyncMock(return_value=True)),
            patch.object(su, "_index_exists", new=AsyncMock(return_value=True)),
        ):
            with self.assertRaises(RuntimeError):
                await su._drop_index_if_free(conn, "users", "ix_users_tg_id")

    async def test_имя_индекса_проверяется_по_схеме_а_не_по_таблице(self):
        """Имена индексов в PostgreSQL общие: занятое имя не даст создать индекс на другой таблице."""
        conn = FakeConn([])
        await su._index_exists(conn, "users", "ix_users_tg_id")
        self.assertTrue(any("indexname = :i" in s for s in conn.sql))
        self.assertFalse(any("tablename = :t" in s for s in conn.sql))

    async def test_миграция_дублей_переживает_занятый_индекс(self):
        conn = FakeConn(["fk_keys_user"])
        with (
            patch.object(su, "_table_exists", new=AsyncMock(return_value=True)),
            patch.object(su, "_index_exists", new=AsyncMock(return_value=True)),
        ):
            await su._migration_v55_drop_duplicate_indexes(conn)
            await su._migration_v56_drop_low_cardinality_indexes(conn)
        self.assertFalse([s for s in conn.sql if s.startswith("DROP INDEX")])


if __name__ == "__main__":
    unittest.main()

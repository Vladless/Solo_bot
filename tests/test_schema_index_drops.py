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


class FakeConn:
    """Соединение, которое пишет полученный SQL и отдаёт заранее заданные зависимости индекса."""

    def __init__(self, dependents: list[str]) -> None:
        self.dependents = dependents
        self.sql: list[str] = []

    async def execute(self, statement: object, params: dict | None = None) -> FakeResult:
        sql = str(statement)
        self.sql.append(" ".join(sql.split()))
        if "pg_constraint" in sql:
            return FakeResult([(name,) for name in self.dependents])
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

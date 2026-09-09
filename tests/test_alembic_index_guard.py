import re
import unittest

from pathlib import Path

from alembic.operations import ops


ROOT = Path(__file__).resolve().parent.parent
INFRA = (ROOT / "core" / "infra.py").read_text(encoding="utf-8")


def _load_env_filter() -> dict:
    """Достаёт из шаблона alembic/env.py фильтр автогенерации и исполняет его отдельно."""
    start = INFRA.index('_LEGACY_TABLES = {"blocked_users"')
    end = INFRA.index("def process_revision_directives(", start)
    namespace: dict = {"ops": ops}
    exec(compile(INFRA[start:end], "<alembic-env-template>", "exec"), namespace)
    return namespace


class FakeTable:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeIndex:
    def __init__(self, table: str) -> None:
        self.table = FakeTable(table)


class AlembicIndexGuardTests(unittest.TestCase):
    """Индексы users и audit_events ведёт schema_upgrade: автогенерация их не снимает.

    Снятый автогенерацией индекс, на который опирается внешний ключ, роняет upgrade
    с DependentObjectsStillExist — на проде это уже случалось с ix_users_id и ix_users_tg_id.
    """

    def setUp(self) -> None:
        self.env = _load_env_filter()

    def test_снятие_индекса_users_не_попадает_в_ревизию(self):
        skip = self.env["_should_skip_op"]
        for index_name in ("ix_users_tg_id", "ix_users_id", "ix_users_created_at"):
            op = ops.DropIndexOp(index_name, "users")
            self.assertTrue(skip(op), index_name)

    def test_снятие_индекса_журнала_тоже_не_попадает(self):
        skip = self.env["_should_skip_op"]
        op = ops.DropIndexOp("ix_audit_events_channel", "audit_events")
        self.assertTrue(skip(op))

    def test_чужие_таблицы_автогенерация_обслуживает(self):
        skip = self.env["_should_skip_op"]
        op = ops.DropIndexOp("ix_keys_client_id", "keys")
        self.assertFalse(skip(op))

    def test_создание_индекса_users_остаётся_доступным(self):
        skip = self.env["_should_skip_op"]
        op = ops.CreateIndexOp("ix_users_preferred_currency", "users", ["preferred_currency"])
        self.assertFalse(skip(op))

    def test_отражённый_индекс_этих_таблиц_не_сравнивается(self):
        include = self.env["include_object"]
        self.assertFalse(include(FakeIndex("users"), "ix_users_tg_id", "index", True, None))
        self.assertFalse(include(FakeIndex("audit_events"), "ix_audit_events_channel", "index", True, None))

    def test_три_легаси_индекса_users_перечислены(self):
        self.assertEqual(
            self.env["_IGNORED_USERS_INDEXES"],
            {"ix_users_id", "ix_users_tg_id", "uq_users_tg_id"},
        )

    def test_снятие_индексов_в_миграциях_проверяет_зависимости(self):
        migrations = (ROOT / "database" / "migrations" / "schema_upgrade.py").read_text(encoding="utf-8")
        drops = re.findall(r'DROP INDEX[^"\']*', migrations)
        self.assertTrue(drops)
        for statement in drops:
            self.assertIn("IF EXISTS", statement)
        segment = migrations[migrations.index("async def _drop_index_if_free") :]
        self.assertIn("dependents = await _index_dependents(conn, index)", segment.split("DROP INDEX")[0])


if __name__ == "__main__":
    unittest.main()

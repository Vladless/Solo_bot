import unittest

from pathlib import Path

from sqlalchemy.dialects import postgresql

from alembic.operations import ops
from database.migrations.autogenerate import (
    ADDITIVE_OPS,
    SchemaReport,
    _safe_alter,
    can_widen,
    normalize_type,
)
from database.migrations.errors import DEPENDENCY_ERRORCODE, error_code, is_already_done, is_dependency_error


ROOT = Path(__file__).resolve().parent.parent


class FakeConn:
    """Соединение для проверки фильтра правок: диалект настоящий, данные — заданные."""

    def __init__(self, nulls: set[tuple[str, str]] | None = None) -> None:
        self.dialect = postgresql.dialect()
        self._nulls = nulls or set()
        self.asked: list[str] = []

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self.asked.append(sql)
        table = sql.split('"')[1]
        column = sql.split('"')[3]
        return FakeResult([(1,)] if (table, column) in self._nulls else [])


class FakeResult:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None


def _alter(**kwargs) -> ops.AlterColumnOp:
    base = {
        "modify_type": None,
        "modify_nullable": None,
        "modify_server_default": False,
        "existing_type": postgresql.VARCHAR(32),
        "existing_nullable": True,
        "existing_server_default": None,
    }
    base.update(kwargs)
    return ops.AlterColumnOp("keys", "client_id", **base)


class AutogenerateScopeTests(unittest.TestCase):
    """Автогенерация применяется только на добавление: снятие объектов — решение человека."""

    def test_добавляющие_операции_разрешены(self):
        for op_type in (
            ops.CreateTableOp,
            ops.AddColumnOp,
            ops.CreateIndexOp,
            ops.CreateUniqueConstraintOp,
            ops.CreateForeignKeyOp,
            ops.CreateCheckConstraintOp,
        ):
            self.assertIn(op_type, ADDITIVE_OPS, op_type.__name__)

    def test_разрушающие_операции_не_разрешены(self):
        for op_type in (
            ops.DropTableOp,
            ops.DropColumnOp,
            ops.DropIndexOp,
            ops.DropConstraintOp,
        ):
            self.assertNotIn(op_type, ADDITIVE_OPS, op_type.__name__)


class SafeAlterTests(unittest.TestCase):
    """Правку колонки пропускаем через фильтр: расширить можно, сузить и соврать про данные — нет."""

    def test_расширение_типа_применяется(self):
        op, note = _safe_alter(FakeConn(), _alter(modify_type=postgresql.VARCHAR(64)))
        self.assertEqual(note, "")
        self.assertIsNotNone(op)
        self.assertEqual(op.modify_type.length, 64)

    def test_сужение_типа_не_применяется(self):
        op, note = _safe_alter(
            FakeConn(), _alter(existing_type=postgresql.VARCHAR(128), modify_type=postgresql.VARCHAR(32))
        )
        self.assertIsNone(op)
        self.assertEqual(note, "")

    def test_лишняя_строгость_снимается(self):
        op, _ = _safe_alter(FakeConn(), _alter(modify_nullable=True, existing_nullable=False))
        self.assertIs(op.modify_nullable, True)

    def test_строгость_ставится_когда_данные_позволяют(self):
        conn = FakeConn()
        op, note = _safe_alter(conn, _alter(modify_nullable=False))
        self.assertIs(op.modify_nullable, False)
        self.assertEqual(note, "")
        self.assertTrue(conn.asked, "перед NOT NULL надо проверить данные")

    def test_с_пустыми_значениями_строгость_остаётся_человеку(self):
        op, note = _safe_alter(FakeConn(nulls={("keys", "client_id")}), _alter(modify_nullable=False))
        self.assertIsNone(op)
        self.assertIn("есть пустые значения", note)

    def test_значение_по_умолчанию_доводится_до_модели(self):
        op, note = _safe_alter(FakeConn(), _alter(modify_server_default="'web'"))
        self.assertEqual(op.modify_server_default, "'web'")
        self.assertEqual(note, "")

    def test_пустая_правка_не_выполняется(self):
        op, note = _safe_alter(FakeConn(), _alter())
        self.assertIsNone(op)
        self.assertEqual(note, "")


class TypeTests(unittest.TestCase):
    def test_правила_расширения(self):
        self.assertTrue(can_widen("integer", "bigint"))
        self.assertTrue(can_widen("json", "jsonb"))
        self.assertTrue(can_widen("timestamp without time zone", "timestamp with time zone"))
        self.assertTrue(can_widen("character varying(32)", "character varying(64)"))
        self.assertTrue(can_widen("character varying(32)", "character varying"))
        self.assertTrue(can_widen("character varying(32)", "text"))
        self.assertFalse(can_widen("bigint", "integer"))
        self.assertFalse(can_widen("text", "character varying(32)"))
        self.assertFalse(can_widen("bigint", "bigint"))
        self.assertFalse(can_widen("jsonb", "text"))

    def test_запись_типа_у_модели_и_базы_одинаковая(self):
        self.assertEqual(normalize_type("VARCHAR(64)"), "character varying(64)")
        self.assertEqual(normalize_type("NUMERIC(18, 8)"), "numeric(18,8)")
        self.assertEqual(normalize_type("TIMESTAMP"), "timestamp without time zone")
        self.assertEqual(normalize_type("TIMESTAMP WITH TIME ZONE"), "timestamp with time zone")
        self.assertEqual(normalize_type("BIGINT"), "bigint")


class ReportTests(unittest.TestCase):
    def test_пустой_отчёт_считается_неизменным(self):
        self.assertFalse(SchemaReport().changed)

    def test_любая_правка_делает_отчёт_изменённым(self):
        for field_name in ("tables", "columns", "indexes", "constraints", "altered"):
            report = SchemaReport()
            getattr(report, field_name).append("что-то")
            self.assertTrue(report.changed, field_name)

    def test_отчёт_человеку_не_считается_правкой(self):
        report = SchemaReport()
        report.skipped.append("нужна ручная миграция")
        self.assertFalse(report.changed)


class MigrationErrorTests(unittest.TestCase):
    """Код ошибки читается и у psycopg2, и у asyncpg."""

    def test_pgcode_и_sqlstate(self):
        class Psycopg2Error(Exception):
            pgcode = DEPENDENCY_ERRORCODE

        class AsyncpgError(Exception):
            sqlstate = DEPENDENCY_ERRORCODE

        class Wrapped(Exception):
            def __init__(self, orig: Exception) -> None:
                super().__init__("cannot drop index")
                self.orig = orig

        self.assertTrue(is_dependency_error(Wrapped(Psycopg2Error())))
        self.assertTrue(is_dependency_error(Wrapped(AsyncpgError())))
        self.assertEqual(error_code(ValueError("нет кода")), "")
        self.assertFalse(is_dependency_error(ValueError("нет кода")))

    def test_уже_сделанное_отличается_от_проблемы_данных(self):
        class Coded(Exception):
            def __init__(self, code: str, message: str = "") -> None:
                super().__init__(message)
                self.sqlstate = code

        class Wrapped(Exception):
            def __init__(self, orig: Exception) -> None:
                super().__init__(str(orig))
                self.orig = orig

        self.assertTrue(is_already_done(Wrapped(Coded("42701"))))
        self.assertTrue(is_already_done(Wrapped(Coded("42P07"))))
        self.assertTrue(is_already_done(Wrapped(Coded("42704"))))
        self.assertTrue(is_already_done(Wrapped(Coded("23505", 'unique constraint "pg_class_relname_nsp_index"'))))
        self.assertFalse(is_already_done(Wrapped(Coded("23505", 'could not create unique index "uq_x"'))))
        self.assertFalse(is_already_done(Wrapped(Coded("23503", "violates foreign key constraint"))))
        self.assertFalse(is_already_done(Wrapped(Coded("42501", "must be owner of table users"))))


class ChainScopeTests(unittest.TestCase):
    """Цепочка апгрейда занимается только старыми базами: новую структуру заводят модели.

    Если сюда снова попадёт CREATE TABLE или CREATE INDEX, значит вместо модели опять пишут DDL руками.
    """

    CHAIN = (ROOT / "database" / "migrations" / "schema_upgrade.py").read_text(encoding="utf-8")

    # Таблицы, которые цепочка вправе создавать сама: свой журнал версий и та, куда она сразу
    # переносит выданные токены — пустую её создать нельзя, иначе входы клиентов потеряются.
    OWN_TABLES = {"schema_migrations", "identity_sessions"}
    # Колонки-предпосылки: их заполняют из старых в том же проходе, до сравнения с моделями.
    FILLED_COLUMNS = {"id", "user_id", "tg_id", "permissions", "edges", "entry_node_id", "{column}"}

    def test_таблицы_только_свои(self):
        import re

        created = set(re.findall(r"CREATE TABLE(?: IF NOT EXISTS)? ([a-z_]+)", self.CHAIN))
        self.assertEqual(created - self.OWN_TABLES, set(), "новой таблице место в моделях, а не в цепочке")

    def test_индексы_только_под_операции_на_старой_схеме(self):
        """Эти индексы обслуживают смену ключей: под них ссылаются внешние ключи до переноса PK."""
        import re

        allowed = {
            "ix_users_id",
            "uq_users_tg_id",
            "ix_keys_tg_id",
            "{tg_index_name}",
            "ix_identity_sessions_identity_id",
            "ix_identity_sessions_identity_last_seen",
        }
        created = set(re.findall(r'CREATE (?:UNIQUE )?INDEX(?: IF NOT EXISTS)? "?([a-z_{}]+)', self.CHAIN))
        self.assertEqual(created - allowed, set(), "индексу новой функции место в модели")

    def test_колонки_только_под_перенос_данных(self):
        import re

        added = set(re.findall(r'ADD COLUMN "?([a-z_{}]+)', self.CHAIN))
        self.assertEqual(added - self.FILLED_COLUMNS, set(), "структурной колонке место в модели")

    def test_каждый_шаг_переносит_данные_или_снимает_лишнее(self):
        import re

        block = self.CHAIN[self.CHAIN.index("_MIGRATIONS = [") :]
        block = block[: block.index("\n]")]
        steps = re.findall(r"(_migration_\w+)", block)
        self.assertTrue(steps)
        work = re.compile(
            r"\b(UPDATE\s|DELETE\s+FROM|INSERT\s+INTO|ALTER COLUMN|ADD CONSTRAINT|DROP CONSTRAINT|"
            r"ADD PRIMARY KEY|DROP INDEX|DROP COLUMN|RENAME|ADD COLUMN)",
            re.I,
        )
        for step in steps:
            start = self.CHAIN.index(f"async def {step}(")
            end = self.CHAIN.find("\nasync def ", start + 1)
            body = self.CHAIN[start : end if end > 0 else len(self.CHAIN)]
            called = re.findall(r"await (_\w+)\(", body)
            for helper in called:
                marker = f"async def {helper}("
                if marker in self.CHAIN:
                    at = self.CHAIN.index(marker)
                    stop = self.CHAIN.find("\nasync def ", at + 1)
                    body += self.CHAIN[at : stop if stop > 0 else len(self.CHAIN)]
            self.assertRegex(body, work, f"{step} не переносит данные — такому шагу место в моделях")


class SchemaSetupWiringTests(unittest.TestCase):
    """Один шаг при запуске: перенос данных старых баз, затем автогенерация alembic по моделям."""

    def test_порядок_шагов_и_единая_точка_входа(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        infra = (ROOT / "core" / "infra.py").read_text(encoding="utf-8")
        init_db = (ROOT / "database" / "setup" / "init_db.py").read_text(encoding="utf-8")

        self.assertIn("run_schema_migrations()", main)
        self.assertIn("asyncio.run(run_schema_setup())", infra)
        self.assertLess(init_db.index("import_module_models()"), init_db.index("create_async_engine(DATABASE_URL)"))
        self.assertLess(init_db.index("apply_all_migrations(conn)"), init_db.index("apply_model_changes(conn"))

    def test_структуру_считает_алембик(self):
        source = (ROOT / "database" / "migrations" / "autogenerate.py").read_text(encoding="utf-8")
        self.assertIn("from alembic.autogenerate import produce_migrations", source)
        self.assertIn("produce_migrations(context, metadata).upgrade_ops", source)
        self.assertIn("operations.invoke(op)", source)
        self.assertIn('"compare_type": True', source)
        self.assertIn('"compare_server_default": True', source)
        self.assertIn("alembic==", (ROOT / "requirements.txt").read_text(encoding="utf-8"))

    def test_файлов_ревизий_не_существует(self):
        """Разница считается от фактической схемы: ревизии на клиенте и были источником падений."""
        for source in ("main.py", "core/infra.py", "database/setup/init_db.py"):
            body = (ROOT / source).read_text(encoding="utf-8")
            for marker in ("alembic upgrade", "revision --autogenerate", "alembic_version", "stamp"):
                self.assertNotIn(marker, body, f"{source}: {marker}")

    def test_снятие_объектов_отсекается_самим_алембиком(self):
        """include_object убирает из сравнения то, чего нет в моделях: alembic не предлагает DROP."""
        source = (ROOT / "database" / "migrations" / "autogenerate.py").read_text(encoding="utf-8")
        self.assertIn('"include_object": _include_object', source)
        self.assertIn("return not (reflected and compare_to is None)", source)

    def test_внутренние_логи_алембика_не_попадают_в_вывод(self):
        """Его сравнение схемы пишет десятки INFO-строк на каждую таблицу — это не наш лог."""
        logger_source = (ROOT / "logger.py").read_text(encoding="utf-8")
        for name in ("alembic", "alembic.autogenerate", "alembic.autogenerate.compare"):
            self.assertIn(f'"{name}",', logger_source)

    def test_итог_одной_строкой(self):
        source = (ROOT / "database" / "migrations" / "autogenerate.py").read_text(encoding="utf-8")
        self.assertIn('mig_out(f"[alembic] схема приведена к моделям: {done}", "green")', source)
        body = source[source.index("def log_report(") :]
        self.assertIn("if not report.changed and not report.skipped:\n        return", body)

    def test_подпись_операции_не_падает_на_внешнем_ключе(self):
        """У CreateForeignKeyOp таблица лежит в source_table, а не в table_name."""
        from database.migrations.autogenerate import _describe

        op = ops.CreateForeignKeyOp("fk_keys_user", "keys", "users", ["user_id"], ["id"])
        self.assertEqual(_describe(op), "ограничение fk_keys_user")
        unnamed = ops.CreateForeignKeyOp(None, "keys", "users", ["user_id"], ["id"])
        self.assertEqual(_describe(unnamed), "ограничение keys")

    def test_каждый_шаг_в_точке_сохранения(self):
        source = (ROOT / "database" / "migrations" / "autogenerate.py").read_text(encoding="utf-8")
        self.assertIn("with conn.begin_nested():", source)


if __name__ == "__main__":
    unittest.main()

from dataclasses import dataclass, field

from sqlalchemy import Connection, MetaData, text
from sqlalchemy.ext.asyncio import AsyncConnection

from alembic.autogenerate import produce_migrations
from alembic.migration import MigrationContext
from alembic.operations import Operations, ops
from database.migrations.errors import is_already_done
from database.migrations.output import mig_out
from logger import logger


ADDITIVE_OPS = (
    ops.CreateTableOp,
    ops.AddColumnOp,
    ops.CreateIndexOp,
    ops.CreateUniqueConstraintOp,
    ops.CreateForeignKeyOp,
    ops.CreateCheckConstraintOp,
    ops.CreateTableCommentOp,
)

TYPE_ALIASES = {
    "varchar": "character varying",
    "char": "character",
    "datetime": "timestamp without time zone",
    "timestamp": "timestamp without time zone",
    "timestamptz": "timestamp with time zone",
    "float": "double precision",
    "bool": "boolean",
    "int": "integer",
    "int4": "integer",
    "int8": "bigint",
    "serial": "integer",
    "bigserial": "bigint",
}

WIDENINGS = {
    ("smallint", "integer"),
    ("smallint", "bigint"),
    ("integer", "bigint"),
    ("real", "double precision"),
    ("json", "jsonb"),
    ("timestamp without time zone", "timestamp with time zone"),
}


@dataclass
class SchemaReport:
    """Что автогенерация довела до моделей и что осталось решать человеку."""

    tables: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    indexes: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    altered: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.tables or self.columns or self.indexes or self.constraints or self.altered)


def normalize_type(raw: object) -> str:
    """Приводит запись типа к виду PostgreSQL: модель и база должны называть один тип одинаково."""
    value = str(raw or "").strip().lower()
    base, bracket, params = value.partition("(")
    base = base.strip()
    if base.endswith("[]"):
        return value
    base = TYPE_ALIASES.get(base, base)
    return f"{base}({params.replace(' ', '')}" if bracket else base


def _length(value: str) -> int:
    _, bracket, params = value.partition("(")
    if not bracket:
        return 0
    digits = params.rstrip(")").split(",")[0].strip()
    return int(digits) if digits.isdigit() else 0


def can_widen(db_type: str, model_type: str) -> bool:
    """Можно ли расширить тип без потери данных: сужение — решение человека, автоматом не делаем."""
    if db_type == model_type:
        return False
    db_base = db_type.partition("(")[0]
    model_base = model_type.partition("(")[0]
    if (db_base, model_base) in WIDENINGS:
        return True
    if db_base == "character varying" and model_base == "text":
        return True
    if db_base == model_base in {"character varying", "numeric"}:
        model_length = _length(model_type)
        return model_length == 0 or model_length > _length(db_type)
    return False


def _reason(exc: BaseException) -> str:
    error = getattr(exc, "orig", exc)
    lines = [line.strip() for line in str(error).splitlines() if line.strip()]
    return lines[0] if lines else type(error).__name__


UNIQUE_SETS_SQL = """
    SELECT t.relname, array_agg(a.attname ORDER BY k.ord)
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    JOIN unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord) ON true
    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
    WHERE n.nspname = 'public' AND c.contype IN ('u', 'p')
    GROUP BY c.oid, t.relname
    UNION
    SELECT t.relname, array_agg(a.attname ORDER BY k.ord)
    FROM pg_index i
    JOIN pg_class t ON t.oid = i.indrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    JOIN unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord) ON true
    JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
    WHERE n.nspname = 'public' AND i.indisunique
    GROUP BY i.indexrelid, t.relname
"""

INDEX_SETS_SQL = """
    SELECT t.relname, array_agg(a.attname ORDER BY k.ord), i.indisunique
    FROM pg_index i
    JOIN pg_class t ON t.oid = i.indrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    JOIN unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord) ON true
    JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
    WHERE n.nspname = 'public'
    GROUP BY i.indexrelid, t.relname, i.indisunique
"""


def _already_covered(conn: Connection, op) -> bool:
    """Есть ли уже такое покрытие теми же колонками — под другим именем.

    Колонку с `unique=True` алембик заводит вместе с ограничением и следом просит второе такое же:
    так на старой базе появлялся дубль вида `users_partner_code_key1`. Дублировать индексы и
    уникальность незачем — они обслуживаются на каждой записи.
    """
    if isinstance(op, ops.CreateUniqueConstraintOp):
        wanted = (op.table_name, tuple(str(column) for column in op.columns))
        rows = conn.execute(text(UNIQUE_SETS_SQL)).fetchall()
        return wanted in {(row[0], tuple(row[1])) for row in rows}
    if isinstance(op, ops.CreateIndexOp):
        unique = bool(op.kw.get("unique", False))
        wanted = (op.table_name, tuple(str(column) for column in op.columns), unique)
        rows = conn.execute(text(INDEX_SETS_SQL)).fetchall()
        return wanted in {(row[0], tuple(row[1]), bool(row[2])) for row in rows}
    return False


def _has_nulls(conn: Connection, table: str, column: str) -> bool:
    return conn.execute(text(f'SELECT 1 FROM "{table}" WHERE "{column}" IS NULL LIMIT 1')).first() is not None


def _safe_alter(conn: Connection, op: ops.AlterColumnOp) -> tuple[ops.AlterColumnOp | None, str]:
    """Оставляет в правке колонки только безопасное: расширение типа и посильную смену строгости.

    Сужение типа теряет данные, а NOT NULL поверх пустых значений база просто не поставит —
    такие расхождения уходят в отчёт, а не выполняются молча.
    """
    kwargs: dict[str, object] = {}
    note = ""

    if op.modify_type is not None:
        db_type = normalize_type(op.existing_type.compile(conn.dialect))
        model_type = normalize_type(op.modify_type.compile(conn.dialect))
        if can_widen(db_type, model_type):
            kwargs["modify_type"] = op.modify_type
        else:
            logger.debug(
                f"[Schema] {op.table_name}.{op.column_name}: тип базы {db_type}, модель {model_type} — оставлено как есть"
            )

    if op.modify_nullable is True:
        kwargs["modify_nullable"] = True
    elif op.modify_nullable is False:
        if _has_nulls(conn, op.table_name, op.column_name):
            note = f"{op.table_name}.{op.column_name}: модель требует NOT NULL, но в колонке есть пустые значения"
        else:
            kwargs["modify_nullable"] = False

    if op.modify_server_default is not False:
        kwargs["modify_server_default"] = op.modify_server_default

    if not kwargs:
        return None, note

    return (
        ops.AlterColumnOp(
            op.table_name,
            op.column_name,
            schema=op.schema,
            existing_type=op.existing_type,
            existing_nullable=op.existing_nullable,
            existing_server_default=op.existing_server_default,
            **kwargs,
        ),
        note,
    )


def _describe(op) -> str:
    """Короткая подпись операции для вывода и отчёта."""
    if isinstance(op, ops.CreateTableOp):
        return f"таблица {op.table_name}"
    if isinstance(op, ops.AddColumnOp):
        return f"колонка {op.table_name}.{op.column.name}"
    if isinstance(op, ops.CreateIndexOp):
        return f"индекс {op.index_name}"
    if isinstance(op, ops.AlterColumnOp):
        return f"колонка {op.table_name}.{op.column_name}"
    if isinstance(op, ops.CreateUniqueConstraintOp | ops.CreateForeignKeyOp | ops.CreateCheckConstraintOp):
        table = getattr(op, "table_name", None) or getattr(op, "source_table", "")
        return f"ограничение {op.constraint_name or table}"
    return f"{type(op).__name__} {getattr(op, 'table_name', '')}".strip()


def _record(report: SchemaReport, op) -> None:
    if isinstance(op, ops.CreateTableOp):
        report.tables.append(op.table_name)
    elif isinstance(op, ops.AddColumnOp):
        report.columns.append(f"{op.table_name}.{op.column.name}")
    elif isinstance(op, ops.CreateIndexOp):
        report.indexes.append(str(op.index_name))
    elif isinstance(op, ops.AlterColumnOp):
        report.altered.append(f"{op.table_name}.{op.column_name}")
    else:
        report.constraints.append(_describe(op))


def _include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Отсекает из сравнения то, чего нет в моделях: снятие объектов и данных — решение человека.

    Так alembic не предлагает снести ни служебный журнал переносов, ни то, что клиент завёл сам.
    """
    return not (reflected and compare_to is None)


def _flatten(container):
    for item in container.ops:
        if isinstance(item, ops.ModifyTableOps):
            yield from _flatten(item)
        else:
            yield item


def _apply(conn: Connection, metadata: MetaData, report: SchemaReport) -> None:
    context = MigrationContext.configure(
        conn,
        opts={
            "compare_type": True,
            "compare_server_default": True,
            "include_schemas": False,
            "include_object": _include_object,
            "target_metadata": metadata,
        },
    )
    operations = Operations(context)

    for op in _flatten(produce_migrations(context, metadata).upgrade_ops):
        if isinstance(op, ops.AlterColumnOp):
            op, note = _safe_alter(conn, op)
            if note:
                report.skipped.append(note)
            if op is None:
                continue
        elif not isinstance(op, ADDITIVE_OPS):
            logger.debug(f"[Schema] {type(op).__name__} {_describe(op)} — снятие объектов делает человек")
            continue
        elif _already_covered(conn, op):
            logger.debug(f"[Schema] {_describe(op)}: те же колонки уже покрыты — дубль не нужен")
            continue

        try:
            with conn.begin_nested():
                operations.invoke(op)
        except Exception as exc:
            reason = _reason(exc)
            if is_already_done(exc):
                logger.debug(f"[Schema] {_describe(op)}: шаг уже сделан ({reason})")
                continue
            report.skipped.append(f"{_describe(op)}: {reason}")
            continue
        _record(report, op)


async def apply_model_changes(conn: AsyncConnection, metadata: MetaData, report: SchemaReport) -> None:
    """Приводит схему к моделям автогенерацией alembic: разницу считает и применяет он сам.

    Файлов ревизий нет: сравниваются модели с фактической схемой, поэтому шаг идемпотентен и не
    зависит от истории установки. Применяется только то, что добавляет или расширяет — снятие
    таблиц, колонок и ограничений остаётся человеку, чтобы обновление не удаляло данные.
    """
    if conn.dialect.name != "postgresql":
        return
    await conn.run_sync(lambda sync_conn: _apply(sync_conn, metadata, report))


def _plural(count: int, one: str, few: str, many: str) -> str:
    tail = count % 100
    if 11 <= tail <= 14:
        return f"{count} {many}"
    tail %= 10
    if tail == 1:
        return f"{count} {one}"
    if 2 <= tail <= 4:
        return f"{count} {few}"
    return f"{count} {many}"


def log_report(report: SchemaReport) -> None:
    """Итог автогенерации одной строкой: молча — когда сверять было нечего."""
    if not report.changed and not report.skipped:
        return

    parts = [
        _plural(len(report.tables), "таблица", "таблицы", "таблиц") if report.tables else "",
        _plural(len(report.columns), "колонка", "колонки", "колонок") if report.columns else "",
        _plural(len(report.indexes), "индекс", "индекса", "индексов") if report.indexes else "",
        _plural(len(report.constraints), "ограничение", "ограничения", "ограничений") if report.constraints else "",
        _plural(len(report.altered), "правка колонки", "правки колонок", "правок колонок") if report.altered else "",
    ]
    done = ", ".join(part for part in parts if part)
    if done:
        mig_out(f"[alembic] схема приведена к моделям: {done}", "green")
    for table in report.tables:
        logger.debug(f"[alembic] создана таблица {table}")
    for column in report.columns:
        logger.debug(f"[alembic] добавлена колонка {column}")
    for index in report.indexes:
        logger.debug(f"[alembic] создан индекс {index}")
    for constraint in report.constraints:
        logger.debug(f"[alembic] создано {constraint}")
    for column in report.altered:
        logger.debug(f"[alembic] приведена к модели колонка {column}")
    for reason in report.skipped:
        logger.warning(f"[alembic] {reason}")
        mig_out(f"[alembic] осталось человеку — {reason}")

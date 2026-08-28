import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
AUDIT = (ROOT / "audit" / "__init__.py").read_text(encoding="utf-8")


def _drain_body() -> str:
    start = AUDIT.index("async def drain_audit_redis_to_db")
    return AUDIT[start:]


class SanitizeTests(unittest.TestCase):
    """PostgreSQL не принимает \\x00 в тексте: такая запись не пишется никогда."""

    def test_очистка_есть(self):
        self.assertIn("def _pg_safe(", AUDIT)
        self.assertIn('clean = str(value).replace("\\x00", "")', AUDIT)

    def test_путь_запроса_чистится_на_входе(self):
        self.assertIn('path_or_handler=_pg_safe(path_or_handler) or "-"', AUDIT)

    def test_текстовые_поля_чистятся_перед_записью(self):
        body = _drain_body()
        for field in ("event_type", "channel", "path_or_handler", "entity_type", "entity_id", "result", "reason"):
            self.assertIn(f"{field}=_pg_safe(", body, field)

    def test_строка_обрезается(self):
        self.assertIn("return clean[:limit]", AUDIT)


class QueueNeverWedgesTests(unittest.TestCase):
    """Раньше сбойная пачка оставалась в processing и запирала весь журнал."""

    def test_пачка_разбирается_поштучно(self):
        body = _drain_body()
        self.assertIn("written, poisoned = await _drain_batch_one_by_one(session_factory, batch)", body)

    def test_после_разбора_очередь_освобождается(self):
        body = _drain_body()
        segment = body[body.index("разбираю поштучно") :][:900]
        self.assertIn("cache_lpop_batch(_AUDIT_REDIS_PROCESSING_KEY, len(raw_batch))", segment)

    def test_цикл_больше_не_обрывается_на_сбое(self):
        body = _drain_body()
        segment = body[body.index("разбираю поштучно") :][:900]
        self.assertNotIn("break", segment, "выход из цикла оставлял пачку навсегда")

    def test_отброшенное_попадает_в_лог(self):
        body = _drain_body()
        self.assertIn("не принимает база и они отброшены", body)

    def test_поштучный_разбор_возвращает_обе_величины(self):
        helper = AUDIT[
            AUDIT.index("async def _drain_batch_one_by_one") : AUDIT.index("async def drain_audit_redis_to_db")
        ]
        self.assertIn("-> tuple[int, list[str]]", helper)
        self.assertIn("written += 1", helper)
        self.assertIn("poisoned.append(", helper)
        self.assertIn("return written, poisoned", helper)

    def test_время_события_не_подменяется_текущим(self):
        """У спасённой записи должен остаться её исходный момент, а не время разбора."""
        helper = AUDIT[
            AUDIT.index("async def _drain_batch_one_by_one") : AUDIT.index("async def drain_audit_redis_to_db")
        ]
        self.assertIn("created_at=_record_created_at(rec),", helper)
        self.assertIn("def _record_created_at(rec: dict) -> datetime:", AUDIT)
        self.assertIn('datetime.fromisoformat(created.replace("Z", "+00:00"))', AUDIT)

    def test_каждая_запись_в_своей_сессии(self):
        helper = AUDIT[
            AUDIT.index("async def _drain_batch_one_by_one") : AUDIT.index("async def drain_audit_redis_to_db")
        ]
        self.assertIn("async with session_factory() as session:", helper)
        self.assertLess(helper.index("for rec in batch:"), helper.index("async with session_factory()"))


if __name__ == "__main__":
    unittest.main()

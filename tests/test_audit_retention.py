import re
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CRON = (ROOT / "core" / "tasks" / "cron_tasks.py").read_text(encoding="utf-8")
REGISTRY = (ROOT / "core" / "tasks" / "registry.py").read_text(encoding="utf-8")
AUDIT = (ROOT / "audit" / "__init__.py").read_text(encoding="utf-8")


def _job() -> str:
    start = CRON.index("async def cleanup_web_analytics_job")
    return CRON[start : CRON.index("def cleanup_web_analytics_process_runner")]


class RetentionTests(unittest.TestCase):
    """Удаление старых записей аудита было написано, но никто его не звал."""

    def test_срок_хранения_объявлен(self):
        self.assertIn("AUDIT_RETENTION_DAYS = 90", CRON)

    def test_уборка_чистит_журнал_доступа(self):
        job = _job()
        self.assertIn("from audit import delete_old_audit_events", job)
        self.assertIn("older_than_days=AUDIT_RETENTION_DAYS", job)

    def test_удаление_идёт_до_коммита(self):
        job = _job()
        self.assertLess(job.index("delete_old_audit_events(session"), job.index("await session.commit()"))

    def test_результат_попадает_в_лог(self):
        job = _job()
        self.assertIn("audit_events={}", job)
        self.assertIn("audit_removed,", job)

    def test_остальные_таблицы_чистятся_как_прежде(self):
        job = _job()
        for name in ("WebPageView", "WebFlowEvent", "WebErrorReport", "KeyTrafficHistory", "rate_limit_counters"):
            self.assertIn(name, job, name)


class ScheduleTests(unittest.TestCase):
    def test_уборка_стоит_в_расписании(self):
        self.assertIn('"cleanup_web_analytics",', REGISTRY)
        self.assertIn("WEB_ANALYTICS_CLEANUP_TRIGGER", REGISTRY)

    def test_запускается_раз_в_сутки(self):
        trigger = re.search(r"WEB_ANALYTICS_CLEANUP_TRIGGER = CronTrigger\(([^)]*)\)", CRON)
        self.assertTrue(trigger)
        self.assertIn("hour=", trigger.group(1))
        self.assertIn("minute=", trigger.group(1))


class HelperStillThereTests(unittest.TestCase):
    def test_обёртка_и_запрос_на_месте(self):
        self.assertIn("async def delete_old_audit_events(", AUDIT)
        self.assertIn("older_than_days: int = 90", AUDIT)


if __name__ == "__main__":
    unittest.main()

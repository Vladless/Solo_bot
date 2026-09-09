import re
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MODEL = (ROOT / "database" / "models" / "payments.py").read_text(encoding="utf-8")
MIGRATIONS = (ROOT / "database" / "migrations" / "schema_upgrade.py").read_text(encoding="utf-8")
SETUP = (ROOT / "database" / "setup" / "init_db.py").read_text(encoding="utf-8")


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


class ChainTests(unittest.TestCase):
    """Индексы объявлены моделью, поэтому в цепочке апгрейда их быть не должно."""

    def test_ручного_шага_для_индексов_нет(self):
        self.assertNotIn("ix_payments_status_created", MIGRATIONS)
        self.assertNotIn("ix_payments_created", MIGRATIONS)

    def test_номера_шагов_уникальны_и_возрастают(self):
        block = MIGRATIONS[MIGRATIONS.index("_MIGRATIONS = [") :]
        block = block[: block.index("\n]")]
        nums = [int(n) for n in re.findall(r"^\s*\((\d+),", block, re.M)]
        self.assertTrue(nums)
        self.assertEqual(nums, sorted(set(nums)), "номера версий должны идти по возрастанию без дублей")

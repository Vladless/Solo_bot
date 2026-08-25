import re
import unittest

from pathlib import Path

from database.models import Gift, Key, Payment, User


ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = (ROOT / "database" / "migrations" / "schema_upgrade.py").read_text(encoding="utf-8")


class NoTgForeignKeysTests(unittest.TestCase):
    def test_ни_одно_зеркало_не_ссылается_на_users_tg_id(self):
        offenders = []
        for model in (Key, Payment, Gift):
            for column in model.__table__.columns:
                for fk in column.foreign_keys:
                    if fk.target_fullname == "users.tg_id":
                        offenders.append(f"{model.__tablename__}.{column.name}")
        self.assertEqual(offenders, [], "внешние ключи на users.tg_id мешают отвязке Telegram")

    def test_связь_владельца_осталась(self):
        owners = {fk.target_fullname for c in Key.__table__.columns if c.name == "user_id" for fk in c.foreign_keys}
        self.assertIn("users.id", owners, "подписка обязана держаться за users.id")

    def test_зеркала_на_месте_как_поля(self):
        self.assertIn("tg_id", Key.__table__.columns)
        self.assertIn("sender_tg_id", Gift.__table__.columns)
        self.assertIn("recipient_tg_id", Gift.__table__.columns)

    def test_tg_id_у_пользователя_не_тронут(self):
        self.assertTrue(User.__table__.columns["tg_id"].unique)


class MigrationTests(unittest.TestCase):
    def _body(self) -> str:
        match = re.search(r"^async def _migration_v51_drop_tg_id_foreign_keys\(.*?(?=^async def )", MIGRATIONS, re.S | re.M)
        assert match, "миграция не найдена"
        return match.group(0)

    def test_миграция_зарегистрирована(self):
        self.assertIn('(51, "Снятие внешних ключей на users.tg_id", _migration_v51_drop_tg_id_foreign_keys)', MIGRATIONS)

    def test_снимаются_только_ключи_на_tg_id(self):
        body = self._body()
        self.assertIn("att.attname = 'tg_id'", body)
        self.assertIn("con.contype = 'f'", body)

    def test_имена_ищутся_в_каталоге_а_не_угадываются(self):
        body = self._body()
        self.assertIn("pg_constraint", body)
        self.assertNotIn("keys_tg_id_fkey", body)

    def test_повторный_запуск_безопасен(self):
        body = self._body()
        self.assertIn("DROP CONSTRAINT IF EXISTS", body)
        self.assertIn("_exec_ignore", body)

    def test_без_таблицы_пользователей_молчит(self):
        self.assertIn('if not await _table_exists(conn, "users"):\n        return', self._body())


if __name__ == "__main__":
    unittest.main()

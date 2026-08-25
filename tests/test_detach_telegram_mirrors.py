import unittest

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from sqlalchemy import update

from database.access.tg_mirror import TG_FOREIGN_KEY_MIRRORS, release_tg_mirrors
from database.identities import detach_telegram


OLD_TG = 555001


def _target(stmt) -> str:
    """Имя таблицы и колонок, которых касается запрос — по нему и сверяем порядок."""
    table = getattr(getattr(stmt, "table", None), "name", "")
    values = ",".join(sorted(str(getattr(k, "name", k)) for k in (getattr(stmt, "_values", None) or {})))
    return f"{table}:{values}" if values else table


class RecordingSession:
    """Пишет очередь запросов: важен не только состав, но и порядок."""

    def __init__(self, user_ids=(77,)) -> None:
        self.statements = []
        self._user_ids = list(user_ids)
        self.flush = AsyncMock()
        self.refresh = AsyncMock()

    async def execute(self, stmt):
        self.statements.append(stmt)
        scalars = SimpleNamespace(all=lambda: list(self._user_ids), one_or_none=lambda: OLD_TG)
        return SimpleNamespace(scalars=lambda: scalars, scalar_one_or_none=lambda: OLD_TG)

    def targets(self) -> list[str]:
        return [_target(s) for s in self.statements]


class ReleaseMirrorsTests(unittest.IsolatedAsyncioTestCase):
    async def test_снимаются_все_колонки_с_внешним_ключом(self):
        session = RecordingSession()
        await release_tg_mirrors(session, OLD_TG)

        self.assertEqual(
            session.targets(),
            ["keys:tg_id", "payments:tg_id", "gifts:sender_tg_id", "gifts:recipient_tg_id"],
        )

    async def test_список_покрывает_все_зеркала_tg_id(self):
        from database import models

        mirrored = set()
        for model in (models.Key, models.Payment, models.Gift):
            for column in model.__table__.columns:
                if column.name.endswith("tg_id"):
                    mirrored.add((model.__name__, column.name))

        covered = {(model.__name__, column) for model, column in TG_FOREIGN_KEY_MIRRORS}
        self.assertEqual(covered, mirrored, "снимать нужно каждое зеркало tg_id в этих таблицах")

    async def test_внешних_ключей_на_users_tg_id_больше_нет(self):
        from database import models

        for model in (models.Key, models.Payment, models.Gift):
            for column in model.__table__.columns:
                for fk in column.foreign_keys:
                    self.assertNotEqual(fk.target_fullname, "users.tg_id", f"{model.__name__}.{column.name}")

    async def test_снимаются_и_чужие_строки_с_тем_же_tg(self):
        session = RecordingSession()
        await release_tg_mirrors(session, OLD_TG)
        for stmt in session.statements:
            self.assertIn("tg_id", str(stmt.whereclause), "отбор идёт по tg_id, а не по user_id")


class DetachTelegramOrderTests(unittest.IsolatedAsyncioTestCase):
    async def _detach(self):
        identity = SimpleNamespace(id="idt-1", tg_id=OLD_TG, email="u@test", is_admin=True)
        session = RecordingSession()
        with patch("database.identities.get_identity_by_id", new=AsyncMock(return_value=identity)):
            result = await detach_telegram(session, identity.id)
        return session, result

    async def test_зеркала_снимаются_до_обнуления_родителя(self):
        session, _ = await self._detach()
        targets = session.targets()

        parent = targets.index("users:tg_id")
        for mirror in ("keys:tg_id", "payments:tg_id", "gifts:sender_tg_id", "gifts:recipient_tg_id"):
            self.assertIn(mirror, targets, mirror)
            self.assertLess(targets.index(mirror), parent, f"{mirror} должно сниматься до users.tg_id")

    async def test_telegram_снят_с_identity(self):
        _, identity = await self._detach()
        self.assertIsNone(identity.tg_id)
        self.assertFalse(identity.is_admin)

    async def test_без_почты_отвязка_запрещена(self):
        identity = SimpleNamespace(id="idt-2", tg_id=OLD_TG, email=None, is_admin=False)
        session = RecordingSession()
        with patch("database.identities.get_identity_by_id", new=AsyncMock(return_value=identity)):
            result = await detach_telegram(session, identity.id)

        self.assertIsNone(result)
        self.assertEqual(session.statements, [], "ни одного запроса при запрещённой отвязке")

    async def test_подписка_не_отвязывается_от_клиента(self):
        session, _ = await self._detach()
        for stmt in session.statements:
            if getattr(getattr(stmt, "table", None), "name", "") == "keys":
                touched = {str(getattr(k, "name", k)) for k in (stmt._values or {})}
                self.assertNotIn("user_id", touched, "связь keys.user_id → users.id не трогаем")


if __name__ == "__main__":
    unittest.main()

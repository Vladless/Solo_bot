import re
import unittest

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from database.access.resolution import subscription_owner_ref


ROOT = Path(__file__).resolve().parent.parent
CREATION = (ROOT / "services" / "operations" / "creation.py").read_text(encoding="utf-8")
UPDATE = (ROOT / "services" / "operations" / "update.py").read_text(encoding="utf-8")
LIFECYCLE = (ROOT / "handlers" / "admin" / "users" / "users_keys" / "lifecycle.py").read_text(encoding="utf-8")
IDENTITIES = (ROOT / "database" / "identities.py").read_text(encoding="utf-8")


class OwnerRefTests(unittest.IsolatedAsyncioTestCase):
    async def _ref(self, user):
        with patch("database.access.resolution.resolve_user_optional", new=AsyncMock(return_value=user)):
            return await subscription_owner_ref(None, 1234)

    async def test_клиент_с_сайта_получает_минус(self):
        user = SimpleNamespace(id=1234, tg_id=-1234)
        self.assertEqual(await self._ref(user), -1234)

    async def test_клиент_из_телеграма_не_меняется(self):
        user = SimpleNamespace(id=77, tg_id=555000111)
        self.assertEqual(await self._ref(user), 555000111)

    async def test_без_записи_остаётся_исходная_ссылка(self):
        self.assertEqual(await self._ref(None), 1234)

    async def test_пустой_tg_id_не_ломает(self):
        user = SimpleNamespace(id=1234, tg_id=None)
        self.assertEqual(await self._ref(user), 1234)


class SourceGuardTests(unittest.TestCase):
    def test_синтетический_id_это_минус_users_id(self):
        self.assertIn("synthetic = -int(uid)", IDENTITIES)

    def test_создание_строит_ссылку_по_владельцу(self):
        self.assertIn("link_ref = await subscription_owner_ref(session, tg_id)", CREATION)
        self.assertIn('public_link = f"{PUBLIC_LINK}{email}/{link_ref}"', CREATION)
        self.assertNotIn('f"{PUBLIC_LINK}{email}/{tg_id}"', CREATION)

    def test_агрегированная_ссылка_получает_тот_же_хвост(self):
        block = CREATION[CREATION.index("public_link = await make_aggregated_link("):]
        self.assertIn("tg_id=link_ref,", block[:400])

    def test_обновление_и_перевыпуск_тоже_исправлены(self):
        self.assertIn("subscription_owner_ref(session, user_id)", UPDATE)
        self.assertIn("subscription_owner_ref(session, user_id)", LIFECYCLE)
        self.assertNotIn('f"{PUBLIC_LINK}{email}/{user_id}"', UPDATE)
        self.assertNotIn('f"{PUBLIC_LINK}{new_email}/{user_id}"', LIFECYCLE)

    def test_в_панель_уходит_владелец_а_не_users_id(self):
        """В панель уходит номер владельца, а не users.id."""
        block = UPDATE[UPDATE.index("async def update_subscription"):]
        for call in ("update_key_on_cluster(", "make_aggregated_link("):
            args = block[block.index(call):][:600]
            self.assertIn("tg_id=owner_ref,", args, call)
            self.assertNotIn("tg_id=user_id,", args, call)


if __name__ == "__main__":
    unittest.main()

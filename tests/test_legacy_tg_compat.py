import unittest

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.types import InlineKeyboardButton

import database  # noqa: F401  порядок импорта: иначе core.bootstrap уходит в цикл

from handlers.admin.users.keyboard import AdminUserEditorCallback, AdminUserKeyEditorCallback, build_user_edit_kb
from middlewares.legacy_ref import LegacyUserRefMiddleware


class CallbackAliasTests(unittest.TestCase):
    """Конструктор принимает tg_id вместо user_id."""

    def test_старый_конструктор_принимается(self):
        cb = AdminUserEditorCallback(action="x", tg_id=8119047207)
        self.assertEqual(cb.user_id, 8119047207)

    def test_новый_конструктор_не_задет(self):
        self.assertEqual(AdminUserEditorCallback(action="x", user_id=96).user_id, 96)

    def test_алиас_есть_и_у_ключевой_фабрики(self):
        self.assertEqual(AdminUserKeyEditorCallback(action="x", tg_id=42, data="d").user_id, 42)

    def test_лишнего_поля_в_callback_data_не_появилось(self):
        packed = AdminUserEditorCallback(action="users_editor", tg_id=8119047207).pack()
        self.assertEqual(packed, "admin_users:users_editor:8119047207::0")
        self.assertLessEqual(len(packed), 64)


class LegacyRefMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    """Приведение ссылки к users.id на входе."""

    async def _pass_through(self, ref: int, resolved: int | None):
        seen = {}

        async def handler(event, data):
            seen["user_id"] = data["callback_data"].user_id

        cb = AdminUserEditorCallback(action="users_editor", user_id=ref)
        with patch("middlewares.legacy_ref.user_id_from_legacy_ref", new=AsyncMock(return_value=resolved)):
            await LegacyUserRefMiddleware()(handler, object(), {"callback_data": cb, "session": SimpleNamespace(scalar=AsyncMock())})
        return seen["user_id"]

    async def test_тг_приводится_к_user_id(self):
        self.assertEqual(await self._pass_through(8119047207, 2), 2)

    async def test_user_id_остаётся_как_есть(self):
        self.assertEqual(await self._pass_through(2, 2), 2)

    async def test_ненайденная_ссылка_не_подменяется(self):
        self.assertEqual(await self._pass_through(999999, None), 999999)


class HookPayloadTests(unittest.IsolatedAsyncioTestCase):
    """Хук карточки отдаёт и user_id, и tg_id."""

    async def test_старый_и_новый_модуль_получают_своё(self):
        got = {}

        def old_module(tg_id, is_banned, admin_role=None, **kwargs: object):
            got["tg_id"] = tg_id
            return InlineKeyboardButton(text="старый", callback_data="a")

        def new_module(user_id, is_banned, admin_role=None, **kwargs: object):
            got["user_id"] = user_id
            return InlineKeyboardButton(text="новый", callback_data="b")

        with patch("handlers.admin.users.keyboard.run_hooks", new=AsyncMock(return_value=[])) as hooks:
            await build_user_edit_kb(96, [], admin_role="superadmin", legacy_tg_id=-96)
        payload = hooks.await_args.kwargs
        self.assertEqual(payload["user_id"], 96)
        self.assertEqual(payload["tg_id"], -96)

        old_module(payload["tg_id"], False, payload["admin_role"])
        new_module(payload["user_id"], False, payload["admin_role"])
        self.assertEqual((got["tg_id"], got["user_id"]), (-96, 96))


if __name__ == "__main__":
    unittest.main()

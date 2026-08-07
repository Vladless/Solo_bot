import unittest

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from database.coupons import check_coupon_usage, create_coupon_usage, has_any_coupon_usage
from database.keys import store_key


class KeysLegacyResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_store_key_raises_when_user_missing(self):
        session = SimpleNamespace(execute=AsyncMock(), add=Mock(), commit=AsyncMock(), rollback=AsyncMock())

        with patch("database.keys.resolve_user_optional", new=AsyncMock(return_value=None)):
            with self.assertRaises(ValueError):
                await store_key(
                    session=session,
                    legacy_user_ref=9999,
                    client_id="client_1",
                    email="u@test",
                    expiry_time=1111111111111,
                    key="k",
                    server_id="s1",
                )

        session.add.assert_not_called()
        session.commit.assert_not_called()

    async def test_store_key_creates_with_billing_user_and_tg_mirror(self):
        user = SimpleNamespace(id=55, tg_id=5050)
        first_query_result = SimpleNamespace(scalar_one_or_none=lambda: None)
        session = SimpleNamespace(
            execute=AsyncMock(return_value=first_query_result),
            add=Mock(),
            commit=AsyncMock(),
            rollback=AsyncMock(),
        )

        with (
            patch("database.keys.resolve_user_optional", new=AsyncMock(return_value=user)),
            patch("database.keys.invalidate_keys_list", new=AsyncMock()),
            patch("database.keys.invalidate_key_details", new=AsyncMock()),
            patch("database.keys.invalidate_user_snapshot", new=Mock()),
        ):
            await store_key(
                session=session,
                legacy_user_ref=5050,
                client_id="client_2",
                email="x@test",
                expiry_time=2222222222222,
                key="kk",
                server_id="s2",
                tariff_id=3,
            )

        self.assertEqual(session.add.call_count, 2)
        added_key = session.add.call_args_list[0].args[0]
        self.assertEqual(added_key.user_id, 55)
        self.assertEqual(added_key.tg_id, 5050)
        self.assertEqual(added_key.client_id, "client_2")
        session.commit.assert_not_called()

    async def test_store_key_updates_existing_key_with_tg_mirror(self):
        user = SimpleNamespace(id=88, tg_id=8080)
        existing_key = SimpleNamespace(id=1, client_id="client_3")
        first_query_result = SimpleNamespace(scalar_one_or_none=lambda: existing_key)
        session = SimpleNamespace(
            execute=AsyncMock(side_effect=[first_query_result, SimpleNamespace()]),
            add=Mock(),
            commit=AsyncMock(),
            rollback=AsyncMock(),
        )

        with (
            patch("database.keys.resolve_user_optional", new=AsyncMock(return_value=user)),
            patch("database.keys.invalidate_keys_list", new=AsyncMock()),
            patch("database.keys.invalidate_key_details", new=AsyncMock()),
            patch("database.keys.invalidate_user_snapshot", new=Mock()),
        ):
            await store_key(
                session=session,
                legacy_user_ref=8080,
                client_id="client_3",
                email="upd@test",
                expiry_time=3333333333333,
                key="new_key",
                server_id="s3",
                selected_device_limit=5,
                current_device_limit=7,
            )

        session.add.assert_not_called()
        self.assertEqual(session.execute.await_count, 2)
        update_stmt = session.execute.await_args_list[1].args[0]
        compiled = update_stmt.compile()
        self.assertEqual(compiled.params["email"], "upd@test")
        self.assertEqual(compiled.params["tg_id"], 8080)
        self.assertEqual(compiled.params["selected_device_limit"], 5)
        self.assertEqual(compiled.params["current_device_limit"], 7)
        session.commit.assert_not_called()


class CouponsLegacyResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_coupon_usage_uses_resolved_user_and_tg_mirror(self):
        user = SimpleNamespace(id=77, tg_id=7007)
        session = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())

        with patch("database.coupons.resolve_user_optional", new=AsyncMock(return_value=user)):
            await create_coupon_usage(session, coupon_id=11, user_id=7007)

        session.execute.assert_awaited_once()
        stmt = session.execute.await_args.args[0]
        compiled = stmt.compile()
        self.assertEqual(compiled.params["coupon_id"], 11)
        self.assertEqual(compiled.params["user_id"], 77)
        self.assertEqual(compiled.params["tg_id"], 7007)
        session.commit.assert_not_called()

    async def test_create_coupon_usage_falls_back_to_legacy_when_user_missing(self):
        session = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())

        with patch("database.coupons.resolve_user_optional", new=AsyncMock(return_value=None)):
            await create_coupon_usage(session, coupon_id=15, user_id=9090)

        session.execute.assert_awaited_once()
        stmt = session.execute.await_args.args[0]
        compiled = stmt.compile()
        self.assertEqual(compiled.params["coupon_id"], 15)
        self.assertEqual(compiled.params["user_id"], 9090)
        self.assertIsNone(compiled.params["tg_id"])
        session.commit.assert_not_called()

    async def test_check_coupon_usage_matches_by_billing_or_tg(self):
        user = SimpleNamespace(id=41, tg_id=4141)
        session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: object())))

        with patch("database.coupons.resolve_user_optional", new=AsyncMock(return_value=user)):
            used = await check_coupon_usage(session, coupon_id=5, legacy_user_ref=4141)

        self.assertTrue(used)
        stmt = session.execute.await_args.args[0]
        compiled = stmt.compile()
        self.assertEqual(compiled.params["coupon_id_1"], 5)
        self.assertEqual(compiled.params["user_id_1"], 41)
        self.assertEqual(compiled.params["tg_id_1"], 4141)

    async def test_has_any_coupon_usage_returns_false_when_no_rows(self):
        user = SimpleNamespace(id=50, tg_id=5050)
        session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(first=lambda: None)))

        with patch("database.coupons.resolve_user_optional", new=AsyncMock(return_value=user)):
            has_usage = await has_any_coupon_usage(session, legacy_user_ref=5050)

        self.assertFalse(has_usage)

    async def test_has_any_coupon_usage_fallbacks_to_legacy_when_user_missing(self):
        session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(first=lambda: (1,))))

        with patch("database.coupons.resolve_user_optional", new=AsyncMock(return_value=None)):
            has_usage = await has_any_coupon_usage(session, legacy_user_ref=6060)

        self.assertTrue(has_usage)
        stmt = session.execute.await_args.args[0]
        compiled = stmt.compile()
        self.assertEqual(compiled.params["user_id_1"], 6060)
        self.assertEqual(compiled.params["tg_id_1"], 6060)

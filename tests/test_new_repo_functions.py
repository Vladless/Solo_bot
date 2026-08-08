import unittest

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from database.gifts import (
    count_gift_usages,
    get_gift_locked,
    get_gift_usage,
    mark_gift_fully_redeemed,
    record_gift_usage,
)
from database.keys import (
    count_active_keys_for_user,
    count_keys_by_server_id,
    delete_key_by_user_and_email,
    get_all_key_server_ids,
    get_key_by_user_and_email,
    get_key_client_id_by_email_and_server,
    get_user_keys_with_servers_by_email,
    update_key_post_creation_snapshot,
    update_key_renewal_snapshot,
)
from database.payments import count_successful_payments
from database.servers import (
    cluster_name_exists,
    get_cluster_name_for_server_name,
    get_enabled_server_subscription_url,
    get_panel_type_for_server,
    get_panel_types_for_cluster,
)
from database.tariffs import get_active_tariff_by_id
from database.users import (
    get_user_preferred_currency,
    mark_trial_started_if_eligible,
)


def _make_session(*, execute_return=None, execute_side_effect=None):
    """Собирает мок-session с правильно настроенным execute и отсутствующим commit/rollback.

    Если хендлер вызовет `session.commit()` — тест упадёт, потому что мы НЕ
    добавляем commit в namespace.
    """
    ns = SimpleNamespace()
    if execute_side_effect is not None:
        ns.execute = AsyncMock(side_effect=execute_side_effect)
    else:
        ns.execute = AsyncMock(return_value=execute_return)
    return ns


_MISSING = object()


def _result_with(
    *,
    scalar=_MISSING,
    scalar_one=_MISSING,
    scalar_one_or_none=_MISSING,
    all_rows=_MISSING,
    mappings_all=_MISSING,
):
    """Фейковый результат session.execute с нужными методами.

    Использует sentinel чтобы отличить "не передано" от "передано как None".
    """
    obj = SimpleNamespace()
    if scalar is not _MISSING:
        obj.scalar = lambda v=scalar: v
    if scalar_one is not _MISSING:
        obj.scalar_one = lambda v=scalar_one: v
    if scalar_one_or_none is not _MISSING:
        obj.scalar_one_or_none = lambda v=scalar_one_or_none: v
    if all_rows is not _MISSING:
        obj.all = lambda v=all_rows: v
    if mappings_all is not _MISSING:
        obj.mappings = lambda v=mappings_all: SimpleNamespace(all=lambda: v)
    return obj


class GiftsRepoTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_gift_locked_returns_orm_row(self):
        gift = SimpleNamespace(gift_id="gift_x")
        session = _make_session(execute_return=_result_with(scalar_one_or_none=gift))
        result = await get_gift_locked(session, "gift_x")
        self.assertIs(result, gift)
        session.execute.assert_awaited_once()

    async def test_get_gift_locked_returns_none_when_missing(self):
        session = _make_session(execute_return=_result_with(scalar_one_or_none=None))
        result = await get_gift_locked(session, "missing")
        self.assertIsNone(result)

    async def test_get_gift_usage_passes_composite_key(self):
        usage = SimpleNamespace()
        session = _make_session(execute_return=_result_with(scalar_one_or_none=usage))
        result = await get_gift_usage(session, "gift_1", 42)
        self.assertIs(result, usage)
        session.execute.assert_awaited_once()

    async def test_count_gift_usages_returns_int(self):
        session = _make_session(execute_return=_result_with(scalar_one=7))
        count = await count_gift_usages(session, "gift_y")
        self.assertEqual(count, 7)

    async def test_count_gift_usages_handles_none(self):
        session = _make_session(execute_return=_result_with(scalar_one=None))
        count = await count_gift_usages(session, "gift_y")
        self.assertEqual(count, 0)

    async def test_record_gift_usage_executes_insert(self):
        session = _make_session(execute_return=_result_with())
        await record_gift_usage(session, "gift_z", user_id=5, tg_id=55)
        session.execute.assert_awaited_once()
        stmt = session.execute.await_args.args[0]
        compiled = stmt.compile()
        self.assertEqual(compiled.params["gift_id"], "gift_z")
        self.assertEqual(compiled.params["user_id"], 5)
        self.assertEqual(compiled.params["tg_id"], 55)

    async def test_mark_gift_fully_redeemed_sets_is_used(self):
        session = _make_session(execute_return=_result_with())
        await mark_gift_fully_redeemed(session, "gift_a", recipient_user_id=9, recipient_tg_id=99)
        session.execute.assert_awaited_once()
        stmt = session.execute.await_args.args[0]
        compiled = stmt.compile()
        self.assertTrue(compiled.params["is_used"])
        self.assertEqual(compiled.params["recipient_user_id"], 9)
        self.assertEqual(compiled.params["recipient_tg_id"], 99)


class KeysRepoTests(unittest.IsolatedAsyncioTestCase):
    async def test_count_active_keys_for_user_filters_frozen(self):
        session = _make_session(execute_return=_result_with(scalar=3))
        result = await count_active_keys_for_user(session, 42)
        self.assertEqual(result, 3)
        stmt = session.execute.await_args.args[0]

        compiled = str(stmt.compile())
        self.assertIn("is_frozen", compiled)

    async def test_count_keys_by_server_id_returns_int(self):
        session = _make_session(execute_return=_result_with(scalar=10))
        result = await count_keys_by_server_id(session, "cluster-a")
        self.assertEqual(result, 10)

    async def test_get_all_key_server_ids_returns_only_strings(self):
        rows = [("srv1",), ("srv2",), (None,), ("srv3",)]
        session = _make_session(execute_return=_result_with(all_rows=rows))
        result = await get_all_key_server_ids(session)
        self.assertEqual(result, ["srv1", "srv2", "srv3"])

    async def test_get_key_by_user_and_email_returns_orm_row(self):
        key = SimpleNamespace(email="u@test")
        session = _make_session(execute_return=_result_with(scalar_one_or_none=key))
        result = await get_key_by_user_and_email(session, 42, "u@test")
        self.assertIs(result, key)

    async def test_delete_key_by_user_and_email_executes_delete(self):
        session = _make_session(execute_return=_result_with())
        await delete_key_by_user_and_email(session, 42, "u@test")
        session.execute.assert_awaited_once()

    async def test_get_key_client_id_by_email_and_server(self):
        session = _make_session(execute_return=_result_with(scalar="client-123"))
        result = await get_key_client_id_by_email_and_server(session, "u@test", "cluster-a")
        self.assertEqual(result, "client-123")

    async def test_update_key_renewal_snapshot_without_limits(self):
        session = _make_session(execute_return=_result_with())
        with patch("database.keys.invalidate_key_details", new=AsyncMock()):
            await update_key_renewal_snapshot(session, "u@test", tariff_id=5, apply_limits=False)
        stmt = session.execute.await_args.args[0]
        compiled = stmt.compile()
        self.assertEqual(compiled.params["tariff_id"], 5)
        self.assertNotIn("selected_device_limit", compiled.params)

    async def test_update_key_renewal_snapshot_with_limits(self):
        session = _make_session(execute_return=_result_with())
        with patch("database.keys.invalidate_key_details", new=AsyncMock()):
            await update_key_renewal_snapshot(
                session,
                "u@test",
                tariff_id=5,
                selected_device_limit=3,
                current_device_limit=3,
                selected_traffic_limit=50,
                current_traffic_limit=50,
                apply_limits=True,
            )
        stmt = session.execute.await_args.args[0]
        compiled = stmt.compile()
        self.assertEqual(compiled.params["tariff_id"], 5)
        self.assertEqual(compiled.params["selected_device_limit"], 3)
        self.assertEqual(compiled.params["selected_traffic_limit"], 50)

    async def test_update_key_post_creation_snapshot(self):
        session = _make_session(execute_return=_result_with())
        with patch("database.keys.invalidate_key_details", new=AsyncMock()):
            await update_key_post_creation_snapshot(
                session,
                user_id=10,
                email="u@test",
                selected_device_limit=2,
                selected_traffic_limit=100,
                selected_price_rub=500,
            )
        stmt = session.execute.await_args.args[0]
        compiled = stmt.compile()
        self.assertEqual(compiled.params["selected_device_limit"], 2)
        self.assertEqual(compiled.params["selected_traffic_limit"], 100)
        self.assertEqual(compiled.params["selected_price_rub"], 500)

    async def test_get_user_keys_with_servers_returns_tuples(self):
        srv = SimpleNamespace(server_name="s1", cluster_name="c1", api_url="http://x", panel_type="3x-ui")
        session = _make_session(execute_return=_result_with(all_rows=[("cid1", "s1", srv)]))
        result = await get_user_keys_with_servers_by_email(session, 42, "u@test")
        self.assertEqual(len(result), 1)
        client_id, server_id, server_info = result[0]
        self.assertEqual(client_id, "cid1")
        self.assertEqual(server_id, "s1")
        self.assertEqual(server_info["server_name"], "s1")
        self.assertEqual(server_info["panel_type"], "3x-ui")


class ServersRepoTests(unittest.IsolatedAsyncioTestCase):
    async def test_cluster_name_exists_returns_true(self):
        result_obj = SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: "row"))
        session = _make_session(execute_return=result_obj)
        ok = await cluster_name_exists(session, "cluster-x")
        self.assertTrue(ok)

    async def test_cluster_name_exists_returns_false(self):
        result_obj = SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None))
        session = _make_session(execute_return=result_obj)
        ok = await cluster_name_exists(session, "no-such")
        self.assertFalse(ok)

    async def test_get_cluster_name_for_server_name(self):
        session = _make_session(execute_return=_result_with(scalar="cluster-a"))
        result = await get_cluster_name_for_server_name(session, "srv1")
        self.assertEqual(result, "cluster-a")

    async def test_get_enabled_server_subscription_url(self):
        session = _make_session(execute_return=_result_with(scalar="https://sub/x"))
        result = await get_enabled_server_subscription_url(session, "srv1")
        self.assertEqual(result, "https://sub/x")

    async def test_get_panel_types_for_cluster(self):
        grouped = {"cluster-a": [{"panel_type": "remnawave"}, {"panel_type": "remnawave"}]}
        session = _make_session()
        with patch("database.servers.get_servers", new=AsyncMock(return_value=grouped)):
            result = await get_panel_types_for_cluster(session, "cluster-a")
        self.assertEqual(result, ["remnawave", "remnawave"])

    async def test_get_panel_type_for_server(self):
        grouped = {"cluster-a": [{"server_name": "srv1", "panel_type": "3x-ui"}]}
        session = _make_session()
        with patch("database.servers.get_servers", new=AsyncMock(return_value=grouped)):
            result = await get_panel_type_for_server(session, "srv1")
        self.assertEqual(result, "3x-ui")


class TariffsRepoTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_active_tariff_by_id_returns_only_active(self):
        tariff = SimpleNamespace(id=5, is_active=True)
        session = _make_session(execute_return=_result_with(scalar_one_or_none=tariff))
        result = await get_active_tariff_by_id(session, 5)
        self.assertIs(result, tariff)
        stmt = session.execute.await_args.args[0]
        compiled = str(stmt.compile())
        self.assertIn("is_active", compiled)

    async def test_get_active_tariff_by_id_none_when_missing(self):
        session = _make_session(execute_return=_result_with(scalar_one_or_none=None))
        result = await get_active_tariff_by_id(session, 999)
        self.assertIsNone(result)


class PaymentsRepoTests(unittest.IsolatedAsyncioTestCase):
    async def test_count_successful_payments_returns_int(self):
        session = _make_session(execute_return=_result_with(scalar=2))
        result = await count_successful_payments(session, 42)
        self.assertEqual(result, 2)

    async def test_count_successful_payments_handles_none(self):
        session = _make_session(execute_return=_result_with(scalar=None))
        result = await count_successful_payments(session, 42)
        self.assertEqual(result, 0)


class UsersRepoTests(unittest.IsolatedAsyncioTestCase):
    async def test_mark_trial_started_if_eligible_emits_conditional_update(self):
        session = _make_session(execute_return=_result_with())
        await mark_trial_started_if_eligible(session, 1234)
        session.execute.assert_awaited_once()
        stmt = session.execute.await_args.args[0]
        compiled = str(stmt.compile())

        self.assertIn("tg_id", compiled)
        self.assertIn("trial IN", compiled.replace("trial in", "trial IN"))

    async def test_get_user_preferred_currency_returns_scalar(self):
        session = _make_session(execute_return=_result_with(scalar="USD"))
        result = await get_user_preferred_currency(session, 1234)
        self.assertEqual(result, "USD")

    async def test_get_user_preferred_currency_returns_none_when_unset(self):
        session = _make_session(execute_return=_result_with(scalar=None))
        result = await get_user_preferred_currency(session, 4321)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()

import unittest

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from database.payments import add_payment
from database.temporary_data import clear_temporary_data, create_temporary_data, get_temporary_data


class PaymentsLegacyResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_add_payment_raises_when_user_not_found(self):
        session = SimpleNamespace(execute=AsyncMock())

        with patch("database.payments.resolve_user_optional", new=AsyncMock(return_value=None)):
            with self.assertRaises(ValueError):
                await add_payment(
                    session=session,
                    legacy_user_ref=999999,
                    amount=100.0,
                    payment_system="test",
                )

        session.execute.assert_not_called()

    async def test_add_payment_uses_resolved_user_and_returns_internal_id(self):
        user = SimpleNamespace(id=77, tg_id=5005)
        result = SimpleNamespace(scalar_one=lambda: 1234)
        session = SimpleNamespace(execute=AsyncMock(return_value=result))

        with patch("database.payments.resolve_user_optional", new=AsyncMock(return_value=user)):
            internal_id = await add_payment(
                session=session,
                legacy_user_ref=5005,
                amount=50.0,
                payment_system="test",
                status="success",
            )

        self.assertEqual(internal_id, 1234)
        session.execute.assert_awaited_once()

    async def test_add_payment_accepts_tg_id_alias_keyword(self):
        user = SimpleNamespace(id=88, tg_id=8080)
        result = SimpleNamespace(scalar_one=lambda: 555)
        session = SimpleNamespace(execute=AsyncMock(return_value=result))

        with patch("database.payments.resolve_user_optional", new=AsyncMock(return_value=user)) as resolve_mock:
            internal_id = await add_payment(
                session=session,
                tg_id=8080,
                amount=99.0,
                payment_system="alias-test",
            )

        self.assertEqual(internal_id, 555)
        resolve_mock.assert_awaited_once_with(session, 8080)


class TemporaryDataLegacyResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_temporary_data_raises_when_user_missing(self):
        session = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())

        with patch("database.temporary_data.resolve_user_optional", new=AsyncMock(return_value=None)):
            with self.assertRaises(ValueError):
                await create_temporary_data(session, legacy_user_ref=1010, state="s", data={"a": 1})

        session.execute.assert_not_called()
        session.commit.assert_not_called()

    async def test_create_temporary_data_executes_when_user_found(self):
        user = SimpleNamespace(id=12, tg_id=1200)
        session = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())

        with patch("database.temporary_data.resolve_user_optional", new=AsyncMock(return_value=user)):
            await create_temporary_data(session, legacy_user_ref=1200, state="waiting", data={"x": 1})

        session.execute.assert_awaited_once()
        session.commit.assert_not_called()

    async def test_get_temporary_data_falls_back_to_tg_when_user_missing(self):
        row = SimpleNamespace(state="waiting_for_payment", data={"required_amount": 100})
        session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: row)))

        with patch("database.temporary_data.resolve_user_optional", new=AsyncMock(return_value=None)):
            data = await get_temporary_data(session, legacy_user_ref=4321)

        self.assertEqual(data, {"state": "waiting_for_payment", "data": {"required_amount": 100}})

    async def test_clear_temporary_data_uses_resolved_user(self):
        user = SimpleNamespace(id=222, tg_id=22)
        session = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock())

        with patch("database.temporary_data.resolve_user_optional", new=AsyncMock(return_value=user)):
            await clear_temporary_data(session, legacy_user_ref=22)

        session.execute.assert_awaited_once()
        session.commit.assert_not_called()


class PaymentRenewalTimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_renewal_takes_expiry_from_quote_when_key_expired(self):
        from handlers.payments.utils import _handle_temp_state

        fixed_now = datetime(2026, 1, 10, 12, 0, 0)
        expired_at = int((fixed_now - timedelta(days=2)).timestamp() * 1000)
        expected_new_expiry = int((fixed_now + timedelta(days=30)).timestamp() * 1000)
        quote = SimpleNamespace(net_cost_rub=100, new_expiry_ms=expected_new_expiry)

        session = SimpleNamespace()
        data = {
            "tariff_id": 7,
            "client_id": "client-1",
            "cost": 100,
            "email": "user@example.com",
            "new_expiry_time": expired_at + int(timedelta(days=30).total_seconds() * 1000),
            "selected_duration_days": 30,
        }

        with (
            patch("handlers.payments.utils.get_balance", new=AsyncMock(return_value=500)),
            patch(
                "handlers.payments.utils.get_key_by_server",
                new=AsyncMock(return_value=SimpleNamespace(expiry_time=expired_at)),
            ),
            patch("services.keys.compute_renewal_quote", new=AsyncMock(return_value=quote)) as quote_mock,
            patch("handlers.keys.renew.flow.complete_key_renewal", new=AsyncMock()) as complete_mock,
            patch("handlers.payments.utils.clear_temporary_data", new=AsyncMock()) as clear_mock,
            patch("handlers.payments.utils.datetime") as datetime_mock,
        ):
            datetime_mock.utcnow.return_value = fixed_now

            handled = await _handle_temp_state(
                session=session,
                user_id=12345,
                state="waiting_for_renewal_payment",
                data=data,
                amount=100,
            )

        self.assertTrue(handled)
        self.assertEqual(quote_mock.await_args.kwargs["current_expiry_ms"], expired_at)
        self.assertEqual(complete_mock.await_args.kwargs["new_expiry_time"], expected_new_expiry)
        clear_mock.assert_awaited_once_with(session, 12345)

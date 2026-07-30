import unittest

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from services.payments.pipeline import (
    ParsedPayment,
    PipelineResult,
    process_cancelled_payment,
    process_success_payment,
)


class _FakeSessionContext:
    """Async context manager, который возвращает переданный session при __aenter__."""

    def __init__(self, session) -> None:
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _sessionmaker_returning(session):
    """Фейковый ``async_session_maker`` — CallableType -> контекст."""
    return lambda: _FakeSessionContext(session)


class _FakeLockedSession:
    """Сессия под новый пайплайн: 1-й execute — advisory lock, 2-й — locked SELECT платежа."""

    def __init__(self, payment_row) -> None:
        self.payment_row = payment_row
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.executed = []

    async def execute(self, stmt, params=None):
        self.executed.append(stmt)
        row = self.payment_row if len(self.executed) >= 2 else None
        return SimpleNamespace(
            scalar_one_or_none=lambda: row,
            scalar=lambda: None,
            fetchone=lambda: None,
        )


def _payment_row(**overrides):
    base = {"id": 7, "tg_id": 42, "status": "pending", "currency": None, "original_amount": None}
    base.update(overrides)
    return SimpleNamespace(**base)


class ProcessSuccessPaymentTests(unittest.IsolatedAsyncioTestCase):
    async def test_idempotent_when_already_success(self):
        """Повторный webhook: строка в БД уже success → сразу already_processed, кэш не участвует."""
        session = _FakeLockedSession(_payment_row(id=1, status="success"))
        parsed = ParsedPayment(payment_id="p1", tg_id=42, amount=500.0)

        with (
            patch("services.payments.pipeline.async_session_maker", _sessionmaker_returning(session)),
            patch(
                "services.payments.pipeline.get_payment_by_payment_id",
                new=AsyncMock(return_value={"id": 1, "tg_id": 42, "status": "pending"}),
            ) as cache_read_mock,
            patch("services.payments.pipeline.update_payment_status", new=AsyncMock()) as upd_mock,
            patch("services.payments.pipeline.add_payment", new=AsyncMock()) as add_mock,
            patch("services.payments.pipeline.update_balance", new=AsyncMock()) as balance_mock,
            patch(
                "services.payments.pipeline.send_payment_success_notification",
                new=AsyncMock(),
            ) as notify_mock,
            patch("services.payments.pipeline.invalidate_payment_cache", new=AsyncMock()),
        ):
            result = await process_success_payment("yookassa", parsed)

        self.assertTrue(result.ok)
        self.assertTrue(result.already_processed)
        cache_read_mock.assert_not_awaited()
        upd_mock.assert_not_awaited()
        add_mock.assert_not_awaited()
        balance_mock.assert_not_awaited()
        notify_mock.assert_not_awaited()
        session.commit.assert_not_awaited()
        self.assertGreaterEqual(len(session.executed), 2)

    async def test_pending_to_success(self):
        """Payment pending → update_payment_status(success) + balance + notify."""
        session = _FakeLockedSession(_payment_row(id=7))
        parsed = ParsedPayment(payment_id="p1", tg_id=42, amount=500.0)

        with (
            patch("services.payments.pipeline.async_session_maker", _sessionmaker_returning(session)),
            patch(
                "services.payments.pipeline.update_payment_status",
                new=AsyncMock(return_value=True),
            ) as upd_mock,
            patch("services.payments.pipeline.add_payment", new=AsyncMock()) as add_mock,
            patch("services.payments.pipeline.update_balance", new=AsyncMock()) as balance_mock,
            patch(
                "services.payments.pipeline.send_payment_success_notification",
                new=AsyncMock(),
            ) as notify_mock,
            patch(
                "services.payments.pipeline.invalidate_payment_cache",
                new=AsyncMock(),
            ) as cache_mock,
        ):
            result = await process_success_payment("robokassa", parsed)

        self.assertTrue(result.ok)
        self.assertFalse(result.already_processed)
        upd_mock.assert_awaited_once()
        self.assertEqual(upd_mock.await_args.kwargs["internal_id"], 7)
        add_mock.assert_not_awaited()
        balance_mock.assert_awaited_once_with(session, 42, 500.0)
        notify_mock.assert_awaited_once()
        session.commit.assert_awaited_once()
        cache_mock.assert_awaited_once_with("p1")

    async def test_fresh_payment_uses_add_payment(self):
        """Нет строки в БД и в кэше → add_payment создаёт новую."""
        session = _FakeLockedSession(None)
        parsed = ParsedPayment(payment_id="p2", tg_id=100, amount=750.0, currency="RUB", metadata={"k": "v"})

        with (
            patch("services.payments.pipeline.async_session_maker", _sessionmaker_returning(session)),
            patch(
                "services.payments.pipeline.get_payment_by_payment_id",
                new=AsyncMock(return_value=None),
            ),
            patch("services.payments.pipeline.update_payment_status", new=AsyncMock()) as upd_mock,
            patch("services.payments.pipeline.add_payment", new=AsyncMock()) as add_mock,
            patch("services.payments.pipeline.update_balance", new=AsyncMock()) as balance_mock,
            patch(
                "services.payments.pipeline.send_payment_success_notification",
                new=AsyncMock(),
            ),
            patch("services.payments.pipeline.invalidate_payment_cache", new=AsyncMock()),
        ):
            result = await process_success_payment("kassai", parsed)

        self.assertTrue(result.ok)
        upd_mock.assert_not_awaited()
        add_mock.assert_awaited_once()
        add_kwargs = add_mock.await_args.kwargs
        self.assertEqual(add_kwargs["tg_id"], 100)
        self.assertEqual(add_kwargs["amount"], 750.0)
        self.assertEqual(add_kwargs["payment_system"], "kassai")
        self.assertEqual(add_kwargs["status"], "success")
        self.assertEqual(add_kwargs["payment_id"], "p2")
        self.assertEqual(add_kwargs["metadata"], {"k": "v"})
        balance_mock.assert_awaited_once_with(session, 100, 750.0)

    async def test_cached_success_short_circuits(self):
        """Нет строки в БД, но кэш говорит success → already_processed без add_payment."""
        session = _FakeLockedSession(None)
        parsed = ParsedPayment(payment_id="p2c", tg_id=100, amount=750.0)

        with (
            patch("services.payments.pipeline.async_session_maker", _sessionmaker_returning(session)),
            patch(
                "services.payments.pipeline.get_payment_by_payment_id",
                new=AsyncMock(return_value={"id": None, "tg_id": 100, "status": "success"}),
            ),
            patch("services.payments.pipeline.add_payment", new=AsyncMock()) as add_mock,
            patch("services.payments.pipeline.update_balance", new=AsyncMock()) as balance_mock,
            patch("services.payments.pipeline.send_payment_success_notification", new=AsyncMock()),
            patch("services.payments.pipeline.invalidate_payment_cache", new=AsyncMock()),
        ):
            result = await process_success_payment("kassai", parsed)

        self.assertTrue(result.ok)
        self.assertTrue(result.already_processed)
        add_mock.assert_not_awaited()
        balance_mock.assert_not_awaited()
        session.commit.assert_not_awaited()

    async def test_update_status_failure_returns_error(self):
        """update_payment_status вернул False → PipelineResult(ok=False)."""
        session = _FakeLockedSession(_payment_row(id=5, tg_id=1))
        parsed = ParsedPayment(payment_id="p3", tg_id=1, amount=1.0)

        with (
            patch("services.payments.pipeline.async_session_maker", _sessionmaker_returning(session)),
            patch(
                "services.payments.pipeline.update_payment_status",
                new=AsyncMock(return_value=False),
            ),
            patch("services.payments.pipeline.update_balance", new=AsyncMock()) as balance_mock,
            patch("services.payments.pipeline.send_payment_success_notification", new=AsyncMock()),
            patch("services.payments.pipeline.invalidate_payment_cache", new=AsyncMock()),
        ):
            result = await process_success_payment("yoomoney", parsed)

        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)
        balance_mock.assert_not_awaited()
        session.commit.assert_not_awaited()

    async def test_credit_amount_override(self):
        """cryptobot: parsed.amount=USDT-сумма, credit_amount_override=RUB, поля пишутся в залоченную строку."""
        row = _payment_row(id=9)
        session = _FakeLockedSession(row)
        parsed = ParsedPayment(payment_id="p4", tg_id=42, amount=10.0, currency="USDT")

        with (
            patch("services.payments.pipeline.async_session_maker", _sessionmaker_returning(session)),
            patch(
                "services.payments.pipeline.update_payment_status",
                new=AsyncMock(return_value=True),
            ),
            patch("services.payments.pipeline.update_balance", new=AsyncMock()) as balance_mock,
            patch("services.payments.pipeline.send_payment_success_notification", new=AsyncMock()) as notify_mock,
            patch("services.payments.pipeline.invalidate_payment_cache", new=AsyncMock()),
        ):
            result = await process_success_payment(
                "CRYPTOBOT",
                parsed,
                credit_amount_override=900.0,
                update_currency="USDT",
                update_original_amount=10.0,
            )

        self.assertTrue(result.ok)

        balance_mock.assert_awaited_once_with(session, 42, 900.0)
        notify_mock.assert_awaited_once_with(42, 900.0, session)

        self.assertEqual(row.currency, "USDT")
        self.assertEqual(row.original_amount, 10.0)

    async def test_metadata_patch_passed_to_update(self):
        """metadata_patch пробрасывается в update_payment_status."""
        session = _FakeLockedSession(_payment_row(id=11))
        parsed = ParsedPayment(payment_id="p5", tg_id=42, amount=500.0)

        with (
            patch("services.payments.pipeline.async_session_maker", _sessionmaker_returning(session)),
            patch(
                "services.payments.pipeline.update_payment_status",
                new=AsyncMock(return_value=True),
            ) as upd_mock,
            patch("services.payments.pipeline.update_balance", new=AsyncMock()),
            patch("services.payments.pipeline.send_payment_success_notification", new=AsyncMock()),
            patch("services.payments.pipeline.invalidate_payment_cache", new=AsyncMock()),
        ):
            await process_success_payment("CRYPTOBOT", parsed, metadata_patch={"fx": {"rate": 90.5}})

        upd_mock.assert_awaited_once()
        self.assertEqual(upd_mock.await_args.kwargs["metadata_patch"], {"fx": {"rate": 90.5}})


class ProcessCancelledPaymentTests(unittest.IsolatedAsyncioTestCase):
    async def test_idempotent_when_already_cancelled(self):
        session = SimpleNamespace(commit=AsyncMock())
        parsed = ParsedPayment(payment_id="p1", tg_id=42, amount=500.0)

        with (
            patch("services.payments.pipeline.async_session_maker", _sessionmaker_returning(session)),
            patch(
                "services.payments.pipeline.get_payment_by_payment_id",
                new=AsyncMock(return_value={"id": 1, "status": "cancelled"}),
            ),
            patch("services.payments.pipeline.update_payment_status", new=AsyncMock()) as upd,
            patch("services.payments.pipeline.add_payment", new=AsyncMock()),
            patch("services.payments.pipeline.invalidate_payment_cache", new=AsyncMock()),
        ):
            result = await process_cancelled_payment("yookassa", parsed)

        self.assertTrue(result.ok)
        self.assertTrue(result.already_processed)
        upd.assert_not_awaited()
        session.commit.assert_not_awaited()

    async def test_pending_to_cancelled(self):
        session = SimpleNamespace(commit=AsyncMock())
        parsed = ParsedPayment(payment_id="p1", tg_id=42, amount=500.0)

        with (
            patch("services.payments.pipeline.async_session_maker", _sessionmaker_returning(session)),
            patch(
                "services.payments.pipeline.get_payment_by_payment_id",
                new=AsyncMock(return_value={"id": 1, "status": "pending"}),
            ),
            patch(
                "services.payments.pipeline.update_payment_status",
                new=AsyncMock(return_value=True),
            ) as upd,
            patch("services.payments.pipeline.invalidate_payment_cache", new=AsyncMock()),
        ):
            result = await process_cancelled_payment("yookassa", parsed)

        self.assertTrue(result.ok)
        upd.assert_awaited_once()
        session.commit.assert_awaited_once()

    async def test_fresh_cancelled_uses_add_payment(self):
        session = SimpleNamespace(commit=AsyncMock())
        parsed = ParsedPayment(payment_id="p1", tg_id=42, amount=0.0)

        with (
            patch("services.payments.pipeline.async_session_maker", _sessionmaker_returning(session)),
            patch(
                "services.payments.pipeline.get_payment_by_payment_id",
                new=AsyncMock(return_value=None),
            ),
            patch("services.payments.pipeline.add_payment", new=AsyncMock()) as add,
            patch("services.payments.pipeline.invalidate_payment_cache", new=AsyncMock()),
        ):
            result = await process_cancelled_payment("heleket", parsed, new_status="failed")

        self.assertTrue(result.ok)
        add.assert_awaited_once()
        self.assertEqual(add.await_args.kwargs["status"], "failed")
        session.commit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()

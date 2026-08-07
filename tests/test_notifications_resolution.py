import unittest

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from database.notifications import add_notification, check_notification_time, check_notification_time_bulk


class NotificationsResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_add_notification_uses_resolved_user_and_tg_mirror(self):
        user = SimpleNamespace(id=12, tg_id=1212)
        session = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())

        with patch("database.notifications.resolve_user_optional", new=AsyncMock(return_value=user)):
            await add_notification(session, legacy_user_ref=1212, notification_type="n1")

        session.execute.assert_awaited_once()
        stmt = session.execute.await_args.args[0]
        compiled = stmt.compile()
        values = set(compiled.params.values())
        self.assertIn(12, values)
        self.assertIn(1212, values)
        self.assertIn("n1", values)
        session.commit.assert_awaited_once()

    async def test_add_notification_skips_commit_when_disabled(self):
        user = SimpleNamespace(id=12, tg_id=1212)
        session = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())

        with patch("database.notifications.resolve_user_optional", new=AsyncMock(return_value=user)):
            await add_notification(session, legacy_user_ref=1212, notification_type="n1", commit=False)

        session.execute.assert_awaited_once()
        session.commit.assert_not_called()

    async def test_check_notification_time_returns_true_when_user_missing(self):
        session = SimpleNamespace(execute=AsyncMock())

        with patch("database.notifications.resolve_user_optional", new=AsyncMock(return_value=None)):
            allowed = await check_notification_time(session, legacy_user_ref=7777, notification_type="n2", hours=12)

        self.assertTrue(allowed)
        session.execute.assert_not_called()

    async def test_check_notification_time_handles_naive_db_timestamp(self):
        user = SimpleNamespace(id=33, tg_id=3333)
        now = datetime(2026, 3, 20, 20, 0, 0, tzinfo=UTC)
        old_naive = (now - timedelta(hours=13)).replace(tzinfo=None)
        recent_naive = (now - timedelta(hours=2)).replace(tzinfo=None)

        old_session = SimpleNamespace(
            execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: old_naive))
        )
        recent_session = SimpleNamespace(
            execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: recent_naive))
        )

        with (
            patch("database.notifications.resolve_user_optional", new=AsyncMock(return_value=user)),
            patch("database.notifications._utc_now", return_value=now),
        ):
            old_allowed = await check_notification_time(
                old_session, legacy_user_ref=3333, notification_type="n3", hours=12
            )
            recent_allowed = await check_notification_time(
                recent_session, legacy_user_ref=3333, notification_type="n3", hours=12
            )

        self.assertTrue(old_allowed)
        self.assertFalse(recent_allowed)

    async def test_check_notification_time_bulk_includes_missing_and_old(self):
        now = datetime(2026, 3, 20, 20, 0, 0, tzinfo=UTC)
        session = SimpleNamespace()
        session.execute = AsyncMock(
            return_value=[
                SimpleNamespace(
                    user_id=1,
                    notification_type="n1",
                    last_notification_time=now - timedelta(hours=1),
                ),
                SimpleNamespace(
                    user_id=2,
                    notification_type="n2",
                    last_notification_time=(now - timedelta(hours=13)).replace(tzinfo=None),
                ),
            ]
        )
        items = [(100, "n1"), (200, "n2"), (300, "n3")]

        with (
            patch("database.notifications._utc_now", return_value=now),
            patch(
                "database.notifications._map_legacy_refs_to_user_ids",
                new=AsyncMock(return_value={100: 1, 200: 2}),
            ),
        ):
            result = await check_notification_time_bulk(session, items=items, hours=12)

        self.assertNotIn((100, "n1"), result)
        self.assertIn((200, "n2"), result)
        self.assertIn((300, "n3"), result)

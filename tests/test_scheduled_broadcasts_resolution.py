import unittest

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from database.scheduled_broadcasts import create_scheduled_broadcast, list_scheduled_broadcasts


class ScheduledBroadcastResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_scheduled_broadcast_resolves_user_and_mirror_tg(self):
        session = SimpleNamespace(add=Mock(), flush=AsyncMock(), commit=AsyncMock(), refresh=AsyncMock())
        creator = SimpleNamespace(id=10, tg_id=555)

        with patch("database.scheduled_broadcasts.resolve_user_optional", new=AsyncMock(return_value=creator)):
            broadcast = await create_scheduled_broadcast(
                session=session,
                created_by_tg_id=999,
                send_to="all",
                cluster_name=None,
                text="hello",
                photo=None,
                keyboard_json=None,
                scheduled_for=SimpleNamespace(),
                workers=5,
                messages_per_second=20,
            )

        self.assertEqual(broadcast.created_by_user_id, 10)
        self.assertEqual(broadcast.created_by_tg_id, 555)
        session.flush.assert_awaited_once()
        session.commit.assert_not_called()
        session.refresh.assert_awaited_once_with(broadcast)

    async def test_create_scheduled_broadcast_keeps_legacy_tg_when_user_missing(self):
        session = SimpleNamespace(add=Mock(), flush=AsyncMock(), commit=AsyncMock(), refresh=AsyncMock())

        with patch("database.scheduled_broadcasts.resolve_user_optional", new=AsyncMock(return_value=None)):
            broadcast = await create_scheduled_broadcast(
                session=session,
                created_by_tg_id=123456,
                send_to="all",
                cluster_name=None,
                text="hello",
                photo=None,
                keyboard_json=None,
                scheduled_for=SimpleNamespace(),
                workers=5,
                messages_per_second=20,
            )

        self.assertIsNone(broadcast.created_by_user_id)
        self.assertEqual(broadcast.created_by_tg_id, 123456)

    async def test_list_scheduled_broadcasts_filters_by_created_by_tg(self):
        rows = [SimpleNamespace(id="a"), SimpleNamespace(id="b")]
        session = SimpleNamespace(
            execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows)))
        )

        result = await list_scheduled_broadcasts(
            session=session,
            statuses=None,
            created_by_tg_id=777,
            limit=10,
            offset=0,
        )

        self.assertEqual(result, rows)
        session.execute.assert_awaited_once()

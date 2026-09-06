import unittest

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import database  # noqa: F401  порядок импорта: иначе core.bootstrap уходит в цикл

from database.identities import _transfer_user_data
from services.payments import process_referrals


SRC = 2
DST = 1


class RecordingSession:
    """Пишет очередь запросов."""

    def __init__(self, dst_has_referrer: bool) -> None:
        self.sql: list[str] = []
        self._dst_has_referrer = dst_has_referrer
        self.flush = AsyncMock()
        self.refresh = AsyncMock()

    async def execute(self, stmt, params=None):
        self.sql.append(" ".join(str(stmt).split()))
        return SimpleNamespace(
            first=lambda: (DST,) if self._dst_has_referrer else None,
            scalar_one_or_none=lambda: None,
            scalars=lambda: SimpleNamespace(all=list),
        )

    def add(self, _obj) -> None:
        pass

    def _referral_steps(self) -> list[str]:
        out = []
        for sql in self.sql:
            if "referrals" not in sql.lower():
                continue
            head = sql.split()[0].upper()
            if head == "DELETE" and "USING" not in sql.upper() and ":src" in sql:
                out.append("delete_cross")
            elif head == "DELETE" and "USING" not in sql.upper():
                out.append("delete_src_referrer")
            elif head == "UPDATE" and "SET referred_user_id" in sql:
                out.append("update_referred")
            elif head == "UPDATE" and "SET referrer_user_id" in sql:
                out.append("update_referrer")
        return out


async def _transfer(dst_has_referrer: bool) -> list[str]:
    session = RecordingSession(dst_has_referrer)
    await _transfer_user_data(session, SRC, DST, 900001, "idt-x")
    return session._referral_steps()


class TransferReferralsTests(unittest.IsolatedAsyncioTestCase):
    """Слияние не создаёт самореферала."""

    async def test_встречная_связь_снимается_до_переноса(self):
        steps = await _transfer(dst_has_referrer=False)
        self.assertIn("delete_cross", steps)
        self.assertLess(steps.index("delete_cross"), steps.index("update_referred"))
        self.assertLess(steps.index("delete_cross"), steps.index("update_referrer"))

    async def test_второй_пригласитель_не_переносится(self):
        self.assertIn("delete_src_referrer", await _transfer(dst_has_referrer=True))

    async def test_без_пригласителя_у_адресата_связь_переносится(self):
        self.assertNotIn("delete_src_referrer", await _transfer(dst_has_referrer=False))


class SelfReferralPayoutTests(unittest.IsolatedAsyncioTestCase):
    """По самореферальной связи бонус не начисляется."""

    async def _payout(self, referrer_id: int) -> dict:
        with (
            patch("services.payments.resolve_user_optional", new=AsyncMock(return_value=SimpleNamespace(id=1))),
            patch(
                "services.payments.get_referral_by_referred_id",
                new=AsyncMock(return_value={"referrer_user_id": referrer_id, "reward_issued": False}),
            ),
            patch("services.payments.update_balance", new=AsyncMock()),
            patch("services.payments.add_payment", new=AsyncMock()),
            patch("services.payments.mark_referral_reward_issued", new=AsyncMock()),
        ):
            return await process_referrals(AsyncMock(), 1, 1000.0)

    async def test_себе_бонус_не_начисляется(self):
        self.assertEqual(await self._payout(1), {})

    async def test_обычный_пригласитель_получает_бонус(self):
        self.assertEqual(await self._payout(42), {42: 250.0})


if __name__ == "__main__":
    unittest.main()

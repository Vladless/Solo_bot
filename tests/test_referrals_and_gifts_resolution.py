import unittest

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.v2.routes.referrals import apply_referral
from api.v2.schemas.web_public import ReferralApplyRequest
from database.gifts import store_gift_link
from database.referrals import add_referral, get_total_referrals
from services.gifts import redeem_gift


class ReferralsResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_add_referral_creates_relation_for_resolved_users(self):
        referred = SimpleNamespace(id=10, tg_id=1010)
        referrer = SimpleNamespace(id=20, tg_id=2020)
        session = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())

        with patch(
            "database.referrals.resolve_user_optional",
            new=AsyncMock(side_effect=[referred, referrer]),
        ):
            await add_referral(session, referred_legacy=1010, referrer_legacy=2020)

        session.execute.assert_awaited_once()
        session.commit.assert_not_called()

    async def test_add_referral_ignores_self_referral(self):
        same_user = SimpleNamespace(id=30, tg_id=3030)
        session = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())

        with patch(
            "database.referrals.resolve_user_optional",
            new=AsyncMock(side_effect=[same_user, same_user]),
        ):
            await add_referral(session, referred_legacy=3030, referrer_legacy=3030)

        session.execute.assert_not_awaited()
        session.commit.assert_not_awaited()

    async def test_get_total_referrals_returns_zero_when_user_missing(self):
        session = SimpleNamespace(execute=AsyncMock())
        with patch("database.referrals.resolve_user_optional", new=AsyncMock(return_value=None)):
            total = await get_total_referrals(session, referrer_legacy=4040)
        self.assertEqual(total, 0)
        session.execute.assert_not_awaited()

    async def test_apply_referral_accepts_site_referral_code(self):
        session = object()
        identity = SimpleNamespace(id="ident-email")
        body = ReferralApplyRequest(referrer_code="https://example.com/referral/321")
        referrer_user = SimpleNamespace(id=321, tg_id=None)
        referred_user = SimpleNamespace(id=555, tg_id=None)

        with (
            patch("api.v2.routes.referrals.idb.ensure_billing_user_for_identity", new=AsyncMock(return_value=555)),
            patch(
                "api.v2.routes.referrals.resolve_user_optional",
                new=AsyncMock(side_effect=[referrer_user, referred_user]),
            ),
            patch("api.v2.routes.referrals.get_referral_by_referred_id", new=AsyncMock(return_value=None)),
            patch("api.v2.routes.referrals.add_referral", new=AsyncMock()) as add_referral_mock,
        ):
            result = await apply_referral(body, session=session, identity=identity)

        self.assertTrue(result.ok)
        self.assertEqual(result.referrer_code, "321")
        self.assertEqual(result.referrer_user_id, 321)
        self.assertEqual(result.referred_user_id, 555)
        add_referral_mock.assert_awaited_once_with(session, 555, 321)


class GiftsResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_store_gift_link_resolves_sender(self):
        sender = SimpleNamespace(id=77, tg_id=7007)
        session = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())

        with patch("database.gifts.resolve_user_optional", new=AsyncMock(return_value=sender)):
            ok = await store_gift_link(
                session=session,
                gift_id="gift_1",
                sender_legacy_ref=7007,
                selected_months=1,
                expiry_time=datetime.now(UTC),
                gift_link="https://t.me/test",
                tariff_id=1,
                max_usages=1,
            )

        self.assertTrue(ok)
        session.execute.assert_awaited_once()
        session.commit.assert_not_awaited()

    async def test_store_gift_link_raises_when_sender_missing(self):
        session = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())
        with patch("database.gifts.resolve_user_optional", new=AsyncMock(return_value=None)):
            with self.assertRaises(ValueError):
                await store_gift_link(
                    session=session,
                    gift_id="gift_2",
                    sender_legacy_ref=9999,
                    selected_months=1,
                    expiry_time=datetime.now(UTC),
                    gift_link="https://t.me/test",
                )

        session.execute.assert_not_awaited()
        session.commit.assert_not_awaited()

    async def test_redeem_gift_uses_billing_user_id_without_telegram(self):
        gift_info = SimpleNamespace(
            gift_id="gift_1",
            sender_user_id=77,
            sender_tg_id=7007,
            recipient_user_id=None,
            is_unlimited=False,
            is_used=False,
            max_usages=1,
            tariff_id=5,
            expiry_time=None,
            selected_device_limit=2,
            selected_traffic_gb=50,
            selected_price_rub=990,
        )
        billing_user = SimpleNamespace(id=555, tg_id=None, created_at=datetime.now(UTC), trial=0)
        tariff_dict = {"id": 5, "name": "Gift tariff", "duration_days": 30, "group_code": "gifts"}
        session = SimpleNamespace(execute=AsyncMock(), flush=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())

        with (
            patch("services.gifts.get_gift_locked", new=AsyncMock(return_value=gift_info)),
            patch("services.gifts.resolve_user_optional", new=AsyncMock(return_value=billing_user)),
            patch("services.gifts.get_gift_usage", new=AsyncMock(return_value=None)),
            patch("services.gifts.count_gift_usages", new=AsyncMock(return_value=0)),
            patch("services.gifts.get_referral_by_referred_id", new=AsyncMock(return_value=None)),
            patch("hooks.hooks.run_hooks", new=AsyncMock(return_value=[])),
            patch("services.gifts.add_referral", new=AsyncMock()) as add_referral_mock,
            patch("services.gifts.update_trial", new=AsyncMock()),
            patch("services.gifts.get_tariff_by_id", new=AsyncMock(return_value=tariff_dict)),
            patch("services.gifts.record_gift_usage", new=AsyncMock()),
            patch("services.gifts.mark_gift_fully_redeemed", new=AsyncMock()),
            patch("services.keys.create_vpn_key_headless", new=AsyncMock()) as create_key_mock,
        ):
            result = await redeem_gift(session, "gift_1", 555)

        self.assertEqual(result.gift_id, "gift_1")
        self.assertEqual(result.tariff_id, 5)
        self.assertEqual(result.duration_days, 30)
        add_referral_mock.assert_awaited_once_with(session, 555, 77)
        create_key_mock.assert_awaited_once()
        self.assertEqual(create_key_mock.await_args.kwargs["tg_id"], 555)
        self.assertTrue(create_key_mock.await_args.kwargs["skip_balance_charge"])

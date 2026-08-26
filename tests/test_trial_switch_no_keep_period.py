import asyncio
import unittest

from unittest.mock import patch

import database  # noqa: F401 — services.keys не может быть первым импортом: цикл через core.bootstrap
import services.keys as keys_service
import services.tariffs.tariff_display as tariff_display

from core.bootstrap import MODES_CONFIG


DAY_MS = 86_400_000
NOW_MS = 1_700_000_000_000

TRIAL = {"id": 1, "group_code": "trial", "duration_days": 7, "price_rub": 0, "traffic_limit": 50, "device_limit": 1}
PAID_TRIAL = {**TRIAL, "id": 2, "price_rub": 210}
MONTHLY = {
    "id": 10,
    "group_code": "basic",
    "duration_days": 30,
    "price_rub": 300,
    "traffic_limit": 200,
    "device_limit": 3,
}
MONTHLY_PRO = {
    "id": 11,
    "group_code": "basic",
    "duration_days": 30,
    "price_rub": 600,
    "traffic_limit": 500,
    "device_limit": 5,
}

TARIFFS = {int(t["id"]): t for t in (TRIAL, PAID_TRIAL, MONTHLY, MONTHLY_PRO)}


async def _fake_get_tariff_by_id(_session, tariff_id):
    return TARIFFS.get(int(tariff_id))


async def _fake_pricing(_session, *, tariff_id, **_kwargs):
    tariff = TARIFFS[int(tariff_id)]
    return keys_service.RenewalPricing(
        base_price_rub=int(tariff["price_rub"]),
        discount_rub=0,
        final_price_rub=int(tariff["price_rub"]),
        coupon_id=None,
        applied_coupon_code=None,
        total_gb=int(tariff["traffic_limit"]),
        balance=0.0,
        required_amount=int(tariff["price_rub"]),
        payment_required=True,
        duration_days=int(tariff["duration_days"]),
        selected_device_limit=int(tariff["device_limit"]),
        selected_traffic_limit=int(tariff["traffic_limit"]),
    )


def _quote(current: dict, target: dict, keep_period: bool, remaining_days: float):
    """Считает смену current → target настоящим compute_renewal_quote."""
    saved = {key: MODES_CONFIG.get(key) for key in ("RENEWAL_SWITCH_KEEP_PERIOD", "RENEWAL_CREDIT_AS_DAYS")}
    MODES_CONFIG["RENEWAL_SWITCH_KEEP_PERIOD"] = keep_period
    MODES_CONFIG["RENEWAL_CREDIT_AS_DAYS"] = False
    try:
        with (
            patch.object(keys_service, "get_tariff_by_id", _fake_get_tariff_by_id),
            patch.object(tariff_display, "get_tariff_by_id", _fake_get_tariff_by_id),
            patch.object(keys_service, "calculate_renewal_pricing", _fake_pricing),
        ):
            return asyncio.run(
                keys_service.compute_renewal_quote(
                    None,
                    billing_user_id=1,
                    key_email="a@b.c",
                    current_tariff_id=int(current["id"]),
                    current_selected_device=None,
                    current_selected_traffic=None,
                    current_expiry_ms=NOW_MS + int(remaining_days * DAY_MS),
                    now_ms=NOW_MS,
                    new_tariff_id=int(target["id"]),
                    new_selected_device=None,
                    new_selected_traffic=None,
                )
            )
    finally:
        for key, value in saved.items():
            if value is None:
                MODES_CONFIG.pop(key, None)
            else:
                MODES_CONFIG[key] = value


class TrialSwitchTests(unittest.TestCase):
    def test_уход_с_триала_даёт_полный_период_и_полную_цену(self):
        quote = _quote(TRIAL, MONTHLY, keep_period=True, remaining_days=3.0)
        self.assertTrue(quote.is_switch)
        self.assertFalse(quote.keeps_period)
        self.assertEqual(quote.net_cost_rub, 300)
        self.assertEqual(quote.new_expiry_ms, NOW_MS + 30 * DAY_MS)

    def test_ловушки_нет_и_с_платным_триалом(self):
        quote = _quote(PAID_TRIAL, MONTHLY, keep_period=True, remaining_days=3.0)
        self.assertFalse(quote.keeps_period)
        self.assertEqual(quote.new_expiry_ms, NOW_MS + 30 * DAY_MS)

    def test_остаток_платного_триала_возвращается_а_не_сгорает(self):
        quote = _quote(PAID_TRIAL, MONTHLY, keep_period=True, remaining_days=3.0)
        self.assertEqual(quote.credit_rub, 90)
        self.assertEqual(quote.net_cost_rub, 210)

    def test_у_бесплатного_триала_возвращать_нечего(self):
        quote = _quote(TRIAL, MONTHLY, keep_period=True, remaining_days=3.0)
        self.assertEqual(quote.credit_rub, 0)

    def test_тумблер_на_триал_больше_не_влияет(self):
        on = _quote(TRIAL, MONTHLY, keep_period=True, remaining_days=3.0)
        off = _quote(TRIAL, MONTHLY, keep_period=False, remaining_days=3.0)
        self.assertEqual(
            (on.net_cost_rub, on.new_expiry_ms, on.keeps_period),
            (off.net_cost_rub, off.new_expiry_ms, off.keeps_period),
        )

    def test_истёкший_триал_тоже_получает_полный_период(self):
        quote = _quote(TRIAL, MONTHLY, keep_period=True, remaining_days=0.0)
        self.assertFalse(quote.keeps_period)
        self.assertEqual(quote.new_expiry_ms, NOW_MS + 30 * DAY_MS)


class PaidSwitchUntouchedTests(unittest.TestCase):
    def test_смена_между_платными_тарифами_держит_срок(self):
        quote = _quote(MONTHLY, MONTHLY_PRO, keep_period=True, remaining_days=10.0)
        self.assertTrue(quote.keeps_period)
        self.assertEqual(quote.new_expiry_ms, NOW_MS + 10 * DAY_MS)

    def test_платная_смена_платит_только_за_остаток(self):
        quote = _quote(MONTHLY, MONTHLY_PRO, keep_period=True, remaining_days=10.0)
        self.assertEqual(quote.credit_rub, 100)
        self.assertEqual(quote.net_cost_rub, 100)

    def test_с_выключенным_тумблером_платная_смена_идёт_обычным_путём(self):
        quote = _quote(MONTHLY, MONTHLY_PRO, keep_period=False, remaining_days=10.0)
        self.assertFalse(quote.keeps_period)
        self.assertEqual(quote.new_expiry_ms, NOW_MS + 30 * DAY_MS)
        self.assertEqual(quote.net_cost_rub, 500)


if __name__ == "__main__":
    unittest.main()

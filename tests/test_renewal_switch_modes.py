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
SMALL = {
    "id": 20,
    "group_code": "basic",
    "duration_days": 30,
    "price_rub": 100,
    "traffic_limit": 100,
    "device_limit": 2,
}
BIG = {"id": 21, "group_code": "basic", "duration_days": 30, "price_rub": 300, "traffic_limit": 500, "device_limit": 5}
LONG = {"id": 22, "group_code": "basic", "duration_days": 90, "price_rub": 600, "traffic_limit": 500, "device_limit": 5}
FREE_PROMO = {
    "id": 23,
    "group_code": "gifts",
    "duration_days": 30,
    "price_rub": 0,
    "traffic_limit": 100,
    "device_limit": 2,
}

TARIFFS = {int(t["id"]): t for t in (TRIAL, SMALL, BIG, LONG, FREE_PROMO)}


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


def quote(current: dict, target: dict, *, remaining_days: float, keep_period=False, credit_as_days=False):
    saved = {key: MODES_CONFIG.get(key) for key in ("RENEWAL_SWITCH_KEEP_PERIOD", "RENEWAL_CREDIT_AS_DAYS")}
    MODES_CONFIG["RENEWAL_SWITCH_KEEP_PERIOD"] = keep_period
    MODES_CONFIG["RENEWAL_CREDIT_AS_DAYS"] = credit_as_days
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


def days_from_now(quote_obj) -> float:
    return round((quote_obj.new_expiry_ms - NOW_MS) / DAY_MS, 4)


class CreditToBalanceModeTests(unittest.TestCase):
    """Режим по умолчанию: остаток текущего тарифа уходит в зачёт, срок считается заново."""

    def test_апгрейд_платит_разницу(self):
        q = quote(SMALL, BIG, remaining_days=15)
        self.assertTrue(q.is_switch)
        self.assertFalse(q.keeps_period)
        self.assertEqual(q.credit_rub, 50)
        self.assertEqual(q.net_cost_rub, 250)
        self.assertEqual(q.refund_to_balance_rub, 0)
        self.assertEqual(days_from_now(q), 30)

    def test_зачёт_и_доплата_дают_полную_цену(self):
        q = quote(SMALL, BIG, remaining_days=15)
        self.assertEqual(q.credit_rub + q.net_cost_rub, q.new_full_price_rub)

    def test_даунгрейд_возвращает_разницу_на_баланс(self):
        q = quote(BIG, SMALL, remaining_days=15)
        self.assertEqual(q.credit_rub, 150)
        self.assertEqual(q.net_cost_rub, -50)
        self.assertEqual(q.refund_to_balance_rub, 50)

    def test_истёкший_ключ_зачитывать_нечего(self):
        q = quote(SMALL, BIG, remaining_days=0)
        self.assertEqual(q.credit_rub, 0)
        self.assertEqual(q.net_cost_rub, 300)
        self.assertEqual(days_from_now(q), 30)

    def test_другая_длительность_считается_по_своему_сроку(self):
        q = quote(SMALL, LONG, remaining_days=15)
        self.assertEqual(q.credit_rub, 50)
        self.assertEqual(q.net_cost_rub, 550)
        self.assertEqual(days_from_now(q), 90)


class CreditAsDaysModeTests(unittest.TestCase):
    """«Перерасчет дни»: остаток превращается в дни, цена платится полная."""

    def test_остаток_становится_днями(self):
        q = quote(SMALL, BIG, remaining_days=15, credit_as_days=True)
        self.assertEqual(q.credit_days, 5)
        self.assertEqual(q.credit_value_rub, 50)
        self.assertEqual(q.credit_rub, 0)
        self.assertEqual(q.net_cost_rub, 300)
        self.assertEqual(days_from_now(q), 35)

    def test_денежного_зачёта_в_этом_режиме_нет(self):
        q = quote(SMALL, BIG, remaining_days=15, credit_as_days=True)
        self.assertEqual(q.refund_to_balance_rub, 0)
        self.assertEqual(q.net_cost_rub, q.new_full_price_rub)

    def test_остатка_на_целый_день_не_хватило_вернулись_к_деньгам(self):
        q = quote(SMALL, BIG, remaining_days=1, credit_as_days=True)
        self.assertEqual(q.credit_days, 0)
        self.assertEqual(q.credit_rub, 3)
        self.assertEqual(q.net_cost_rub, 297)
        self.assertEqual(days_from_now(q), 30)

    def test_даунгрейд_даёт_много_дней(self):
        q = quote(BIG, SMALL, remaining_days=15, credit_as_days=True)
        self.assertEqual(q.credit_days, 45)
        self.assertEqual(q.net_cost_rub, 100)
        self.assertEqual(days_from_now(q), 75)


class KeepPeriodModeTests(unittest.TestCase):
    """«Смена: сохранять срок»: срок не двигается, платится только остаток периода."""

    def test_срок_не_двигается(self):
        q = quote(SMALL, BIG, remaining_days=15, keep_period=True)
        self.assertTrue(q.keeps_period)
        self.assertEqual(days_from_now(q), 15)

    def test_платится_остаток_нового_минус_зачёт(self):
        q = quote(SMALL, BIG, remaining_days=15, keep_period=True)
        self.assertEqual(q.credit_rub, 50)
        self.assertEqual(q.net_cost_rub, 100)

    def test_даунгрейд_возвращает_на_баланс(self):
        q = quote(BIG, SMALL, remaining_days=15, keep_period=True)
        self.assertEqual(q.credit_rub, 150)
        self.assertEqual(q.net_cost_rub, -100)
        self.assertEqual(q.refund_to_balance_rub, 100)
        self.assertEqual(days_from_now(q), 15)

    def test_истёкший_ключ_идёт_обычным_путём(self):
        q = quote(SMALL, BIG, remaining_days=0, keep_period=True)
        self.assertFalse(q.keeps_period)
        self.assertEqual(q.net_cost_rub, 300)
        self.assertEqual(days_from_now(q), 30)

    def test_триал_в_этом_режиме_не_участвует(self):
        q = quote(TRIAL, BIG, remaining_days=1, keep_period=True)
        self.assertFalse(q.keeps_period)
        self.assertEqual(q.net_cost_rub, 300)
        self.assertEqual(days_from_now(q), 30)


class ModePriorityTests(unittest.TestCase):
    def test_сохранение_срока_старше_режима_дней(self):
        q = quote(SMALL, BIG, remaining_days=15, keep_period=True, credit_as_days=True)
        self.assertTrue(q.keeps_period)
        self.assertEqual(q.credit_days, 0)
        self.assertEqual(q.net_cost_rub, 100)

    def test_на_триале_старшинство_уступает_режиму_дней(self):
        q = quote(TRIAL, BIG, remaining_days=1, keep_period=True, credit_as_days=True)
        self.assertFalse(q.keeps_period)
        self.assertEqual(q.net_cost_rub, 300)


class NotASwitchTests(unittest.TestCase):
    """Те же лимиты — это продление, а не смена: полная цена и срок в стек."""

    def test_продление_того_же_тарифа(self):
        q = quote(SMALL, SMALL, remaining_days=15)
        self.assertFalse(q.is_switch)
        self.assertEqual(q.credit_rub, 0)
        self.assertEqual(q.net_cost_rub, 100)
        self.assertEqual(days_from_now(q), 45)

    def test_режимы_на_продление_не_влияют(self):
        plain = quote(SMALL, SMALL, remaining_days=15)
        for kwargs in ({"keep_period": True}, {"credit_as_days": True}, {"keep_period": True, "credit_as_days": True}):
            q = quote(SMALL, SMALL, remaining_days=15, **kwargs)
            self.assertEqual(
                (q.is_switch, q.net_cost_rub, q.new_expiry_ms, q.keeps_period, q.credit_days),
                (plain.is_switch, plain.net_cost_rub, plain.new_expiry_ms, plain.keeps_period, plain.credit_days),
                kwargs,
            )

    def test_разная_длительность_при_тех_же_лимитах_всё_ещё_продление(self):
        q = quote(BIG, LONG, remaining_days=15)
        self.assertFalse(q.is_switch)
        self.assertEqual(q.net_cost_rub, 600)
        self.assertEqual(days_from_now(q), 105)


def screen_shown(quote_obj) -> bool:
    """Условие показа экрана смены из handlers/keys/renew/switch.py."""
    return not (
        not quote_obj.is_switch
        or (quote_obj.credit_rub <= 0 and quote_obj.credit_days <= 0 and not quote_obj.keeps_period)
    )


class SwitchScreenGateTests(unittest.TestCase):
    """Экран смены обязан появляться везде, где сделка отличается от обычного продления."""

    def test_гейт_в_коде_совпадает_с_проверяемым(self):
        from pathlib import Path

        source = (Path(__file__).resolve().parent.parent / "handlers" / "keys" / "renew" / "switch.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "if not quote.is_switch or (quote.credit_rub <= 0 and quote.credit_days <= 0 and not quote.keeps_period):",
            source,
        )

    def test_денежный_зачёт_показывается(self):
        self.assertTrue(screen_shown(quote(SMALL, BIG, remaining_days=15)))

    def test_зачёт_днями_показывается(self):
        self.assertTrue(screen_shown(quote(SMALL, BIG, remaining_days=15, credit_as_days=True)))

    def test_сохранение_срока_показывается_даже_без_зачёта(self):
        q = quote(FREE_PROMO, BIG, remaining_days=15, keep_period=True)
        self.assertEqual(q.credit_rub, 0)
        self.assertTrue(q.keeps_period)
        self.assertTrue(screen_shown(q), "иначе бот посчитает полную цену мимо режима")

    def test_обычное_продление_экрана_не_требует(self):
        self.assertFalse(screen_shown(quote(SMALL, SMALL, remaining_days=15)))

    def test_уход_с_триала_идёт_без_экрана_и_считается_как_обычная_смена(self):
        q = quote(TRIAL, BIG, remaining_days=1, keep_period=True)
        self.assertFalse(screen_shown(q))
        self.assertEqual(q.net_cost_rub, q.new_full_price_rub)
        self.assertEqual(days_from_now(q), 30)

    def test_пропуск_экрана_значит_обычная_сделка(self):
        for cur, new, rem, keep, days in InvariantTests.CASES:
            q = quote(cur, new, remaining_days=rem, keep_period=keep, credit_as_days=days)
            if screen_shown(q):
                continue
            self.assertEqual(q.net_cost_rub, q.new_full_price_rub, (cur["id"], new["id"], rem, keep, days))
            self.assertFalse(q.keeps_period, (cur["id"], new["id"], rem, keep, days))


class InvariantTests(unittest.TestCase):
    CASES = [
        (cur, new, rem, keep, days)
        for cur, new in ((SMALL, BIG), (BIG, SMALL), (SMALL, LONG), (TRIAL, BIG), (FREE_PROMO, BIG))
        for rem in (0, 1, 15, 29.5)
        for keep in (False, True)
        for days in (False, True)
    ]

    def test_возврат_всегда_зеркалит_отрицательную_доплату(self):
        for cur, new, rem, keep, days in self.CASES:
            q = quote(cur, new, remaining_days=rem, keep_period=keep, credit_as_days=days)
            self.assertEqual(q.refund_to_balance_rub, max(0, -q.net_cost_rub), (cur["id"], new["id"], rem, keep, days))

    def test_доплата_никогда_не_превышает_полную_цену(self):
        for cur, new, rem, keep, days in self.CASES:
            q = quote(cur, new, remaining_days=rem, keep_period=keep, credit_as_days=days)
            self.assertLessEqual(q.net_cost_rub, q.new_full_price_rub, (cur["id"], new["id"], rem, keep, days))

    def test_срок_всегда_в_будущем(self):
        for cur, new, rem, keep, days in self.CASES:
            q = quote(cur, new, remaining_days=rem, keep_period=keep, credit_as_days=days)
            self.assertGreater(q.new_expiry_ms, NOW_MS, (cur["id"], new["id"], rem, keep, days))

    def test_режим_дней_никогда_не_отдаёт_деньги(self):
        for cur, new, rem, _keep, _days in self.CASES:
            q = quote(cur, new, remaining_days=rem, credit_as_days=True)
            if q.credit_days > 0:
                self.assertEqual(q.credit_rub, 0, (cur["id"], new["id"], rem))
                self.assertEqual(q.net_cost_rub, q.new_full_price_rub, (cur["id"], new["id"], rem))

    def test_сохранение_срока_не_трогает_дату(self):
        for cur, new, rem, _keep, days in self.CASES:
            q = quote(cur, new, remaining_days=rem, keep_period=True, credit_as_days=days)
            if q.keeps_period:
                self.assertEqual(q.new_expiry_ms, NOW_MS + int(rem * DAY_MS), (cur["id"], new["id"], rem))


if __name__ == "__main__":
    unittest.main()

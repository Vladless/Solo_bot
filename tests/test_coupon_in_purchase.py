import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
FLOW = (ROOT / "handlers" / "payments" / "fast_payment_flow.py").read_text(encoding="utf-8")
PURCHASE = (ROOT / "handlers" / "tariffs" / "buy" / "purchase.py").read_text(encoding="utf-8")
UTILS = (ROOT / "handlers" / "payments" / "utils.py").read_text(encoding="utf-8")


def _apply_block() -> str:
    start = FLOW.index("async def fastflow_apply_coupon")
    return FLOW[start : FLOW.index("payment_config = await get_payment_providers_config()", start)]


class DiscountSurvivesTests(unittest.TestCase):
    """Списание пересчитывает цену заново — без кода купона скидка терялась."""

    def test_код_купона_кладётся_в_заявку(self):
        self.assertIn('temp_payload_updated["applied_coupon_code"] = code', _apply_block())

    def test_завершение_читает_этот_код(self):
        self.assertIn('coupon_code=data.get("applied_coupon_code")', UTILS)


class ZeroLeftFinishesTests(unittest.TestCase):
    def test_нулевая_доплата_закрывает_покупку(self):
        block = _apply_block()
        self.assertIn("if required_amount_new == 0:", block)
        self.assertIn("_finish_from_balance(message, session, str(temp_key), temp_payload_updated)", block)

    def test_завершение_идёт_через_общий_обработчик(self):
        helper = FLOW[FLOW.index("async def _finish_from_balance") :][:900]
        self.assertIn("from handlers.payments.utils import _handle_temp_state", helper)
        self.assertIn("_handle_temp_state(session, message.from_user.id, temp_key, payload, 0)", helper)

    def test_сбой_завершения_не_молчит(self):
        helper = FLOW[FLOW.index("async def _finish_from_balance") :][:900]
        self.assertIn("Не удалось завершить покупку", helper)


class CouponTypesTests(unittest.TestCase):
    """Купонов три вида: процент, на баланс и на дни."""

    def test_типы_различаются(self):
        block = _apply_block()
        self.assertIn('percent_raw = int(getattr(coupon, "percent", 0) or 0)', block)
        self.assertIn('amount_raw = int(getattr(coupon, "amount", 0) or 0)', block)
        self.assertIn('days_raw = int(getattr(coupon, "days", 0) or 0)', block)

    def test_купон_на_дни_объясняется_а_не_отвергается_молча(self):
        block = _apply_block()
        self.assertIn("days_coupon_text", block)
        self.assertIn("продлит активный ключ", FLOW)

    def test_купон_на_баланс_зачисляется(self):
        block = _apply_block()
        self.assertIn("from services.coupons import apply_fixed_coupon", block)
        self.assertIn("apply_fixed_coupon(session=session", block)

    def test_после_зачисления_недостача_пересчитывается(self):
        block = _apply_block()
        self.assertIn("balance_after = await get_balance(session, message.from_user.id)", block)
        self.assertIn("left = int(max(0, ceil(float(price_now) - float(balance_after))))", block)

    def test_если_после_зачисления_платить_нечем_покупка_закрывается(self):
        block = _apply_block()
        self.assertIn("if left == 0:", block)
        self.assertIn("_finish_from_balance(message, session, str(temp_key), payload_after)", block)

    def test_процентный_разбирается_после_остальных(self):
        block = _apply_block()
        self.assertLess(block.index("days_raw > 0"), block.index("apply_percent_coupon(int(base_price), coupon)"))


class OfferBeforeChargeTests(unittest.TestCase):
    """При достаточном балансе деньги списывались сразу — купон вводить было негде."""

    def test_экран_показывается_до_списания(self):
        body = PURCHASE[PURCHASE.index("balance = await get_balance(session, tg_id)") :]
        self.assertLess(body.index("_offer_coupon_before_charge"), body.index("CREATING_CONNECTION_MSG"))

    def test_экран_даёт_обе_кнопки(self):
        helper = PURCHASE[PURCHASE.index("async def _offer_coupon_before_charge") :]
        helper = helper[: helper.index("async def proceed_purchase_with_values")]
        self.assertIn('callback_data="buy_confirm_balance"', helper)
        self.assertIn('callback_data="fastflow_coupon"', helper)

    def test_экран_уважает_тумблер_купонов(self):
        helper = PURCHASE[PURCHASE.index("async def _offer_coupon_before_charge") :]
        self.assertIn('BUTTONS_CONFIG.get("COUPON_BUTTON_ENABLE", True)', helper)

    def test_заявка_кладётся_чтобы_купон_нашёл_сумму(self):
        helper = PURCHASE[PURCHASE.index("async def _offer_coupon_before_charge") :]
        self.assertIn('create_temporary_data(session, tg_id, "waiting_for_payment", payload)', helper)
        self.assertIn('state.update_data(temp_key="waiting_for_payment"', helper)

    def test_кнопка_оформления_обрабатывается(self):
        self.assertIn('@router.callback_query(F.data == "buy_confirm_balance")', FLOW)
        self.assertIn("async def buy_confirm_balance(", FLOW)


if __name__ == "__main__":
    unittest.main()

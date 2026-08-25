import re
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PAYMENTS = (ROOT / "handlers" / "payments" / "utils.py").read_text(encoding="utf-8")


def _load_guard():
    body = re.search(r"^def _addon_already_applied\(.*?\n    return checked\n", PAYMENTS, re.S | re.M)
    assert body, "не найдена проверка повторного применения"
    namespace: dict = {}
    exec(body.group(0), namespace)
    return namespace["_addon_already_applied"]


applied = _load_guard()


class AlreadyAppliedTests(unittest.TestCase):
    def test_лимиты_совпали_значит_докупка_уже_прошла(self):
        record = {"current_device_limit": 5, "current_traffic_limit": 1500}
        self.assertTrue(applied(record, 5, 1500))

    def test_трафик_ещё_не_вырос_значит_применяем(self):
        record = {"current_device_limit": 5, "current_traffic_limit": 1000}
        self.assertFalse(applied(record, 5, 1500))

    def test_устройства_ещё_не_выросли_значит_применяем(self):
        record = {"current_device_limit": 3, "current_traffic_limit": 1500}
        self.assertFalse(applied(record, 5, 1500))

    def test_только_трафик_в_докупке(self):
        record = {"current_device_limit": 3, "current_traffic_limit": 1500}
        self.assertTrue(applied(record, None, 1500))
        self.assertFalse(applied(record, None, 2000))

    def test_пустой_ключ_не_считается_применённым(self):
        record = {"current_device_limit": None, "current_traffic_limit": None}
        self.assertFalse(applied(record, 5, 1500))

    def test_без_целей_ничего_не_утверждаем(self):
        record = {"current_device_limit": 5, "current_traffic_limit": 1500}
        self.assertFalse(applied(record, None, None), "без целевых лимитов списание не отменяем")

    def test_строковые_значения_из_базы(self):
        record = {"current_device_limit": "5", "current_traffic_limit": "1500"}
        self.assertTrue(applied(record, 5, 1500))


class GuardPlacementTests(unittest.TestCase):
    def _branch(self) -> str:
        start = PAYMENTS.index('if state == "waiting_for_addons_payment":')
        end = PAYMENTS.index("[ADDONS] Ошибка при применении расширения", start)
        return PAYMENTS[start:end]

    def test_проверка_стоит_до_списания(self):
        branch = self._branch()
        guard = branch.index("_addon_already_applied(record,")
        charge = branch.index("update_balance(session, user_id, -extra_price_to_charge)")
        self.assertLess(guard, charge, "списание должно быть отсечено проверкой")

    def test_проверка_стоит_до_обращения_к_панели(self):
        branch = self._branch()
        guard = branch.index("_addon_already_applied(record,")
        panel = branch.index("await renew_key_in_cluster(")
        self.assertLess(guard, panel, "незачем дёргать панель ради уже применённой докупки")

    def test_сверяем_итоговые_лимиты_а_не_размер_пакета(self):
        self.assertIn("_addon_already_applied(record, hwid_device_limit_to_set, total_gb)", PAYMENTS)

    def test_повтор_очищает_заявку_и_не_роняет_платёж(self):
        branch = self._branch()
        tail = branch[branch.index("_addon_already_applied(record,"):]
        self.assertIn("await clear_temporary_data(session, user_id)", tail)
        self.assertIn("return True", tail[: tail.index("update_balance")])

    def test_повтор_попадает_в_журнал(self):
        self.assertIn("повторное списание отменено", PAYMENTS)


if __name__ == "__main__":
    unittest.main()

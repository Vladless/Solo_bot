import re
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCREEN = (ROOT / "handlers" / "tariffs" / "addons" / "pack_mode" / "screen.py").read_text(encoding="utf-8")
ROUTER = (ROOT / "handlers" / "tariffs" / "addons" / "pack_mode" / "router.py").read_text(encoding="utf-8")


def _load_price_helpers():
    """services.addons тянет за собой bootstrap и упирается в круговой импорт,
    поэтому берём чистые функции расчёта прямо из файла."""
    source = (ROOT / "services" / "addons.py").read_text(encoding="utf-8")
    config = (ROOT / "core" / "settings" / "tariffs_config.py").read_text(encoding="utf-8")
    namespace: dict = {"ceil": __import__("math").ceil, "Any": object}
    exec(re.search(r"^def get_override_value\(.*?(?=^def )", config, re.S | re.M).group(0), namespace)
    for name in ("calc_pack_devices_price_rub", "calc_pack_traffic_price_rub",
                 "calc_pack_full_price_rub"):
        match = re.search(rf"^def {name}\(.*?(?=^def |^@|^class )", source, re.S | re.M)
        assert match, f"не найдена функция {name}"
        exec(match.group(0), namespace)
    return namespace["calc_pack_full_price_rub"]


calc_pack_full_price_rub = _load_price_helpers()

TARIFF = {
    "device_options": [1, 2, 3, 4, 5],
    "traffic_options_gb": [500, 1000, 1500, 2000, 2500],
    "device_step_rub": 100,
    "traffic_overrides": {"500": 200},
}


class PriceWithoutSecondPackTests(unittest.TestCase):
    def test_невыбранный_трафик_не_стоит_денег(self):
        цена = calc_pack_full_price_rub(
            tariff=TARIFF,
            has_device_option=True,
            has_traffic_option=True,
            selected_devices=5,
            selected_traffic_gb=None,
        )
        self.assertEqual(цена, 500, "в цену попал пакет трафика, который не выбирали")

    def test_невыбранные_устройства_не_стоят_денег(self):
        цена = calc_pack_full_price_rub(
            tariff=TARIFF,
            has_device_option=True,
            has_traffic_option=True,
            selected_devices=None,
            selected_traffic_gb=500,
        )
        self.assertEqual(цена, 200)

    def test_пустой_выбор_ничего_не_стоит(self):
        цена = calc_pack_full_price_rub(
            tariff=TARIFF,
            has_device_option=True,
            has_traffic_option=True,
            selected_devices=None,
            selected_traffic_gb=None,
        )
        self.assertEqual(цена, 0)


class NoForcedPreselectionTests(unittest.TestCase):
    def test_экран_не_подставляет_первый_вариант(self):
        self.assertNotIn("selected_devices = device_int_options[0]", SCREEN)
        self.assertNotIn("selected_traffic_gb = traffic_int_options[0]", SCREEN)

    def test_стартовое_состояние_осталось_пустым(self):
        self.assertIn("addon_selected_device_limit=None", ROUTER)
        self.assertIn("addon_selected_traffic_gb=None", ROUTER)

    def test_экран_по_прежнему_умеет_пустой_выбор(self):
        self.assertIn("has_device_pack_selected = has_device_option and selected_devices is not None", SCREEN)
        self.assertIn("has_traffic_pack_selected = has_traffic_option and selected_traffic_gb is not None", SCREEN)


class DeselectTests(unittest.TestCase):
    def test_повторное_нажатие_снимает_устройства(self):
        self.assertIn(
            "await state.update_data(addon_selected_device_limit=None if repeated else new_devices)",
            ROUTER,
        )

    def test_повторное_нажатие_снимает_трафик(self):
        self.assertIn(
            "await state.update_data(addon_selected_traffic_gb=None if repeated else new_traffic_gb)",
            ROUTER,
        )

    def test_повторное_нажатие_больше_не_игнорируется(self):
        self.assertNotIn("if selected_devices is not None and int(selected_devices) == new_devices:", ROUTER)
        self.assertNotIn("if selected_traffic_gb is not None and int(selected_traffic_gb) == new_traffic_gb:", ROUTER)


class EmptySelectionGuardTests(unittest.TestCase):
    def test_кнопка_оплаты_скрыта_при_пустом_выборе(self):
        self.assertIn("if has_device_pack_selected or has_traffic_pack_selected:", SCREEN)
        кнопка = SCREEN.index('callback_data="key_addons_confirm"')
        условие = SCREEN.index("if has_device_pack_selected or has_traffic_pack_selected:")
        self.assertLess(условие, кнопка, "условие должно охватывать кнопку оплаты")

    def test_пустой_выбор_не_доходит_до_оплаты(self):
        self.assertIn("if selected_devices is None and selected_traffic_gb is None:", ROUTER)
        проверка = ROUTER.index("if selected_devices is None and selected_traffic_gb is None:")
        расчёт = ROUTER.index("diff_full = calc_pack_full_price_rub(")
        self.assertLess(проверка, расчёт, "отсечение должно стоять до расчёта цены")


if __name__ == "__main__":
    unittest.main()

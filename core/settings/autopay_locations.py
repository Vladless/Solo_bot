from typing import Final


AUTOPAY_MANAGEMENT_LOCATIONS: Final[dict[str, frozenset[str]]] = {
    "profile": frozenset({"profile"}),
    "pay_menu": frozenset({"pay_menu"}),
    "both": frozenset({"profile", "pay_menu"}),
    "subscription": frozenset({"subscription"}),
    "profile_subscription": frozenset({"profile", "subscription"}),
    "pay_menu_subscription": frozenset({"pay_menu", "subscription"}),
    "all": frozenset({"profile", "pay_menu", "subscription"}),
}

AUTOPAY_MANAGEMENT_OPTIONS: Final[list[dict[str, str]]] = [
    {"value": "profile", "label": "В профиле"},
    {"value": "pay_menu", "label": "В меню оплаты"},
    {"value": "both", "label": "В профиле и меню оплаты"},
    {"value": "subscription", "label": "В «Моя подписка»"},
    {"value": "profile_subscription", "label": "В профиле и «Моя подписка»"},
    {"value": "pay_menu_subscription", "label": "В меню оплаты и «Моя подписка»"},
    {"value": "all", "label": "В профиле, меню оплаты и «Моя подписка»"},
]

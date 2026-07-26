from typing import Any

from hooks.hooks import run_hooks


PROVIDERS_BASE: dict[str, dict[str, Any]] = {
    "YOOKASSA": {
        "currency": "RUB",
        "value": "pay_yookassa",
        "fast": "process_custom_amount_input",
        "order": 1,
    },
    "YOOMONEY": {
        "currency": "RUB",
        "value": "pay_yoomoney",
        "fast": "process_custom_amount_input_yoomoney",
        "order": 2,
    },
    "ROBOKASSA": {
        "currency": "RUB",
        "value": "pay_robokassa",
        "fast": "handle_custom_amount_input",
        "order": 3,
    },
    "KASSAI_CARDS": {
        "currency": "RUB",
        "value": "pay_kassai_cards",
        "fast": "handle_custom_amount_input_kassai_cards",
        "module": "kassai",
        "order": 4,
    },
    "KASSAI_SBP": {
        "currency": "RUB",
        "value": "pay_kassai_sbp",
        "fast": "handle_custom_amount_input_kassai_sbp",
        "module": "kassai",
        "order": 5,
    },
    "WATA_RU": {
        "currency": "RUB",
        "value": "pay_wata_ru",
        "fast": "handle_custom_amount_input_wata_ru",
        "module": "wata",
        "order": 6,
    },
    "PARITYPAY_SBP": {
        "currency": "RUB",
        "value": "pay_paritypay_sbp",
        "fast": "handle_custom_amount_input_paritypay_sbp",
        "module": "paritypay",
        "order": 7,
    },
    "PLATEGA_SBP": {
        "currency": "RUB",
        "value": "pay_platega_sbp",
        "fast": "handle_custom_amount_input_platega_sbp",
        "module": "platega",
        "order": 8,
    },
    "PLATEGA_CARDS": {
        "currency": "RUB",
        "value": "pay_platega_cards",
        "fast": "handle_custom_amount_input_platega_cards",
        "module": "platega",
        "order": 9,
    },
    "OVERPAY_CARDS": {
        "currency": "RUB",
        "value": "pay_overpay_cards",
        "fast": "handle_custom_amount_input_overpay_cards",
        "module": "overpay",
        "order": 10,
    },
    "OVERPAY_SBP": {
        "currency": "RUB",
        "value": "pay_overpay_sbp",
        "fast": "handle_custom_amount_input_overpay_sbp",
        "module": "overpay",
        "order": 11,
    },
    "TRIBUTE": {
        "currency": "RUB+USD",
        "value": "pay_tribute",
        "fast": None,
        "order": 12,
    },
    "HELEKET": {
        "currency": "USD",
        "value": "pay_heleket_crypto",
        "fast": "handle_custom_amount_input_heleket",
        "order": 13,
    },
    "WATA_INT": {
        "currency": "USD",
        "value": "pay_wata_int",
        "fast": "handle_custom_amount_input_wata_int",
        "module": "wata",
        "order": 14,
    },
    "PLATEGA_INT": {
        "currency": "USD",
        "value": "pay_platega_int",
        "fast": "handle_custom_amount_input_platega_int",
        "module": "platega",
        "order": 15,
    },
    "PLATEGA_CRYPTO": {
        "currency": "USD",
        "value": "pay_platega_crypto",
        "fast": "handle_custom_amount_input_platega_crypto",
        "module": "platega",
        "order": 16,
    },
    "CRYPTOBOT": {
        "currency": "USD",
        "value": "pay_cryptobot",
        "fast": "process_custom_amount_input",
        "order": 17,
    },
    "FREEKASSA": {
        "currency": "USD",
        "value": "pay_freekassa",
        "fast": None,
        "order": 18,
    },
    "STARS": {
        "currency": "STARS",
        "value": "pay_stars",
        "fast": "process_custom_amount_input_stars",
        "order": 19,
    },
}

WEB_LINK_PROVIDER_IDS = (
    "YOOKASSA",
    "YOOMONEY",
    "ROBOKASSA",
    "KASSAI_CARDS",
    "KASSAI_SBP",
    "WATA_RU",
    "WATA_INT",
    "PARITYPAY_SBP",
    "PLATEGA_SBP",
    "PLATEGA_CARDS",
    "PLATEGA_INT",
    "PLATEGA_CRYPTO",
    "OVERPAY_CARDS",
    "OVERPAY_SBP",
    "HELEKET",
    "FREEKASSA",
    "CRYPTOBOT",
)

TELEGRAM_ONLY_PROVIDER_IDS = (
    "TRIBUTE",
    "STARS",
)

_MODULE_WEB_LINK_PROVIDER_IDS: list[str] = []


def register_web_link_provider(provider_id: str) -> None:
    key = (provider_id or "").strip().upper()
    if not key or key in WEB_LINK_PROVIDER_IDS or key in _MODULE_WEB_LINK_PROVIDER_IDS:
        return
    _MODULE_WEB_LINK_PROVIDER_IDS.append(key)


def get_web_link_provider_ids() -> tuple[str, ...]:
    return tuple(WEB_LINK_PROVIDER_IDS) + tuple(_MODULE_WEB_LINK_PROVIDER_IDS)


def _get_effective_order(name: str, cfg: dict[str, Any]) -> int:
    """Возвращает эффективный порядок провайдера (админ > модуль > дефолт)."""
    from core.settings.providers_order_config import PROVIDERS_ORDER

    if name in PROVIDERS_ORDER:
        return PROVIDERS_ORDER[name]
    return cfg.get("order", 999)


def _sort_providers(providers: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Сортирует провайдеров по полю 'order' (меньше = выше)."""
    return dict(sorted(providers.items(), key=lambda item: _get_effective_order(item[0], item[1])))


def sort_provider_names(names: list[str], providers_map: dict[str, dict[str, Any]]) -> list[str]:
    """Сортирует список имён провайдеров по их 'order' из providers_map."""
    return sorted(names, key=lambda n: _get_effective_order(n, providers_map.get(n) or {}))


def get_providers(flags: dict[str, bool]) -> dict[str, dict[str, Any]]:
    providers: dict[str, dict[str, Any]] = {}
    for name, base in PROVIDERS_BASE.items():
        cfg = dict(base)
        cfg["enabled"] = bool(flags.get(name))
        providers[name] = cfg
    return providers


async def get_providers_with_hooks(flags: dict[str, bool]) -> dict[str, dict[str, Any]]:
    providers = get_providers(flags)
    results = await run_hooks("providers_config", providers=providers, flags=flags)
    for result in results:
        if not isinstance(result, dict):
            continue
        for name, patch in result.items():
            if patch is None:
                providers.pop(name, None)
            elif isinstance(patch, dict):
                base = dict(providers.get(name, {}))
                base.update(patch)
                providers[name] = base
    return _sort_providers(providers)

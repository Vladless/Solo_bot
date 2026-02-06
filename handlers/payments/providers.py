from typing import Any

from hooks.hooks import run_hooks

PROVIDERS_BASE: dict[str, dict[str, Any]] = {
    "YOOKASSA": {
        "currency": "RUB",
        "value": "pay_yookassa",
        "fast": "process_custom_amount_input",
        "order": 10,
    },
    "YOOMONEY": {
        "currency": "RUB",
        "value": "pay_yoomoney",
        "fast": "process_custom_amount_input_yoomoney",
        "order": 20,
    },
    "ROBOKASSA": {
        "currency": "RUB",
        "value": "pay_robokassa",
        "fast": "handle_custom_amount_input",
        "order": 30,
    },
    "KASSAI_CARDS": {
        "currency": "RUB",
        "value": "pay_kassai_cards",
        "fast": "handle_custom_amount_input_kassai_cards",
        "module": "kassai",
        "order": 40,
    },
    "KASSAI_SBP": {
        "currency": "RUB",
        "value": "pay_kassai_sbp",
        "fast": "handle_custom_amount_input_kassai_sbp",
        "module": "kassai",
        "order": 50,
    },
    "TRIBUTE": {
        "currency": "RUB+USD",
        "value": "pay_tribute",
        "fast": None,
        "order": 60,
    },
    "HELEKET": {
        "currency": "USD",
        "value": "pay_heleket_crypto",
        "fast": "handle_custom_amount_input_heleket",
        "order": 70,
    },
    "CRYPTOBOT": {
        "currency": "USD",
        "value": "pay_cryptobot",
        "fast": "process_custom_amount_input",
        "order": 80,
    },
    "FREEKASSA": {
        "currency": "USD",
        "value": "pay_freekassa",
        "fast": None,
        "order": 90,
    },
    "STARS": {
        "currency": "STARS",
        "value": "pay_stars",
        "fast": "process_custom_amount_input_stars",
        "order": 100,
    },
}


def _get_effective_order(name: str, cfg: dict[str, Any]) -> int:
    """Возвращает эффективный порядок провайдера (админ > модуль > дефолт)."""
    from core.settings.providers_order_config import PROVIDERS_ORDER

    if name in PROVIDERS_ORDER:
        return PROVIDERS_ORDER[name]
    return cfg.get("order", 999)


def _sort_providers(providers: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Сортирует провайдеров по полю 'order' (меньше = выше)."""
    return dict(
        sorted(providers.items(), key=lambda item: _get_effective_order(item[0], item[1]))
    )


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

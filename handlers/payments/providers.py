from typing import Any

from hooks.hooks import run_hooks


PROVIDERS_BASE: dict[str, dict[str, Any]] = {
    "YOOKASSA": {
        "currency": "RUB",
        "value": "pay_yookassa",
        "fast": "process_custom_amount_input",
    },
    "YOOMONEY": {
        "currency": "RUB",
        "value": "pay_yoomoney",
        "fast": "process_custom_amount_input_yoomoney",
    },
    "ROBOKASSA": {
        "currency": "RUB",
        "value": "pay_robokassa",
        "fast": "handle_custom_amount_input",
    },
    "KASSAI_CARDS": {
        "currency": "RUB",
        "value": "pay_kassai_cards",
        "fast": "handle_custom_amount_input_kassai_cards",
        "module": "kassai",
    },
    "KASSAI_SBP": {
        "currency": "RUB",
        "value": "pay_kassai_sbp",
        "fast": "handle_custom_amount_input_kassai_sbp",
        "module": "kassai",
    },
    "TRIBUTE": {
        "currency": "RUB+USD",
        "value": "pay_tribute",
        "fast": None,
    },
    "HELEKET": {
        "currency": "USD",
        "value": "pay_heleket_crypto",
        "fast": "handle_custom_amount_input_heleket",
    },
    "CRYPTOBOT": {
        "currency": "USD",
        "value": "pay_cryptobot",
        "fast": "process_custom_amount_input",
    },
    "FREEKASSA": {
        "currency": "USD",
        "value": "pay_freekassa",
        "fast": None,
    },
    "STARS": {
        "currency": "STARS",
        "value": "pay_stars",
        "fast": "process_custom_amount_input_stars",
    },
}


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
    return providers

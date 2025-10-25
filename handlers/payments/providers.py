from typing import Dict
from hooks.hooks import run_hooks

PROVIDERS_BASE: Dict[str, dict] = {
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
    "WATA_RU": {
        "currency": "RUB",
        "value": "pay_wata_ru",
        "fast": None,
    },
    "WATA_SBP": {
        "currency": "RUB",
        "value": "pay_wata_sbp",
        "fast": None,
    },
    "TRIBUTE": {
        "currency": "RUB",
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
    "WATA_INT": {
        "currency": "USD",
        "value": "pay_wata_int",
        "fast": None,
    },
    "STARS": {
        "currency": "STARS",
        "value": "pay_stars",
        "fast": "process_custom_amount_input_stars",
    },
}

def get_providers(flags: Dict[str, bool]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for k, base in PROVIDERS_BASE.items():
        cfg = dict(base)
        cfg["enabled"] = bool(flags.get(k))
        out[k] = cfg
    return out

async def get_providers_with_hooks(flags: Dict[str, bool]) -> Dict[str, dict]:
    out = get_providers(flags)
    results = await run_hooks("providers_config", providers=out, flags=flags)
    for r in results:
        if not isinstance(r, dict):
            continue
        for name, patch in r.items():
            if patch is None:
                out.pop(name, None)
            elif isinstance(patch, dict):
                base = dict(out.get(name, {}))
                base.update(patch)
                out[name] = base
    return out

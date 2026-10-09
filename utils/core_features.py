CORE_REPLACED_MODULES = frozenset({"yookassa_autopay", "2328_modules", "kassa2328"})


def is_core_replaced_module(name: str) -> bool:
    return str(name or "").strip() in CORE_REPLACED_MODULES

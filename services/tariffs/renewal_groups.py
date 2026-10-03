from collections.abc import Iterable


DEFAULT_RENEWAL_FORBIDDEN_GROUPS = frozenset({
    "trial",
    "discounts",
    "discounts_max",
    "cold_discounts",
    "cold_discounts_max",
    "gifts",
})


def resolve_renewal_tariff_group(
    key_tariff_group: object,
    server_tariff_group: object,
    *,
    forbidden_groups: Iterable[object] = DEFAULT_RENEWAL_FORBIDDEN_GROUPS,
) -> str:
    """Определяет группу тарифов для продления ключа."""
    key_group = str(key_tariff_group or "").strip()
    server_group = str(server_tariff_group or "").strip()
    forbidden = {str(group or "").strip() for group in forbidden_groups}

    if key_group and key_group not in forbidden:
        return key_group
    return server_group

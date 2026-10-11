from collections.abc import Iterable, Mapping


DEFAULT_RENEWAL_FORBIDDEN_GROUPS = frozenset({
    "trial",
    "discounts",
    "discounts_max",
    "cold_discounts",
    "cold_discounts_max",
    "gifts",
})


def is_same_renewal_family(old_tariff: Mapping[str, object] | None, new_tariff: Mapping[str, object] | None) -> bool:
    """Сравнивает группу и подгруппу тарифов для продления."""
    if not old_tariff or not new_tariff:
        return False
    return all(
        str(old_tariff.get(field) or "").strip() == str(new_tariff.get(field) or "").strip()
        for field in ("group_code", "subgroup_title")
    )


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

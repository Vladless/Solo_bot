_SPECIAL_GROUP_CODES = frozenset({
    "trial",
    "discounts",
    "discounts_max",
    "cold_discounts",
    "cold_discounts_max",
    "gifts",
})


def _tariff_id(value: object) -> int | None:
    """Возвращает корректный ID тарифа."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def _has_tariff_binding(server: dict, tariff_id: int) -> bool:
    """Проверяет прямую привязку сервера к тарифу."""
    bindings = server.get("tariff_ids")
    if not isinstance(bindings, list | tuple | set | frozenset):
        return False
    return any(_tariff_id(value) == tariff_id for value in bindings)


def _has_legacy_binding(server: dict, subgroup: str) -> bool:
    """Проверяет старую привязку без прямых тарифов."""
    tariffs = server.get("tariff_ids")
    if tariffs is not None and (not isinstance(tariffs, list | tuple | set | frozenset) or tariffs):
        return False
    subgroups = server.get("tariff_subgroups")
    return isinstance(subgroups, list | tuple | set | frozenset) and subgroup in subgroups


def is_server_configured(server: dict) -> bool:
    """Проверяет обязательные параметры панели и инбаунда."""
    api_url = server.get("api_url")
    if not isinstance(api_url, str) or not api_url.strip():
        return False
    panel_type = str(server.get("panel_type") or "").lower()
    inbound_id = server.get("inbound_id")
    if panel_type == "remnawave":
        return isinstance(inbound_id, str) and bool(inbound_id.strip())
    if panel_type == "3x-ui":
        return _tariff_id(inbound_id) is not None
    return False


def resolve_tariff_servers(
    cluster_servers: list[dict],
    tariff: dict | None,
    *,
    binding_scope: list[dict] | None = None,
    selected_server: str | None = None,
    is_trial: bool = False,
) -> list[dict]:
    """Возвращает только серверы с явным разрешением тарифа."""
    if not tariff:
        return []
    tariff_id = _tariff_id(tariff.get("id"))
    if tariff_id is None:
        return []

    group_code = str(tariff.get("group_code") or "").lower()
    special = "trial" if is_trial else group_code if group_code in _SPECIAL_GROUP_CODES else None
    scope = cluster_servers if binding_scope is None else binding_scope
    has_direct_binding = any(_has_tariff_binding(server, tariff_id) for server in scope)
    if has_direct_binding:
        allowed = [server for server in cluster_servers if _has_tariff_binding(server, tariff_id)]
    else:
        subgroup = tariff.get("subgroup_title")
        if isinstance(subgroup, str) and subgroup:
            allowed = [server for server in cluster_servers if _has_legacy_binding(server, subgroup)]
        elif special and (subgroup is None or subgroup == ""):
            allowed = cluster_servers
        else:
            return []

    allowed = [server for server in allowed if server.get("enabled") is True]
    if special:
        allowed = [
            server
            for server in allowed
            if isinstance(server.get("special_groups"), list | tuple | set | frozenset)
            and special in server["special_groups"]
        ]
    if selected_server is not None:
        allowed = [server for server in allowed if server.get("server_name") == selected_server]
    return allowed
